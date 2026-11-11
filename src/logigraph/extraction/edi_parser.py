"""Step 4.2: Parse X12 214 (Shipment Status) files into structured records.

Technique: deterministic EDI parsing.
  1. Read the delimiters from the ISA header (it has a fixed length of 106
     characters, so the separators always sit at the same positions).
  2. Split into segments and elements.
  3. Validate the envelope (control numbers and segment count must match).
  4. Read B10, L11, N1 and each LX loop (AT7 status, MS1 location, AT8 weight).
  5. Derive the delay: delivered date (D1) minus estimated delivery (AG).
"""
import argparse
import json
from datetime import date
from pathlib import Path

from logigraph.forms.x12_codes import (
    DELAYED, DELIVERED, ESTIMATED_DELIVERY, REASON_CODE_NAMES, STATUS_CODE_NAMES,
)

RAW_DIR = Path("data/raw/edi214")
OUT_PATH = Path("data/processed/extracted/edi214.jsonl")
TRUTH_PATH = Path("data/processed/synthetic/shipments.jsonl")


class EDIError(ValueError):
    """Raised when a file is not a valid X12 interchange."""


# ---------------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------------
def split_segments(raw):
    """Use the ISA header to find the delimiters, then split the file."""
    raw = raw.replace("\r", "").replace("\n", "")
    if not raw.startswith("ISA") or len(raw) < 106:
        raise EDIError("File does not start with a complete ISA header")
    element_sep = raw[3]        # character right after "ISA"
    sub_element_sep = raw[104]  # ISA16
    segment_term = raw[105]     # character right after ISA16
    segments = [s.split(element_sep) for s in raw.split(segment_term) if s.strip()]
    return segments, {"element": element_sep, "sub_element": sub_element_sep,
                      "segment": segment_term}


def validate_envelope(segments):
    """Check the control numbers and the ST/SE segment count. Returns a list of problems."""
    problems = []
    by_id = {}
    for seg in segments:
        by_id.setdefault(seg[0], []).append(seg)
    isa, iea = by_id.get("ISA", [[]])[0], by_id.get("IEA", [[]])[0]
    gs, ge = by_id.get("GS", [[]])[0], by_id.get("GE", [[]])[0]
    if not (isa and iea and isa[13] == iea[2]):
        problems.append("ISA13 does not match IEA02")
    if not (gs and ge and gs[6] == ge[2]):
        problems.append("GS06 does not match GE02")
    if gs and gs[1] != "QM":
        problems.append(f"GS01 is {gs[1]!r}, expected 'QM' (shipment status)")

    st_index = next((i for i, s in enumerate(segments) if s[0] == "ST"), None)
    se_index = next((i for i, s in enumerate(segments) if s[0] == "SE"), None)
    if st_index is None or se_index is None:
        problems.append("Missing ST or SE")
    else:
        counted = se_index - st_index + 1
        se = segments[se_index]
        if int(se[1]) != counted:
            problems.append(f"SE01 says {se[1]} segments, counted {counted}")
        if se[2] != segments[st_index][2]:
            problems.append("SE02 does not match ST02")
    return problems


def x12_date(ccyymmdd):
    return date(int(ccyymmdd[:4]), int(ccyymmdd[4:6]), int(ccyymmdd[6:8])).isoformat()


def element(seg, n):
    """Safe element access: element(seg, 1) is the first data element."""
    return seg[n] if len(seg) > n and seg[n] != "" else None


# ---------------------------------------------------------------------------
# Parsing a 214
# ---------------------------------------------------------------------------
def parse_214(path):
    segments, delimiters = split_segments(path.read_text(encoding="utf-8"))
    problems = validate_envelope(segments)

    isa = segments[0]
    record = {
        "doc_id": path.stem,
        "doc_type": "EDI214",
        "path": str(path),
        "references": {},
        "fields": {
            "test_data": element(isa, 15) == "T",
            "sender_id": (element(isa, 6) or "").strip(),
            "shipper_name": None,
            "consignee_name": None,
            "carrier_scac": None,
            "events": [],
        },
        "envelope_problems": problems,
        "evidence": {},
    }
    refs, fields, evidence = record["references"], record["fields"], record["evidence"]

    event = None
    for seg in segments:
        tag = seg[0]
        if tag == "B10":
            refs["hawb_number"] = element(seg, 1)
            refs["shipment_id"] = element(seg, 2)
            fields["carrier_scac"] = element(seg, 3)
            evidence.update({"hawb_number": "B10-01", "shipment_id": "B10-02",
                             "carrier_scac": "B10-03"})
        elif tag == "L11" and element(seg, 2) == "PO":
            refs["po_number"] = element(seg, 1)
            evidence["po_number"] = "L11-01 (qualifier PO)"
        elif tag == "N1":
            if element(seg, 1) == "SH":
                fields["shipper_name"] = element(seg, 2)
                evidence["shipper_name"] = "N1*SH"
            elif element(seg, 1) == "CN":
                fields["consignee_name"] = element(seg, 2)
                evidence["consignee_name"] = "N1*CN"
        elif tag == "LX":
            event = {"sequence": int(element(seg, 1))}
            fields["events"].append(event)
        elif tag == "AT7" and event is not None:
            status, reason = element(seg, 1), element(seg, 2)
            event.update({
                "status_code": status,
                "status": STATUS_CODE_NAMES.get(status, "Unknown status code"),
                "reason_code": reason,
                "reason": REASON_CODE_NAMES.get(reason, "Unknown reason code"),
                "date": x12_date(element(seg, 5)) if element(seg, 5) else None,
            })
        elif tag == "MS1" and event is not None:
            event["location"] = {"city": element(seg, 1), "state": element(seg, 2),
                                 "country": element(seg, 3)}
        elif tag == "AT8" and event is not None:
            event["weight_kg"] = float(element(seg, 3)) if element(seg, 3) else None
            event["pieces"] = int(element(seg, 4)) if element(seg, 4) else None

    # Derived facts: estimated vs actual delivery, delay and its reason.
    events = fields["events"]
    estimated = next((e["date"] for e in events if e.get("status_code") == ESTIMATED_DELIVERY), None)
    delivered = next((e["date"] for e in events if e.get("status_code") in DELIVERED), None)
    delay_event = next((e for e in events if e.get("status_code") == DELAYED), None)
    fields["estimated_delivery"] = estimated
    fields["actual_delivery"] = delivered
    fields["delay_days"] = (
        max(0, (date.fromisoformat(delivered) - date.fromisoformat(estimated)).days)
        if estimated and delivered else None)
    fields["delay_reason_code"] = delay_event["reason_code"] if delay_event else None
    evidence["delay_days"] = "AT7 D1 date minus AT7 AG date"
    return record


# ---------------------------------------------------------------------------
# Check against the ground truth (testing only)
# ---------------------------------------------------------------------------
def check(records):
    from logigraph.synthetic.edi214 import REASON_CODES  # reason -> code used by the generator

    truth = {t["hawb_number"]: t for t in
             (json.loads(line) for line in TRUTH_PATH.read_text(encoding="utf-8").splitlines())}
    tests = {}
    for r in records:
        t = truth.get(r["references"].get("hawb_number"))
        if not t:
            tests.setdefault("hawb_number", []).append(False)
            continue
        f = r["fields"]
        want_reason = REASON_CODES[t["delay_reason"]][0] if t["delay_reason"] else None
        for name, got, want in [
            ("hawb_number", True, True),
            ("shipment_id", r["references"].get("shipment_id"), t["shipment_id"]),
            ("envelope valid", r["envelope_problems"], []),
            ("estimated delivery", f["estimated_delivery"], t["scheduled_delivery"]),
            ("actual delivery", f["actual_delivery"], t["actual_delivery"]),
            ("delay_days", f["delay_days"], t["delay_days"]),
            ("delay reason code", f["delay_reason_code"], want_reason),
        ]:
            tests.setdefault(name, []).append(got == want)
    print("\nAccuracy against ground truth:")
    for name, results in tests.items():
        print(f"  {name:<20} {sum(results):>3}/{len(results)}")


def main():
    parser = argparse.ArgumentParser(description="Parse X12 214 files.")
    parser.add_argument("--check", action="store_true", help="compare with the ground truth")
    args = parser.parse_args()

    records, failed = [], []
    for path in sorted(RAW_DIR.glob("*.edi")):
        try:
            records.append(parse_214(path))
        except EDIError as e:
            failed.append((path.name, str(e)))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    delayed = [r for r in records if r["fields"]["delay_days"]]
    print(f"214 files parsed: {len(records)} -> {OUT_PATH}")
    print(f"Failed to parse : {len(failed)}")
    print(f"Envelope errors : {sum(1 for r in records if r['envelope_problems'])}")
    print(f"Delayed         : {len(delayed)}")
    if delayed:
        r = delayed[0]
        code = r["fields"]["delay_reason_code"]
        print(f"\nExample: HAWB {r['references']['hawb_number']} estimated "
              f"{r['fields']['estimated_delivery']}, delivered {r['fields']['actual_delivery']} "
              f"-> {r['fields']['delay_days']} days late, reason {code} ({REASON_CODE_NAMES[code]})")

    if args.check:
        check(records)


if __name__ == "__main__":
    main()