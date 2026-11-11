"""Step 4.1: Read the filled HAWB and SLI forms back into structured records.

Technique: form-field (AcroForm widget) extraction through the SAME field maps
used to fill the forms, read in the opposite direction. No LLM is needed:
the values sit in named fields, so extraction is exact and repeatable.

Each document becomes one record:
  references  - IDs used to link documents of the same shipment later (4.4)
  fields      - business fields (shipper, consignee, values, terms, ...)
  line_items  - one entry per filled line
  evidence    - which form field each business field came from (provenance)
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pymupdf

from logigraph.forms.field_maps import (
    HAWB_CHECKBOX_GROUPS, HAWB_LINE_ITEM_COLUMNS, HAWB_TEXT_FIELDS,
    SLI_CHECKBOX_GROUPS, SLI_DOCUMENT_CHECKBOXES, SLI_DOCUMENT_COLUMN_SPLIT_X, SLI_TEXT_FIELDS,
)

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed/extracted")
TRUTH_PATH = Path("data/processed/synthetic/shipments.jsonl")

# The HAWB number has no form field; it is printed on the MOVEMENT# line.
# We read the page text inside this box (x0, y0, x1, y1 in PDF points).
AIR_BILL_BOX = (365, 120, 600, 142)

# Country names that can follow the city on an address line. Needed because
# both the country ("United Kingdom") and the postal code ("SW1A 1AA") can
# contain spaces, so a simple split is ambiguous.
COUNTRIES = ["United Arab Emirates", "United Kingdom", "United States", "Netherlands",
             "Singapore", "Australia", "Germany", "Mexico", "Brazil", "Japan", "India"]

# Mode is not a field on the HAWB; it follows from the service column ticked.
SERVICE_TO_MODE = {"intl_air": "Air", "ocean": "Ocean", "domestic": "Truck"}


# ---------------------------------------------------------------------------
# Reading the PDF
# ---------------------------------------------------------------------------
def read_widgets(pdf_path):
    """Return text field values, ticked checkbox names and ticked (name, x0) pairs."""
    text, ticked, ticked_at = {}, set(), set()
    doc = pymupdf.open(pdf_path)
    for w in doc[0].widgets():
        if w.field_type_string == "Text":
            value = (w.field_value or "").strip()
            if value:
                text[w.field_name] = value
        elif w.field_type_string == "CheckBox" and w.field_value not in (None, "", "Off", False):
            ticked.add(w.field_name)
            ticked_at.add((w.field_name, round(w.rect.x0)))
    page_text = doc[0].get_textbox(pymupdf.Rect(*AIR_BILL_BOX)).strip()
    doc.close()
    return text, ticked, ticked_at, page_text


def ticked_values(groups, ticked):
    """{"payment.terms": {"Check Box37": "collect"}} + ticked boxes -> {"payment.terms": ["collect"]}"""
    out = {}
    for group, boxes in groups.items():
        values = [value for name, value in boxes.items() if name in ticked]
        if values:
            out[group] = values
    return out


# ---------------------------------------------------------------------------
# Cleaning values
# ---------------------------------------------------------------------------
def to_iso(us_date):
    return datetime.strptime(us_date, "%m/%d/%Y").date().isoformat() if us_date else None


def to_number(text):
    """'1,028' -> 1028, '50.3 kg' -> 50.3, '' -> None"""
    if not text:
        return None
    match = re.search(r"-?[\d,]+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group().replace(",", ""))
    return int(number) if number.is_integer() else number


def parse_party(block):
    """Split a 3-line name/address box into its parts."""
    if not block:
        return None
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    party = {"name": lines[0]}
    if len(lines) > 2:
        # Street addresses can take more than one line, so the city line is
        # always the LAST line and everything between is the street address.
        party["address"] = ", ".join(lines[1:-1])
        # "Houston, TX 35494"  or  "London, United Kingdom SW1A 1AA"
        city, _, rest = lines[-1].partition(",")
        rest = rest.strip()
        party["city"] = city.strip()
        region, postal = rest, None
        us_state = re.match(r"^([A-Z]{2})\s+(.+)$", rest)
        if us_state:
            region, postal = us_state.groups()
        else:
            for country in COUNTRIES:
                if rest.startswith(country):
                    region, postal = country, rest[len(country):].strip() or None
                    break
        party["state_or_country"] = region
        party["postal_code"] = postal
    return party


# ---------------------------------------------------------------------------
# HAWB
# ---------------------------------------------------------------------------
def extract_hawb(pdf_path):
    text, ticked, _, page_text = read_widgets(pdf_path)
    f = {business: text.get(field) for field, business in HAWB_TEXT_FIELDS.items()}
    checks = ticked_values(HAWB_CHECKBOX_GROUPS, ticked)

    # Service: the first service column with a tick decides the mode.
    service, mode = None, None
    for column in ("intl_air", "ocean", "domestic", "local"):
        values = checks.get(f"service.{column}")
        if values:
            service = f"{column}.{values[0]}"
            mode = SERVICE_TO_MODE.get(column)
            if service == "intl_air.charter":
                mode = "Air Charter"
            break

    hawb_match = re.search(r"[A-Z0-9]{8,}", page_text)

    line_items = []
    cols = HAWB_LINE_ITEM_COLUMNS
    for row in range(len(cols["pieces"])):
        pieces = text.get(cols["pieces"][row])
        if not pieces:
            continue
        line_items.append({
            "row": row + 1,
            "pieces": to_number(pieces),
            "gross_weight_kg": to_number(text.get(cols["gross_weight"][row])),
            "description": text.get(cols["description"][row]),
            "schedule_b": text.get(cols["schedule_b"][row]),
            "dimensions_in": (text.get(cols["dimensions"][row]) or "").replace(" in", "") or None,
        })

    dg = "dangerous_goods.present" in checks
    return {
        "doc_id": pdf_path.stem,
        "doc_type": "HAWB",
        "path": str(pdf_path),
        "references": {
            "hawb_number": hawb_match.group() if hawb_match else None,
            "po_number": f["shipper.po_number"],
            "bol_number": f["shipper.bol_number"],
        },
        "fields": {
            "ship_date": to_iso(f["shipment.date"]),
            "origin_code": f["shipment.origin"],
            "destination_code": f["shipment.destination"],
            "mode": mode,
            "service_level": service,
            "payment_terms": (checks.get("payment.terms") or [None])[0],
            "declared_value_usd": to_number(f["value.declared"]),
            "customs_value_usd": to_number(f["value.customs"]),
            "insurance_usd": to_number(f["value.insurance_amount"]) or 0,
            "cod_amount_usd": to_number(f["value.cod_amount"]),
            "special_instructions": f["shipment.special_instructions"],
            "shipper": {**(parse_party(f["shipper.name_address"]) or {}),
                        "account_number": f["shipper.account_number"],
                        "contact": f["shipper.contact_ein"], "phone": f["shipper.phone"]},
            "consignee": {**(parse_party(f["consignee.name_address"]) or {}),
                          "account_number": f["consignee.account_number"],
                          "contact": f["consignee.contact_broker"], "phone": f["consignee.phone"]},
            "dangerous_goods_un": f["dangerous_goods.un_number"] if dg else None,
        },
        "line_items": line_items,
        "evidence": {"hawb_number": "page text at MOVEMENT# line",
                     **{business: field for field, business in HAWB_TEXT_FIELDS.items()
                        if text.get(field)}},
    }


# ---------------------------------------------------------------------------
# SLI
# ---------------------------------------------------------------------------
def extract_sli(pdf_path):
    text, ticked, ticked_at, _ = read_widgets(pdf_path)
    f = {business: text.get(field) for field, business in SLI_TEXT_FIELDS.items()}
    checks = ticked_values(SLI_CHECKBOX_GROUPS, ticked)

    # Document checkboxes: same name can sit in both columns, so use position.
    attached, to_prepare = [], []
    for name, x0 in ticked_at:
        if name in SLI_DOCUMENT_CHECKBOXES:
            column = attached if x0 < SLI_DOCUMENT_COLUMN_SPLIT_X else to_prepare
            column.append(SLI_DOCUMENT_CHECKBOXES[name])

    # Commodity boxes hold one line per item, in the same order in every box.
    def lines(key):
        return [line.strip() for line in (f[key] or "").splitlines() if line.strip()]

    sched = lines("commodity.schedule_b")
    eccn, lic = lines("commodity.eccn"), lines("commodity.license")
    qty, kg, usd = lines("commodity.quantity"), lines("commodity.weight_kg"), lines("commodity.value_usd")
    commodities = []
    for i, entry in enumerate(sched):
        code, _, description = entry.partition(" ")
        commodities.append({
            "schedule_b": code,
            "description": description.strip(),
            "eccn": eccn[i] if i < len(eccn) else None,
            "license": lic[i] if i < len(lic) else None,
            "pieces": to_number(qty[i]) if i < len(qty) else None,
            "weight_kg": to_number(kg[i]) if i < len(kg) else None,
            "value_usd": to_number(usd[i]) if i < len(usd) else None,
        })

    air, ocean = f["service.air"], f["service.ocean"]
    return {
        "doc_id": pdf_path.stem,
        "doc_type": "SLI",
        "path": str(pdf_path),
        "references": {
            "shipment_id": f["shipment.shipper_reference"],
            "po_number": f["shipment.consignee_po"],
            "quote_number": f["sli.quote_number"],
        },
        "fields": {
            "sli_date": to_iso(f["sli.date"]),
            "usppi": {**(parse_party(f["usppi.name_address"]) or {}), "ein": f["usppi.ein"]},
            "ultimate_consignee": parse_party(f["ultimate_consignee.name_address"]),
            "consignee_type": (checks.get("consignee.type") or [None])[0],
            "parties_related": (checks.get("parties.related") or [None])[0],
            "origin_state": f["shipment.origin_state"],
            "destination_country": f["shipment.destination_country"],
            "incoterm": (checks.get("shipment.incoterm") or [None])[0],
            "routed_export": (checks.get("shipment.routed_export") or [None])[0] == "yes",
            "requested_mode": "Air" if air else ("Ocean" if ocean else None),
            "declared_value_usd": to_number(f["value.declared"]),
            "insurance_usd": to_number(f["value.insurance_amount"]) or 0,
            "payment_location": f["payment.location"],
            "special_instructions": f["shipment.special_instructions"],
            "dangerous_goods": bool(f["dangerous_goods.yes_initials"]),
            "documents_attached": sorted(attached),
            "documents_to_prepare": sorted(to_prepare),
        },
        "line_items": commodities,
        "evidence": {business: field for field, business in SLI_TEXT_FIELDS.items() if text.get(field)},
    }


# ---------------------------------------------------------------------------
# Check against the ground truth (only for testing; extraction never reads it)
# ---------------------------------------------------------------------------
def check(hawbs, slis):
    truth = [json.loads(line) for line in TRUTH_PATH.read_text(encoding="utf-8").splitlines()]
    by_hawb = {t["hawb_number"]: t for t in truth}
    by_id = {t["shipment_id"]: t for t in truth}

    tests = {
        "HAWB hawb_number": [(h["references"]["hawb_number"] in by_hawb) for h in hawbs],
    }
    for h in hawbs:
        t = by_hawb.get(h["references"]["hawb_number"])
        if not t:
            continue
        hf = h["fields"]
        for name, got, want in [
            ("HAWB ship_date", hf["ship_date"], t["ship_date"]),
            ("HAWB mode", hf["mode"], t["mode"]),
            ("HAWB payment_terms", hf["payment_terms"], t["payment_terms"]),
            ("HAWB shipper name", hf["shipper"].get("name"), t["shipper"]["name"]),
            ("HAWB consignee name", hf["consignee"].get("name"), t["consignee"]["name"]),
            ("HAWB consignee country", hf["consignee"].get("state_or_country"),
             t["consignee"]["state_or_country"]),
            ("HAWB consignee postal", hf["consignee"].get("postal_code"),
             t["consignee"]["postal_code"]),
            ("HAWB declared value", hf["declared_value_usd"], t["declared_value_usd"]),
            ("HAWB total pieces", sum(i["pieces"] for i in h["line_items"]),
             sum(i["pieces"] for i in t["line_items"])),
            ("HAWB dangerous goods", hf["dangerous_goods_un"], t["dangerous_goods_un"]),
        ]:
            tests.setdefault(name, []).append(got == want)
    for s in slis:
        t = by_id.get(s["references"]["shipment_id"])
        if not t:
            tests.setdefault("SLI shipment_id", []).append(False)
            continue
        sf = s["fields"]
        for name, got, want in [
            ("SLI shipment_id", True, True),
            ("SLI incoterm", sf["incoterm"], t["incoterm"]),
            ("SLI consignee_type", sf["consignee_type"], t["consignee_type"]),
            ("SLI routed_export", sf["routed_export"], t["routed_export"]),
            ("SLI destination country", sf["destination_country"], t["destination_country"]),
            ("SLI schedule B codes", [c["schedule_b"] for c in s["line_items"]],
             [i["schedule_b"] for i in t["line_items"]]),
        ]:
            tests.setdefault(name, []).append(got == want)

    print("\nAccuracy against ground truth:")
    for name, results in tests.items():
        print(f"  {name:<26} {sum(results):>3}/{len(results)}")


def main():
    parser = argparse.ArgumentParser(description="Extract HAWB and SLI form fields.")
    parser.add_argument("--check", action="store_true", help="compare with the ground truth")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hawbs = [extract_hawb(p) for p in sorted((RAW_DIR / "hawb").glob("*.pdf"))]
    slis = [extract_sli(p) for p in sorted((RAW_DIR / "sli").glob("*.pdf"))]

    for name, records in (("hawb", hawbs), ("sli", slis)):
        out = OUT_DIR / f"{name}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name.upper():<4}: {len(records)} records -> {out}")

    if hawbs:
        h = hawbs[0]
        print(f"\nExample HAWB {h['references']['hawb_number']}: "
              f"{h['fields']['mode']}, {h['fields']['origin_code']}->{h['fields']['destination_code']}, "
              f"{h['fields']['payment_terms']}, shipper '{h['fields']['shipper'].get('name')}'")

    if args.check:
        check(hawbs, slis)


if __name__ == "__main__":
    main()