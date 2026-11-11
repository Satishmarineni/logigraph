"""Step 4.3: Read the status report PDFs with an LLM, then check its answer.

Technique: schema-constrained LLM extraction + validation gates.
  1. Extract the page text with PyMuPDF (the report has a real text layer).
  2. Ask the LLM to fill a Pydantic schema (structured output), including an
     evidence quote copied word for word from the report.
  3. Validation gates (plain Python, no LLM):
       - the quote must appear verbatim in the report text
       - the delay days (if any) must appear inside that quote
       - the HAWB number must appear in the report text
     Anything that fails goes to a review queue instead of the graph.
  4. Cross-check against the 214 record of the same HAWB (4.2) and flag conflicts.
LLM answers are cached by file hash, so re-running costs nothing.
"""
import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

import pymupdf
from openai import OpenAI
from pydantic import BaseModel, Field

from logigraph.config import OPENAI_API_KEY, OPENAI_MODEL

RAW_DIR = Path("data/raw/status")
OUT_PATH = Path("data/processed/extracted/status.jsonl")
REVIEW_PATH = Path("data/processed/review/status_review.jsonl")
CACHE_DIR = Path("data/processed/llm_cache/status")
EDI_PATH = Path("data/processed/extracted/edi214.jsonl")

DelayReason = Literal["weather", "customs_hold", "documentation_issue",
                      "consignee_unavailable", "missed_connection",
                      "capacity_shortage", "other"]

# Which X12 reason code (from the 214) agrees with which LLM reason category.
REASON_CODE_TO_CATEGORIES = {
    "AO": {"weather"},
    "CA": {"customs_hold"},
    "BC": {"documentation_issue"},
    "AG": {"consignee_unavailable"},
    "AJ": {"missed_connection", "capacity_shortage", "other"},  # "Other Carrier Related"
}


class StatusFinding(BaseModel):
    """What the LLM must return for one status report."""
    hawb_number: str = Field(description="The HAWB number exactly as printed")
    delivered_on_time: bool
    delay_days: int | None = Field(description="Days late; null if on time")
    scheduled_delivery: str | None = Field(description="YYYY-MM-DD")
    actual_delivery: str | None = Field(description="YYYY-MM-DD")
    delay_reason: DelayReason | None = Field(description="Category of the delay cause; null if on time")
    evidence_quote: str = Field(description="One sentence copied WORD FOR WORD from the report "
                                            "that states the delivery outcome")
    confidence: float = Field(ge=0, le=1)


SYSTEM_PROMPT = (
    "You extract facts from a logistics shipment status report. "
    "Use only what the report says; do not guess. "
    "For evidence_quote, copy one sentence exactly as written in the report, "
    "including numbers and punctuation. Dates must be YYYY-MM-DD."
)


# ---------------------------------------------------------------------------
# LLM call with a file-hash cache
# ---------------------------------------------------------------------------
def ask_llm(client, text):
    completion = client.chat.completions.parse(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": text}],
        response_format=StatusFinding,
    )
    message = completion.choices[0].message
    if message.refusal:
        raise ValueError(f"LLM refused: {message.refusal}")
    return message.parsed


def cached_finding(client, path, text):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{path.stem}_{digest}.json"
    if cache_file.exists():
        return StatusFinding.model_validate_json(cache_file.read_text(encoding="utf-8")), True
    finding = ask_llm(client, text)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(finding.model_dump_json(indent=2), encoding="utf-8")
    return finding, False


# ---------------------------------------------------------------------------
# Validation gates
# ---------------------------------------------------------------------------
def normalize(text):
    return " ".join(text.split())


def gate_problems(finding, text):
    problems = []
    page = normalize(text)
    quote = normalize(finding.evidence_quote)
    if quote not in page:
        problems.append("evidence quote not found word for word in the report")
    if finding.delay_days is not None and str(finding.delay_days) not in quote:
        problems.append("delay_days value does not appear in the evidence quote")
    if finding.hawb_number not in page:
        problems.append("HAWB number not found in the report")
    if not finding.delivered_on_time and finding.delay_reason is None:
        problems.append("late shipment without a delay reason")
    return problems


def cross_check(finding, edi):
    """Compare the LLM finding with the parsed 214 for the same HAWB."""
    if edi is None:
        return ["no 214 found for this HAWB"]
    conflicts = []
    edi_days = edi["fields"]["delay_days"] or 0
    if (finding.delay_days or 0) != edi_days:
        conflicts.append(f"delay_days: report says {finding.delay_days}, 214 says {edi_days}")
    code = edi["fields"]["delay_reason_code"]
    if code and finding.delay_reason not in REASON_CODE_TO_CATEGORIES.get(code, set()):
        conflicts.append(f"reason: report says {finding.delay_reason}, 214 code is {code}")
    return conflicts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="LLM extraction from status report PDFs.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N reports")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is missing. Add it to the .env file in the project root.")
    client = OpenAI(api_key=OPENAI_API_KEY, max_retries=3, timeout=60)

    edi_by_hawb = {}
    if EDI_PATH.exists():
        for line in EDI_PATH.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            edi_by_hawb[r["references"]["hawb_number"]] = r

    paths = sorted(RAW_DIR.glob("*.pdf"))[: args.limit]
    accepted, review, llm_calls = [], [], 0

    for path in paths:
        doc = pymupdf.open(path)
        text = doc[0].get_text()
        doc.close()

        finding, from_cache = cached_finding(client, path, text)
        llm_calls += 0 if from_cache else 1

        problems = gate_problems(finding, text)
        conflicts = cross_check(finding, edi_by_hawb.get(finding.hawb_number))
        record = {
            "doc_id": path.stem,
            "doc_type": "STATUS_REPORT",
            "path": str(path),
            "references": {"hawb_number": finding.hawb_number},
            "fields": finding.model_dump(exclude={"evidence_quote", "confidence"}),
            "evidence": {"quote": finding.evidence_quote, "page": 1},
            "confidence": finding.confidence,
            "conflicts_with_214": conflicts,
        }
        if problems:
            record["review_reasons"] = problems
            review.append(record)
        else:
            accepted.append(record)

    for out, records in ((OUT_PATH, accepted), (REVIEW_PATH, review)):
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    late = [r for r in accepted if not r["fields"]["delivered_on_time"]]
    print(f"Reports read    : {len(paths)}  (new LLM calls: {llm_calls}, from cache: {len(paths) - llm_calls})")
    print(f"Accepted        : {len(accepted)} -> {OUT_PATH}")
    print(f"Review queue    : {len(review)} -> {REVIEW_PATH}")
    print(f"Late (accepted) : {len(late)}")
    print(f"Conflicts w/ 214: {sum(1 for r in accepted if r['conflicts_with_214'])}")
    for r in late[:3]:
        f = r["fields"]
        print(f"\n  HAWB {f['hawb_number']}: {f['delay_days']} days late, reason={f['delay_reason']}")
        print(f"  quote: \"{r['evidence']['quote']}\"")
    for r in review[:3]:
        print(f"\n  REVIEW {r['doc_id']}: {r['review_reasons']}")


if __name__ == "__main__":
    main()