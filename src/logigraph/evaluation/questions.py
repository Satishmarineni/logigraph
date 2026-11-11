"""Step 6.4a: Build the benchmark questions with their correct answers.

Techniques:
  - Gold answers from ground truth: every correct answer is COMPUTED from
    data/processed/synthetic/shipments.jsonl (the generator's ground truth),
    never typed by hand, so the benchmark cannot be tuned to the system.
  - Only the 100 shipments that have documents are used (the ones in
    extracted/hawb.jsonl), because the system can only know those.
  - Question types, each scored a different way:
      set      -> the exact list of HAWB numbers
      number   -> one count or amount
      text     -> required phrases (names, codes, contract wording); each
                  requirement may list accepted alternatives, e.g. "$.50" or "$0.50"
      refusal  -> the answer is not in the data; the system must say so
  - Categories show WHERE each system is strong: graph facts, single
    shipment lookups, aggregates, contract text, hybrid, unanswerable.
"""
import json
from collections import Counter
from datetime import date

from logigraph.config import PROJECT_ROOT

GROUND_TRUTH = PROJECT_ROOT / "data" / "processed" / "synthetic" / "shipments.jsonl"
HAWB_RECORDS = PROJECT_ROOT / "data" / "processed" / "extracted" / "hawb.jsonl"
OUT_FILE = PROJECT_ROOT / "data" / "processed" / "eval" / "questions.jsonl"


def load_shipments():
    with open(HAWB_RECORDS, encoding="utf-8") as f:
        documented = {json.loads(line)["references"]["hawb_number"] for line in f}
    with open(GROUND_TRUTH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return [s for s in rows if s["hawb_number"] in documented]


def date_forms(iso):
    """Accepted ways to write a date: 2025-10-12, October 12, 2025, 12 October 2025."""
    d = date.fromisoformat(iso)
    return [iso, f"{d:%B} {d.day}, {d.year}", f"{d.day} {d:%B} {d.year}"]


def hawbs(shipments, keep):
    return sorted(s["hawb_number"] for s in shipments if keep(s))


def build_questions(S):
    late = [s for s in S if s["delay_days"] > 0]
    longest = max(late, key=lambda s: s["delay_days"])
    sample = sorted(S, key=lambda s: s["hawb_number"])[0]          # a fixed sample shipment
    company = sorted(late, key=lambda s: s["hawb_number"])[0]["shipper"]["name"]
    company_short = company.replace(" (SYNTHETIC)", "")
    country_counts = Counter(s["destination_country"] for s in S)
    mode_rate = {}
    for mode in {s["mode"] for s in S}:
        group = [s for s in S if s["mode"] == mode]
        mode_rate[mode] = sum(s["delay_days"] > 0 for s in group) / len(group)
    worst_mode = max(mode_rate, key=mode_rate.get)
    laptops_de = round(sum(li["gross_weight_kg"] for s in S if s["destination_country"] == "Germany"
                           for li in s["line_items"] if "laptop" in li["description"].lower()), 1)

    q = []

    def add(category, kind, question, gold, **extra):
        q.append({"id": f"Q{len(q) + 1:02d}", "category": category, "kind": kind,
                  "question": question, "gold": gold, **extra})

    # --- Graph facts: lists of shipments -------------------------------------
    add("graph_list", "set", "Which shipments were delayed by a customs hold?",
        hawbs(S, lambda s: s["delay_reason"] == "customs_hold" and s["delay_days"] > 0))
    add("graph_list", "set", "Which shipments were delayed because of weather?",
        hawbs(S, lambda s: s["delay_reason"] == "weather" and s["delay_days"] > 0))
    add("graph_list", "set", "Which shipments were delivered late?", hawbs(S, lambda s: s["delay_days"] > 0))
    add("graph_list", "set", "Which shipments contain lithium batteries?",
        hawbs(S, lambda s: any("lithium" in li["description"].lower() for li in s["line_items"])))
    add("graph_list", "set", "Which shipments contain dangerous goods?",
        hawbs(S, lambda s: s["dangerous_goods_un"]))
    add("graph_list", "set", "List the prepaid Air shipments shipped under EXW.",
        hawbs(S, lambda s: s["mode"] == "Air" and s["incoterm"] == "EXW" and s["payment_terms"] == "prepaid"))
    add("graph_list", "set", "Which shipments went by Ocean?", hawbs(S, lambda s: s["mode"] == "Ocean"))
    add("graph_list", "set", "Which Truck shipments were delivered late?",
        hawbs(S, lambda s: s["mode"] == "Truck" and s["delay_days"] > 0))
    add("graph_list", "set", f"Which shipments involve the company {company_short}?",
        hawbs(S, lambda s: company_short in s["shipper"]["name"] or company_short in s["consignee"]["name"]))

    # --- Aggregates: counts and totals ----------------------------------------
    add("aggregate", "number", "How many shipments went by Truck?", sum(s["mode"] == "Truck" for s in S))
    add("aggregate", "number", "How many shipments were shipped under DDP?", sum(s["incoterm"] == "DDP" for s in S))
    add("aggregate", "number", "How many shipments were sent to Japan?", country_counts["Japan"])
    add("aggregate", "number", "How many shipments were delivered late?", len(late))
    add("aggregate", "number", "What is the total weight in kg of laptops shipped to Germany?", laptops_de)
    add("aggregate", "text", "Which transport mode has the highest late-delivery rate?", [worst_mode])

    # --- Single shipment lookups ---------------------------------------------
    add("lookup", "number", f"How many days late was shipment {longest['hawb_number']}?", longest["delay_days"])
    add("lookup", "number", f"What is the declared value in USD of HAWB {sample['hawb_number']}?",
        sample["declared_value_usd"])
    add("lookup", "text", f"Who is the consignee of HAWB {sample['hawb_number']}?",
        [sample["consignee"]["name"].replace(" (SYNTHETIC)", "")])
    add("lookup", "text", f"What is the destination country of HAWB {sample['hawb_number']}?",
        [sample["destination_country"]])
    add("lookup", "text", f"On what date was HAWB {longest['hawb_number']} actually delivered?",
        [date_forms(longest["actual_delivery"])])

    # --- Contract text (HAWB terms and conditions) ----------------------------
    add("contract", "text", "What is the carrier's liability limit per pound?",
        [["$.50", "$0.50", "50 cents"]])
    add("contract", "text", "Within how many days must a claim be filed for an export shipment?", ["180"])
    add("contract", "text", "Within how many days must a claim for a domestic shipment be made?", ["270"])
    add("contract", "text", "How is dimensional weight used when rating a shipment?",
        [["whichever is greater", "greater of"]])
    add("contract", "text", "If the broker does not pay customs charges, who is still liable for them?",
        ["importer of record"])

    # --- Hybrid: facts + contract wording ------------------------------------
    add("hybrid", "set+text", "Which shipments were delayed by weather, and what does the contract say "
        "about the carrier's responsibility for delays?",
        hawbs(S, lambda s: s["delay_reason"] == "weather" and s["delay_days"] > 0),
        phrases=["gross negligence"])

    # --- Not in the data: the system must refuse -----------------------------
    add("unanswerable", "refusal", "What is the name of the carrier's CEO?", None)
    add("unanswerable", "refusal",
        f"What was the temperature inside the container of HAWB {longest['hawb_number']}?", None)
    add("unanswerable", "refusal", "What was the fuel surcharge percentage on HAWB SYN00000000?", None)
    return q


def main():
    questions = build_questions(load_shipments())
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for item in questions:
            f.write(json.dumps(item) + "\n")
    print(f"{len(questions)} questions -> {OUT_FILE.relative_to(PROJECT_ROOT)}")
    for item in questions:
        gold = item["gold"] if not isinstance(item["gold"], list) or len(item["gold"]) <= 4 \
            else f"{len(item['gold'])} HAWBs"
        print(f"  {item['id']} [{item['category']:<12}] {item['question'][:62]:<62} gold={gold}")


if __name__ == "__main__":
    main()