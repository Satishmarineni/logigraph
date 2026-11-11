"""Step 6.2: Graph retrieval - run a pre-written Cypher template for each plan.

Techniques:
  - Query templates: every intent has a fixed, tested Cypher query. The LLM
    never writes Cypher; the planner's values are passed as PARAMETERS
    ($mode, $country, ...), never pasted into the query text, so a question
    can never inject Cypher.
  - Composable filters: each filter the planner found (mode, Incoterm,
    payment terms, delay reason, country, company, commodity, dates) adds one
    WHERE condition. Filters that need a path use EXISTS { } subqueries.
  - Fuzzy commodity matching: "lithium batteries" must match
    "Lithium-ion battery packs", so each word is cut to a short stem
    ("lithi", "batte") and every stem must appear in the description.
  - Provenance: every row carries the source document IDs for citations.
"""
import argparse
import json
import re

from neo4j import GraphDatabase

from logigraph.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

STOP_WORDS = {"the", "and", "for", "with", "of", "units", "goods"}


def commodity_stems(text):
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [w[:5] for w in words if len(w) >= 4 and w not in STOP_WORDS]


def shipment_filters(plan):
    """Build WHERE conditions and parameters from the plan's filters."""
    where, params = [], {}
    if plan["intent"] == "delayed_shipments":
        where.append("s.delay_days > 0")
    if plan["intent"] == "dangerous_goods":
        where.append("EXISTS { (s)-[:CONTAINS_DG]->(:DangerousGoods) }")
    if plan.get("delay_reason"):
        where.append("s.delay_reason = $delay_reason")
        params["delay_reason"] = plan["delay_reason"]
    for prop in ("mode", "incoterm", "payment_terms"):
        if plan.get(prop):
            where.append(f"s.{prop} = ${prop}")
            params[prop] = plan[prop]
    if plan.get("date_from"):
        where.append("s.ship_date >= date($date_from)")
        params["date_from"] = plan["date_from"]
    if plan.get("date_to"):
        where.append("s.ship_date <= date($date_to)")
        params["date_to"] = plan["date_to"]
    if plan.get("destination_country"):
        where.append("EXISTS { (s)-[:DESTINED_FOR]->(d:Location) "
                     "WHERE toLower(d.country) = toLower($country) }")
        params["country"] = plan["destination_country"]
    if plan.get("company_name"):
        where.append("EXISTS { (s)-[:SHIPPED_BY|CONSIGNED_TO]->(co:Company) "
                     "WHERE toLower(co.name) CONTAINS toLower($company) }")
        params["company"] = plan["company_name"]
    stems = commodity_stems(plan.get("commodity"))
    if stems:
        where.append("EXISTS { (s)-[:HAS_LINE_ITEM]->(:LineItem)-[:OF_COMMODITY]->(cm:Commodity) "
                     "WHERE all(t IN $stems WHERE toLower(cm.description) CONTAINS t) }")
        params["stems"] = stems
    return where, params


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
SHIPMENT_DETAILS = """
MATCH (s:Shipment {hawb_number: $hawb})
OPTIONAL MATCH (s)-[:SHIPPED_BY]->(shipper:Company)
OPTIONAL MATCH (s)-[:CONSIGNED_TO]->(consignee:Company)
OPTIONAL MATCH (s)-[:ORIGINATES_AT]->(o:Location)
OPTIONAL MATCH (s)-[:DESTINED_FOR]->(d:Location)
OPTIONAL MATCH (s)-[:USES_SERVICE]->(svc:ServiceLevel)
OPTIONAL MATCH (s)-[:CONTAINS_DG]->(dg:DangerousGoods)
OPTIONAL MATCH (s)-[:HAS_LINE_ITEM]->(li:LineItem)-[:OF_COMMODITY]->(cm:Commodity)
RETURN s.hawb_number AS hawb, s.shipment_id AS shipment_id, s.po_number AS po, s.mode AS mode,
       toString(s.ship_date) AS ship_date, s.incoterm AS incoterm, s.payment_terms AS payment_terms,
       s.declared_value_usd AS declared_value_usd, s.insurance_usd AS insurance_usd,
       toString(s.scheduled_delivery) AS scheduled_delivery, toString(s.actual_delivery) AS actual_delivery,
       s.delay_days AS delay_days, s.delay_reason AS delay_reason,
       shipper.name AS shipper, consignee.name AS consignee, o.code AS origin, d.code AS destination,
       d.country AS destination_country, svc.code AS service, dg.un_number AS dangerous_goods,
       collect(DISTINCT {commodity: cm.description, schedule_b: cm.schedule_b, pieces: li.pieces,
                         kg: li.gross_weight_kg, eccn: cm.eccn}) AS line_items,
       s.sources AS sources
"""

SHIPMENT_TIMELINE = """
MATCH (s:Shipment {hawb_number: $hawb})-[:HAS_EVENT]->(e:StatusEvent)
OPTIONAL MATCH (e)-[:AT_LOCATION]->(loc:Location)
OPTIONAL MATCH (e)-[:CAUSED_BY]->(r:DelayReason)
RETURN toString(e.date) AS date, e.status_code AS status_code, e.status AS status,
       e.reason_code AS reason_code, r.description AS delay_reason, loc.code AS location,
       e.sources AS sources
ORDER BY e.date, e.event_id
"""

SHIPMENT_LIST = """
MATCH (s:Shipment)
{where}
OPTIONAL MATCH (s)-[:SHIPPED_BY]->(shipper:Company)
OPTIONAL MATCH (s)-[:CONSIGNED_TO]->(consignee:Company)
OPTIONAL MATCH (s)-[:DESTINED_FOR]->(d:Location)
OPTIONAL MATCH (s)-[:CONTAINS_DG]->(dg:DangerousGoods)
RETURN s.hawb_number AS hawb, s.mode AS mode, toString(s.ship_date) AS ship_date,
       s.incoterm AS incoterm, s.payment_terms AS payment_terms,
       s.delay_days AS delay_days, s.delay_reason AS delay_reason,
       shipper.name AS shipper, consignee.name AS consignee,
       d.code AS destination, d.country AS destination_country, dg.un_number AS dangerous_goods,
       s.sources AS sources
ORDER BY s.delay_days DESC, s.ship_date
"""

COMMODITY_SUMMARY = """
MATCH (s:Shipment)-[:HAS_LINE_ITEM]->(li:LineItem)-[:OF_COMMODITY]->(cm:Commodity)
{where}
RETURN cm.description AS commodity, cm.schedule_b AS schedule_b,
       count(DISTINCT s) AS shipments, sum(li.pieces) AS total_pieces,
       round(sum(li.gross_weight_kg), 1) AS total_kg,
       collect(DISTINCT s.hawb_number)[..10] AS example_hawbs,
       collect(DISTINCT s.sources)[..10] AS sources_list
ORDER BY total_kg DESC
"""

DELAY_STATS = """
MATCH (s:Shipment)
{where}
RETURN s.mode AS mode, count(s) AS shipments,
       sum(CASE WHEN s.delay_days > 0 THEN 1 ELSE 0 END) AS late,
       round(100.0 * sum(CASE WHEN s.delay_days > 0 THEN 1 ELSE 0 END) / count(s), 1) AS late_pct,
       round(avg(CASE WHEN s.delay_days > 0 THEN s.delay_days END), 1) AS avg_days_late
ORDER BY late_pct DESC
"""


def build_query(plan):
    """Return (cypher, params) for a plan, or (None, None) for text-only intents."""
    intent = plan["intent"]
    if intent in ("shipment_details", "shipment_timeline"):
        template = SHIPMENT_DETAILS if intent == "shipment_details" else SHIPMENT_TIMELINE
        return template, {"hawb": plan["hawb_number"]}

    where, params = shipment_filters(plan)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    if intent == "commodity_summary":
        stems = params.get("stems")
        # The commodity filter applies to the line item itself, not just "the shipment has it".
        extra = ["all(t IN $stems WHERE toLower(cm.description) CONTAINS t)"] if stems else []
        conditions = [w for w in where if "OF_COMMODITY" not in w] + extra
        clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return COMMODITY_SUMMARY.format(where=clause), params
    if intent == "delay_stats":
        return DELAY_STATS.format(where=clause), params
    if intent in ("delayed_shipments", "find_shipments", "dangerous_goods", "company_shipments"):
        return SHIPMENT_LIST.format(where=clause), params
    return None, None


def doc_ids(sources_json):
    try:
        return sorted({s["doc_id"] for s in json.loads(sources_json or "[]") if s and s.get("doc_id")})
    except (TypeError, json.JSONDecodeError):
        return []


def retrieve(driver, plan):
    cypher, params = build_query(plan)
    if cypher is None:
        return {"intent": plan["intent"], "cypher": None, "params": None, "rows": [], "row_count": 0}
    with driver.session() as session:
        rows = [record.data() for record in session.run(cypher, **params)]
    for row in rows:
        if "sources" in row:
            row["doc_ids"] = doc_ids(row.pop("sources"))
        elif "sources_list" in row:              # aggregated rows: merge the shipments' sources
            row["doc_ids"] = sorted({d for src in row.pop("sources_list") for d in doc_ids(src)})
    return {"intent": plan["intent"], "cypher": " ".join(cypher.split()), "params": params,
            "rows": rows, "row_count": len(rows)}


def empty_plan(intent, **filters):
    plan = {"intent": intent, "hawb_number": None, "company_name": None, "destination_country": None,
            "mode": None, "incoterm": None, "payment_terms": None, "delay_reason": None,
            "commodity": None, "date_from": None, "date_to": None}
    plan.update(filters)
    return plan


SAMPLE_PLANS = [
    ("Everything about SYN19046318", empty_plan("shipment_details", hawb_number="SYN19046318")),
    ("Timeline of SYN12515446", empty_plan("shipment_timeline", hawb_number="SYN12515446")),
    ("Delayed by customs hold", empty_plan("delayed_shipments", delay_reason="customs_hold")),
    ("Prepaid Air under EXW", empty_plan("find_shipments", mode="Air", incoterm="EXW", payment_terms="prepaid")),
    ("Shippers of lithium batteries", empty_plan("company_shipments", commodity="lithium batteries")),
    ("Laptops shipped to Germany", empty_plan("commodity_summary", commodity="laptops",
                                              destination_country="Germany")),
    ("Late rate by mode", empty_plan("delay_stats")),
]


def main():
    parser = argparse.ArgumentParser(description="Run graph retrieval for sample plans or a question.")
    parser.add_argument("--question", help="plan this question with the LLM, then retrieve")
    parser.add_argument("--show-cypher", action="store_true")
    args = parser.parse_args()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    if args.question:
        from openai import OpenAI
        from logigraph.config import OPENAI_API_KEY
        from logigraph.rag.query_planner import plan_question
        plan = plan_question(OpenAI(api_key=OPENAI_API_KEY), args.question)
        cases = [(args.question, plan)]
    else:
        cases = SAMPLE_PLANS

    for label, plan in cases:
        result = retrieve(driver, plan)
        print(f"\n{label}  [{result['intent']}]  -> {result['row_count']} rows")
        if args.show_cypher and result["cypher"]:
            print(f"  cypher: {result['cypher'][:300]}")
            print(f"  params: {result['params']}")
        for row in result["rows"][:5]:
            short = {k: v for k, v in row.items() if v not in (None, [], "")}
            print(f"  {json.dumps(short, default=str)[:240]}")
    driver.close()


if __name__ == "__main__":
    main()