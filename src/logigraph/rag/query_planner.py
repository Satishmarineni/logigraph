"""Step 6.1: Query planner - turn a plain-English question into a plan.

The plan says:
  - intent : which kind of question this is (each graph intent maps to one
             pre-written Cypher template in Step 6.2)
  - route  : "graph", "vector" or "hybrid" (graph facts + text passages)
  - filters: the entities to look up (HAWB, company, country, mode, Incoterm,
             payment terms, delay reason, commodity, date range)

Techniques:
  - Schema-constrained LLM output: the plan is a Pydantic model, and fields
    with a fixed set of values (mode, Incoterm, ...) are Literal types, so the
    LLM can only choose values that exist in the ontology.
  - Deterministic overrides: exact IDs (HAWB numbers) are taken from the
    question with a regular expression, never from the LLM.
  - Routing by intent: contract questions go to vector search, fact questions
    to the graph, and fact questions that also need wording go to both.
The LLM never writes Cypher; it only picks an intent and fills in values.
"""
import argparse
import re
from datetime import date
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from logigraph.config import OPENAI_API_KEY, OPENAI_MODEL

Intent = Literal[
    "shipment_details",     # everything about one HAWB
    "shipment_timeline",    # the 214 milestones of one HAWB
    "delayed_shipments",    # late shipments, optionally filtered
    "find_shipments",       # list or count shipments matching filters
    "dangerous_goods",      # shipments with dangerous goods and their shippers
    "company_shipments",    # shipments of one shipper or consignee
    "commodity_summary",    # pieces / weight / shipments per commodity
    "delay_stats",          # late rate and average delay by mode
    "contract_terms",       # answered from the HAWB terms and conditions text
    "unsupported",          # nothing in the data can answer it
]
GRAPH_INTENTS = {"shipment_details", "shipment_timeline", "delayed_shipments", "find_shipments",
                 "dangerous_goods", "company_shipments", "commodity_summary", "delay_stats"}

HAWB_PATTERN = re.compile(r"\b[A-Z]{3}\d{8}\b")


class QueryPlan(BaseModel):
    intent: Intent
    needs_text: bool = Field(description="True if the answer ALSO needs wording from the terms and "
                                         "conditions or a status report, not only stored facts")
    hawb_number: str | None
    company_name: str | None = Field(description="Shipper or consignee name mentioned in the question")
    destination_country: str | None = Field(description="Full English country name, e.g. Germany")
    mode: Literal["Air", "Air Charter", "Ocean", "Truck"] | None
    incoterm: Literal["EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP",
                      "FAS", "FOB", "CFR", "CIF"] | None
    payment_terms: Literal["prepaid", "collect", "third_party", "cod"] | None
    delay_reason: Literal["weather", "customs_hold", "documentation_issue", "consignee_unavailable",
                          "missed_connection", "capacity_shortage"] | None
    commodity: str | None = Field(description="Goods mentioned, e.g. 'lithium batteries', 'laptops'")
    date_from: str | None = Field(description="Ship date range start, YYYY-MM-DD")
    date_to: str | None = Field(description="Ship date range end, YYYY-MM-DD")
    text_query: str | None = Field(description="Short search phrase for the terms and conditions or "
                                               "status reports, when needs_text is true or intent is contract_terms")


SYSTEM_PROMPT = """You plan how to answer questions about logistics shipments.
The data holds export shipments (HAWB number, mode, ship date, Incoterm, payment terms,
declared value, delivery dates, delay days and reason), shipper and consignee companies,
destination airports/seaports and countries, line items and commodities (Schedule B),
dangerous goods (UN numbers), X12 214 status events, and the HAWB terms and conditions text
(liability limits, claims deadlines, insurance, customs charges, dimensional weight, etc.).

Pick the ONE intent that fits best and fill only the values the question states.
Do not guess values that are not in the question. Dates must be YYYY-MM-DD.
Use contract_terms for questions about rules, liability, claims, insurance or fees.
Set needs_text true when a fact question also asks what the contract or report says."""


def plan_with_llm(client, question):
    completion = client.chat.completions.parse(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": f"Today is {date.today().isoformat()}. Question: {question}"}],
        response_format=QueryPlan,
    )
    message = completion.choices[0].message
    if message.refusal:
        raise ValueError(f"LLM refused: {message.refusal}")
    return message.parsed


def valid_iso(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def finalize(plan, question):
    """Apply deterministic rules on top of the LLM plan and choose the route."""
    plan = plan.model_copy()

    # Exact IDs come from the question text, never from the LLM.
    found = HAWB_PATTERN.findall(question)
    plan.hawb_number = found[0] if found else None
    if plan.intent in ("shipment_details", "shipment_timeline") and not plan.hawb_number:
        plan.intent = "find_shipments"

    plan.date_from, plan.date_to = valid_iso(plan.date_from), valid_iso(plan.date_to)

    if plan.intent == "contract_terms":
        route = "vector"
        plan.text_query = plan.text_query or question
    elif plan.intent == "unsupported":
        route = "vector"                     # still try the text before giving up
        plan.text_query = plan.text_query or question
    elif plan.needs_text:
        route = "hybrid"
        plan.text_query = plan.text_query or question
    else:
        route = "graph"
    return plan, route


def plan_question(client, question):
    plan, route = finalize(plan_with_llm(client, question), question)
    return {"question": question, "route": route, **plan.model_dump()}


SAMPLE_QUESTIONS = [
    "Tell me everything about HAWB SYN19046318",
    "Show the status history of SYN12515446",
    "Which shipments were delayed by a customs hold?",
    "How many prepaid air shipments under EXW were there?",
    "Which shippers send lithium batteries?",
    "What is the total weight of laptops shipped to Germany?",
    "Which mode has the highest late-delivery rate?",
    "What is the carrier's liability limit per pound?",
    "Which shipments were delayed by weather, and what does the contract say about delays?",
    "What is the CEO's name of the carrier?",
]


def main():
    parser = argparse.ArgumentParser(description="Plan how to answer a question.")
    parser.add_argument("--question", help="plan this question instead of the samples")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is missing. Add it to the .env file.")
    client = OpenAI(api_key=OPENAI_API_KEY, max_retries=3, timeout=60)

    for question in [args.question] if args.question else SAMPLE_QUESTIONS:
        p = plan_question(client, question)
        filters = {k: v for k, v in p.items()
                   if k not in ("question", "route", "intent", "needs_text", "text_query") and v}
        print(f"\nQ: {question}")
        print(f"   route={p['route']:<6} intent={p['intent']:<18} filters={filters}")
        if p["text_query"]:
            print(f"   text_query: {p['text_query']}")


if __name__ == "__main__":
    main()