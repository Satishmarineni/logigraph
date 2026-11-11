"""Step 4.4: Link the four document types, resolve entities, validate against
the ontology, and write graph-ready facts.

Techniques:
  1. Reference matching - documents of one shipment carry different IDs:
        HAWB: hawb_number + PO      SLI: shipment_id + PO
        214 : hawb_number + shipment_id + PO      Status report: hawb_number
     so HAWB and SLI are joined through the PO number, and the 214 confirms it.
  2. Entity resolution - company names are normalized (case, punctuation,
     legal suffixes) so "Acme Inc." and "ACME INC" become one Company node.
  3. Conflict detection - when two documents disagree (e.g. declared value),
     the fact is flagged instead of silently picking one.
  4. Ontology validation - every node and relationship goes through
     check_node / check_relationship; anything that fails goes to review.
  5. Provenance - every node and relationship lists the documents it came from.
"""
import json
import re
from pathlib import Path

from logigraph.ontology.relationships import OntologyError, check_node, check_relationship
from logigraph.ontology.schema import CLASSES

EXTRACTED = Path("data/processed/extracted")
OUT_PATH = Path("data/processed/facts/graph_facts.json")
REVIEW_PATH = Path("data/processed/review/fact_review.jsonl")
CONFLICTS_PATH = Path("data/processed/review/conflicts.jsonl")
TRUTH_PATH = Path("data/processed/synthetic/shipments.jsonl")

LEGAL_SUFFIXES = {"inc", "llc", "ltd", "plc", "gmbh", "corp", "co", "group", "ag", "sa",
                  "bv", "pty", "kk", "pvt", "ltda", "synthetic"}


def load(name):
    path = EXTRACTED / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------
def normalize_name(name):
    """'Garcia, Deleon and Henson (SYNTHETIC)' -> 'garcia deleon and henson'"""
    words = re.sub(r"[^\w\s]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in LEGAL_SUFFIXES)


def company_id(party):
    """Same normalized name in the same city = same company."""
    return f"{normalize_name(party['name'])}|{(party.get('city') or '').lower()}"


# ---------------------------------------------------------------------------
# A small in-memory fact store that merges duplicates and keeps provenance
# ---------------------------------------------------------------------------
class FactStore:
    def __init__(self):
        self.nodes = {}           # (class, key) -> {"class", "props", "sources"}
        self.rels = {}            # (type, from, to) -> {"type", "from", "to", "sources"}
        self.review = []
        self.conflicts = []

    def node(self, cls, props, source):
        props = {k: v for k, v in props.items() if v is not None}
        try:
            check_node(cls, props)
        except OntologyError as e:
            self.review.append({"kind": "node", "class": cls, "props": props,
                                "source": source, "reason": str(e)})
            return None
        key = props[CLASSES[cls]["key"]]
        existing = self.nodes.get((cls, key))
        if existing is None:
            self.nodes[(cls, key)] = {"class": cls, "props": props, "sources": [source]}
        else:
            for prop, value in props.items():
                old = existing["props"].get(prop)
                if old is None:
                    existing["props"][prop] = value
                elif old != value:
                    self.conflicts.append({"class": cls, "key": key, "property": prop,
                                           "values": [old, value], "sources": existing["sources"] + [source]})
            if source not in existing["sources"]:
                existing["sources"].append(source)
        return (cls, key)

    def rel(self, rel_type, from_node, to_node, source, **props):
        if from_node is None or to_node is None:
            return
        try:
            check_relationship(rel_type, from_node[0], to_node[0])
        except OntologyError as e:
            self.review.append({"kind": "relationship", "type": rel_type, "from": from_node,
                                "to": to_node, "source": source, "reason": str(e)})
            return
        k = (rel_type, from_node, to_node)
        entry = self.rels.setdefault(k, {"type": rel_type, "from": list(from_node),
                                         "to": list(to_node), "props": props, "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)


def location_kind(code):
    return "seaport" if len(code) == 5 else "airport"


# ---------------------------------------------------------------------------
# Building facts for one shipment
# ---------------------------------------------------------------------------
def build(store, hawb, sli, edi, status):
    hawb_no = hawb["references"]["hawb_number"]
    h, s = hawb["fields"], (sli or {}).get("fields", {})
    e = (edi or {}).get("fields", {})
    src_h = {"doc_id": hawb["doc_id"], "doc_type": "HAWB"}
    src_s = {"doc_id": sli["doc_id"], "doc_type": "SLI"} if sli else None
    src_e = {"doc_id": edi["doc_id"], "doc_type": "EDI214"} if edi else None
    src_r = {"doc_id": status["doc_id"], "doc_type": "STATUS_REPORT",
             "evidence": status["evidence"]["quote"]} if status else None

    # Cross-document checks: flag disagreements
    if sli and h["declared_value_usd"] != s["declared_value_usd"]:
        store.conflicts.append({"class": "Shipment", "key": hawb_no, "property": "declared_value_usd",
                                "values": [h["declared_value_usd"], s["declared_value_usd"]],
                                "sources": [src_h, src_s]})
    if sli and company_id(h["shipper"]) != company_id(s["usppi"]):
        store.conflicts.append({"class": "Shipment", "key": hawb_no, "property": "shipper vs USPPI",
                                "values": [h["shipper"]["name"], s["usppi"]["name"]],
                                "sources": [src_h, src_s]})

    # Shipment: each property from the document that owns it
    shipment = store.node("Shipment", {
        "hawb_number": hawb_no,
        "shipment_id": (sli or {}).get("references", {}).get("shipment_id")
                       or (edi or {}).get("references", {}).get("shipment_id"),
        "po_number": hawb["references"]["po_number"],
        "mode": h["mode"],
        "ship_date": h["ship_date"],
        "payment_terms": h["payment_terms"],
        "declared_value_usd": h["declared_value_usd"],
        "insurance_usd": h["insurance_usd"],
        "incoterm": s.get("incoterm"),
        "routed_export": s.get("routed_export"),
        "scheduled_delivery": e.get("estimated_delivery"),
        "actual_delivery": e.get("actual_delivery"),
        "delay_days": e.get("delay_days"),
        "delay_reason": status["fields"]["delay_reason"] if status else None,
    }, src_h)
    for src in (src_s, src_e, src_r):
        if src and shipment:
            store.nodes[shipment]["sources"].append(src)

    # Parties
    shipper = store.node("Company", {"company_id": company_id(h["shipper"]), "name": h["shipper"]["name"],
                                     "address": h["shipper"].get("address"), "city": h["shipper"].get("city"),
                                     "state_or_country": h["shipper"].get("state_or_country"),
                                     "account_number": h["shipper"].get("account_number")}, src_h)
    consignee = store.node("Company", {"company_id": company_id(h["consignee"]), "name": h["consignee"]["name"],
                                       "address": h["consignee"].get("address"), "city": h["consignee"].get("city"),
                                       "state_or_country": h["consignee"].get("state_or_country"),
                                       "account_number": h["consignee"].get("account_number")}, src_h)
    store.rel("SHIPPED_BY", shipment, shipper, src_h)
    store.rel("CONSIGNED_TO", shipment, consignee, src_h)
    if sli:
        store.rel("SHIPPED_BY", shipment, shipper, src_s)          # USPPI confirms the shipper
    if edi:
        store.rel("SHIPPED_BY", shipment, shipper, src_e)          # N1*SH confirms it too

    # Route - origin is always the US (export shipments); the destination country comes from the SLI.
    route = (("ORIGINATES_AT", h["origin_code"], "United States"),
             ("DESTINED_FOR", h["destination_code"], s.get("destination_country")))
    for rel_type, code, country in route:
        loc = store.node("Location", {"code": code, "kind": location_kind(code), "country": country}, src_h)
        store.rel(rel_type, shipment, loc, src_h)

    # Service and dangerous goods
    if h["service_level"]:
        store.rel("USES_SERVICE", shipment, store.node("ServiceLevel", {"code": h["service_level"]}, src_h), src_h)
    if h["dangerous_goods_un"]:
        dg = store.node("DangerousGoods", {"un_number": h["dangerous_goods_un"]}, src_h)
        store.rel("CONTAINS_DG", shipment, dg, src_h)

    # Line items and commodities (SLI adds ECCN, license and value per Schedule B)
    sli_by_code = {c["schedule_b"]: c for c in (sli or {}).get("line_items", [])}
    for item in hawb["line_items"]:
        extra = sli_by_code.get(item["schedule_b"], {})
        line = store.node("LineItem", {"line_id": f"{hawb_no}-{item['row']}", "pieces": item["pieces"],
                                       "gross_weight_kg": item["gross_weight_kg"],
                                       "dimensions_in": item["dimensions_in"],
                                       "value_usd": extra.get("value_usd")}, src_h)
        commodity = store.node("Commodity", {"schedule_b": item["schedule_b"], "description": item["description"],
                                             "eccn": extra.get("eccn"), "license": extra.get("license")}, src_h)
        store.rel("HAS_LINE_ITEM", shipment, line, src_h)
        store.rel("OF_COMMODITY", line, commodity, src_h)

    # Status events (214)
    if edi:
        carrier = store.node("Carrier", {"scac": e["carrier_scac"]}, src_e)
        store.rel("CARRIED_BY", shipment, carrier, src_e)
        for ev in e["events"]:
            event = store.node("StatusEvent", {
                "event_id": f"{hawb_no}-{ev['sequence']}", "status_code": ev["status_code"],
                "status": ev["status"], "reason_code": ev["reason_code"], "date": ev["date"]}, src_e)
            store.rel("HAS_EVENT", shipment, event, src_e)
            place = ev.get("location") or {}
            if place.get("city"):
                code = f"{place['city']}, {place.get('country') or ''}".strip(", ")
                loc = store.node("Location", {"code": code, "city": place["city"],
                                              "country": place.get("country"), "kind": "city"}, src_e)
                store.rel("AT_LOCATION", event, loc, src_e)
            if ev["status_code"] == "SD":
                reason = store.node("DelayReason", {"code": ev["reason_code"],
                                                    "description": ev["reason"]}, src_e)
                store.rel("CAUSED_BY", event, reason, src_e)

    # Documents
    for record, src in ((hawb, src_h), (sli, src_s), (edi, src_e), (status, src_r)):
        if record:
            doc = store.node("Document", {"doc_id": record["doc_id"], "doc_type": record["doc_type"],
                                          "path": record["path"]}, src)
            store.rel("DOCUMENTED_IN", shipment, doc, src)


def main():
    hawbs, slis = load("hawb"), load("sli")
    edis, statuses = load("edi214"), load("status")

    # Reference indexes
    sli_by_po = {r["references"]["po_number"]: r for r in slis}
    edi_by_hawb = {r["references"]["hawb_number"]: r for r in edis}
    status_by_hawb = {r["references"]["hawb_number"]: r for r in statuses}

    store = FactStore()
    unmatched = {"SLI": 0, "EDI214": 0, "STATUS_REPORT": 0}
    for hawb in hawbs:
        hawb_no, po = hawb["references"]["hawb_number"], hawb["references"]["po_number"]
        sli = sli_by_po.get(po)
        edi = edi_by_hawb.get(hawb_no)
        status = status_by_hawb.get(hawb_no)
        # The 214 carries both IDs, so it confirms the PO-based HAWB<->SLI match.
        if sli and edi and sli["references"]["shipment_id"] != edi["references"]["shipment_id"]:
            store.conflicts.append({"class": "Shipment", "key": hawb_no, "property": "shipment_id",
                                    "values": [sli["references"]["shipment_id"],
                                               edi["references"]["shipment_id"]],
                                    "sources": [sli["doc_id"], edi["doc_id"]]})
        for name, record in (("SLI", sli), ("EDI214", edi), ("STATUS_REPORT", status)):
            unmatched[name] += record is None
        build(store, hawb, sli, edi, status)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"nodes": list(store.nodes.values()),
                                    "relationships": list(store.rels.values())}, indent=1,
                                   ensure_ascii=False), encoding="utf-8")
    for path, rows in ((REVIEW_PATH, store.review), (CONFLICTS_PATH, store.conflicts)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    counts = {}
    for cls, _ in store.nodes:
        counts[cls] = counts.get(cls, 0) + 1
    rel_counts = {}
    for rel_type, _, _ in store.rels:
        rel_counts[rel_type] = rel_counts.get(rel_type, 0) + 1

    print(f"Shipments linked: {len(hawbs)}  (unmatched: {unmatched})")
    print("\nNodes:")
    for cls in CLASSES:
        print(f"  {cls:<15} {counts.get(cls, 0):>5}")
    print("\nRelationships:")
    for rel_type, n in sorted(rel_counts.items()):
        print(f"  {rel_type:<15} {n:>5}")
    print(f"\nReview queue (failed ontology checks): {len(store.review)} -> {REVIEW_PATH}")
    print(f"Conflicts between documents          : {len(store.conflicts)} -> {CONFLICTS_PATH}")
    print(f"Graph facts saved: {OUT_PATH}")

    # Spot check against the ground truth (testing only)
    if TRUTH_PATH.exists():
        truth = {t["hawb_number"]: t for t in
                 (json.loads(line) for line in TRUTH_PATH.read_text(encoding="utf-8").splitlines())}
        ok = total = 0
        for (cls, key), node in store.nodes.items():
            if cls != "Shipment" or key not in truth:
                continue
            t, p = truth[key], node["props"]
            for prop in ("shipment_id", "mode", "incoterm", "payment_terms", "delay_days",
                         "scheduled_delivery", "actual_delivery"):
                total += 1
                ok += p.get(prop) == t[prop]
        print(f"\nShipment properties matching ground truth: {ok}/{total}")


if __name__ == "__main__":
    main()