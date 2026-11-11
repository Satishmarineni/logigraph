"""Step 5.3a: Turn the documents into text chunks for the vector index.

What gets chunked:
  1. HAWB terms and conditions (page 2) - split by clause ("8. Limitation of
     Liability..."), long clauses packed into ~1,200-character pieces at
     sentence boundaries. The page is the same on every HAWB, so it is chunked once.
  2. Status reports - one chunk each (they are short).
  3. HAWB and SLI forms - their text layer only holds printed labels; the
     values live in form fields. So each filled form is rendered as a short
     plain-English text from the extracted record (Step 4.1).
Every chunk starts with a [context] header (document type, clause, HAWB), so
a chunk still makes sense on its own. Chunk IDs use the same doc_id as the
Document nodes in Neo4j, which lets graph facts and text passages join.
"""
import json
import re
from pathlib import Path

import pymupdf

RAW_DIR = Path("data/raw")
EXTRACTED = Path("data/processed/extracted")
OUT_PATH = Path("data/processed/chunks.jsonl")
MAX_CHARS = 1200
STAMP = "SYNTHETIC SAMPLE - NOT A REAL SHIPMENT"

# "8. Limitation of Liability Per Shipment. (a) In connection..."
# Also handles a title that starts with an acronym ("14. C.O.D. Shipments.")
# and one that ends with a colon ("28. Quotations not Binding:").
CLAUSE_START = re.compile(r"^\s*(\d{1,2})\.\s+((?:[A-Z]\.)+\s)?([A-Z][^.:]{2,80})[.:]\s*(.*)$")


def clean(text):
    return text.replace(STAMP, " ").strip()


def pack_sentences(text, limit=MAX_CHARS):
    """Split text into pieces of at most `limit` characters, at sentence ends."""
    sentences = re.split(r"(?<=[.;])\s+", " ".join(text.split()))
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


# ---------------------------------------------------------------------------
# 1. Terms and conditions (HAWB page 2)
# ---------------------------------------------------------------------------
def terms_chunks(hawb_pdf):
    doc = pymupdf.open(hawb_pdf)
    lines = clean(doc[1].get_text()).splitlines()
    doc.close()

    clauses, number, title, body = [], "0", "Introduction", []
    for line in lines:
        match = CLAUSE_START.match(line)
        if match:
            clauses.append((number, title, body))
            number = match.group(1)
            title = f"{match.group(2) or ''}{match.group(3)}".strip()
            body = [match.group(4)]
        else:
            body.append(line)
    clauses.append((number, title, body))

    chunks = []
    for number, title, body in clauses:
        text = " ".join(body)
        if len(text.strip()) < 40:
            continue
        for i, piece in enumerate(pack_sentences(text), start=1):
            chunks.append({
                "chunk_id": f"TERMS#p2#c{number}-{i}",
                "doc_id": "HAWB_TERMS",
                "doc_type": "TERMS",
                "page": 2,
                "clause": int(number),
                "text": f"[HAWB Terms and Conditions, clause {number}: {title}] {piece}",
            })
    return chunks


# ---------------------------------------------------------------------------
# 2. Status reports
# ---------------------------------------------------------------------------
def status_chunks():
    chunks = []
    for pdf in sorted((RAW_DIR / "status").glob("*.pdf")):
        doc = pymupdf.open(pdf)
        text = " ".join(clean(doc[0].get_text()).split())
        doc.close()
        hawb = pdf.stem.removeprefix("STATUS_")
        chunks.append({"chunk_id": f"{pdf.stem}#p1#c1", "doc_id": pdf.stem,
                       "doc_type": "STATUS_REPORT", "page": 1, "hawb_number": hawb,
                       "text": f"[Shipment status report, HAWB {hawb}] {text}"})
    return chunks


# ---------------------------------------------------------------------------
# 3. HAWB and SLI forms, rendered from the extracted records
# ---------------------------------------------------------------------------
def hawb_text(r):
    f = r["fields"]
    items = "; ".join(f"{i['pieces']} pcs {i['description']} (Schedule B {i['schedule_b']}), "
                      f"{i['gross_weight_kg']} kg, {i['dimensions_in']} in" for i in r["line_items"])
    return (f"House air waybill {r['references']['hawb_number']}, PO {r['references']['po_number']}. "
            f"Ship date {f['ship_date']}. Mode {f['mode']}, service {f['service_level']}. "
            f"From {f['origin_code']} to {f['destination_code']}. "
            f"Shipper: {f['shipper'].get('name')}, {f['shipper'].get('city')}, {f['shipper'].get('state_or_country')}. "
            f"Consignee: {f['consignee'].get('name')}, {f['consignee'].get('city')}, "
            f"{f['consignee'].get('state_or_country')}. Payment terms: {f['payment_terms']}. "
            f"Declared value USD {f['declared_value_usd']}. Insurance USD {f['insurance_usd']}. "
            f"Dangerous goods: {f['dangerous_goods_un'] or 'none'}. Line items: {items}.")


def sli_text(r):
    f = r["fields"]
    items = "; ".join(f"{c['schedule_b']} {c['description']}, ECCN {c['eccn']}, license {c['license']}, "
                      f"value USD {c['value_usd']}" for c in r["line_items"])
    return (f"Shipper's letter of instructions {r['references']['quote_number']} for shipment "
            f"{r['references']['shipment_id']}, PO {r['references']['po_number']}. Date {f['sli_date']}. "
            f"Exporter (USPPI): {f['usppi'].get('name')}, EIN {f['usppi'].get('ein')}. "
            f"Ultimate consignee: {(f['ultimate_consignee'] or {}).get('name')}, "
            f"{(f['ultimate_consignee'] or {}).get('state_or_country')}; consignee type {f['consignee_type']}. "
            f"Incoterm {f['incoterm']}. Routed export: {'yes' if f['routed_export'] else 'no'}. "
            f"Destination country {f['destination_country']}. Requested mode {f['requested_mode']}. "
            f"Commodities: {items}. Documents attached: {', '.join(f['documents_attached']) or 'none'}.")


def form_chunks():
    chunks = []
    for name, render, label in (("hawb", hawb_text, "HAWB form"), ("sli", sli_text, "SLI form")):
        for line in (EXTRACTED / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            chunks.append({"chunk_id": f"{r['doc_id']}#p1#c1", "doc_id": r["doc_id"],
                           "doc_type": r["doc_type"], "page": 1,
                           "hawb_number": r["references"].get("hawb_number"),
                           "text": f"[{label}, {r['doc_id']}] {render(r)}"})
    return chunks


def build_chunks():
    first_hawb = next(iter(sorted((RAW_DIR / "hawb").glob("*.pdf"))))
    return terms_chunks(first_hawb) + status_chunks() + form_chunks()


def main():
    chunks = build_chunks()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    by_type = {}
    for c in chunks:
        by_type[c["doc_type"]] = by_type.get(c["doc_type"], 0) + 1
    lengths = [len(c["text"]) for c in chunks]
    print(f"Chunks: {len(chunks)} -> {OUT_PATH}")
    for t, n in by_type.items():
        print(f"  {t:<14} {n:>4}")
    print(f"Length: min {min(lengths)}, max {max(lengths)}, avg {sum(lengths) // len(lengths)} characters")
    example = next(c for c in chunks if c.get("clause") == 8)
    print(f"\nExample: {example['chunk_id']}\n  {example['text'][:220]}...")


if __name__ == "__main__":
    main()