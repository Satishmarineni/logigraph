"""Step 2c-3: Write an X12 214 (Shipment Status) file and a readable status
report PDF for each synthetic shipment.

The 214 follows X12 version 004010. The ISA test indicator is "T", so every
interchange is marked as test data. Status and reason codes are standard
X12 codes (element 1650 for AT701, element 1651 for AT702).
"""
import argparse
import json
from datetime import date
from pathlib import Path

import pymupdf

from logigraph.synthetic.generator import DESTINATIONS, OCEAN_PORTS, US_ORIGINS

SHIPMENTS_PATH = Path("data/processed/synthetic/shipments.jsonl")
EDI_OUT = Path("data/raw/edi214")
PDF_OUT = Path("data/raw/status")

SENDER_ID = "SYNCARRIER"      # fictional carrier
RECEIVER_ID = "LOGIGRAPH"
SCAC = "SYNC"                 # fictional Standard Carrier Alpha Code
CARRIER_NAME = "Synthetic Freight Lines (SYNTHETIC)"
STAMP = "SYNTHETIC SAMPLE - NOT A REAL SHIPMENT"

# AT701 - Shipment Status Indicator Code (X12 element 1650)
STATUS_CODES = {
    "picked_up": ("AF", "Carrier Departed Pickup Location with Shipment"),
    "departed":  ("P1", "Departed Terminal Location"),
    "estimated": ("AG", "Estimated Delivery"),
    "delayed":   ("SD", "Shipment Delayed"),
    "arrived":   ("X4", "Arrived at Terminal Location"),
    "delivered": ("D1", "Completed Unloading at Delivery Location"),
}

# AT702 - Shipment Status or Appointment Reason Code (X12 element 1651)
# X12 has no exact code for a missed flight connection or a capacity
# shortage, so those use "Other Carrier Related" and the readable report
# carries the specific reason.
REASON_CODES = {
    None:                    ("NS", "Normal Status"),
    "weather":               ("AO", "Weather or Natural Disaster Related"),
    "customs_hold":          ("CA", "Customs (Import or Export)"),
    "documentation_issue":   ("BC", "Missing Documents"),
    "consignee_unavailable": ("AG", "Consignee Related"),
    "missed_connection":     ("AJ", "Other Carrier Related"),
    "capacity_shortage":     ("AJ", "Other Carrier Related"),
}
REASON_TEXT = {
    "weather": "weather conditions",
    "customs_hold": "a customs hold",
    "documentation_issue": "missing documents",
    "consignee_unavailable": "the consignee being unavailable",
    "missed_connection": "a missed connection",
    "capacity_shortage": "a capacity shortage",
}

# Location code -> (city, state or "", country code)
LOCATIONS = {code: (city, state, "US") for code, city, state in US_ORIGINS}
COUNTRY_CODES = {"Germany": "DE", "United Kingdom": "GB", "Japan": "JP", "India": "IN",
                 "Brazil": "BR", "Mexico": "MX", "Netherlands": "NL", "Singapore": "SG",
                 "United Arab Emirates": "AE", "Australia": "AU"}
LOCATIONS.update({code: (city, "", COUNTRY_CODES[country]) for code, city, country, _ in DESTINATIONS})
for airport, port in OCEAN_PORTS.items():
    LOCATIONS[port] = LOCATIONS[airport]


def location(code_or_city):
    """MS1 values for a location code or a city name."""
    if code_or_city in LOCATIONS:
        return LOCATIONS[code_or_city]
    for city, state, country in LOCATIONS.values():
        if city == code_or_city:
            return city, state, country
    return code_or_city, "", ""


def clean(text):
    """Remove X12 delimiters (* ~ >) from free text so they cannot break the file."""
    return text.replace("*", " ").replace("~", " ").replace(">", " ")


def yymmdd(iso):
    return date.fromisoformat(iso).strftime("%y%m%d")


def ccyymmdd(iso):
    return date.fromisoformat(iso).strftime("%Y%m%d")


def milestones_with_estimate(s):
    """Add an "estimated delivery" (AG) milestone with the ORIGINAL scheduled date,
    right after departure, so delay = delivered date - estimated date."""
    estimate = {"status": "estimated", "date": s["scheduled_delivery"],
                "location": s["destination_code"], "reason": None}
    m = s["milestones"]
    return m[:2] + [estimate] + m[2:]


# ---------------------------------------------------------------------------
# X12 214
# ---------------------------------------------------------------------------
def build_214(s, control_number):
    ctrl = f"{control_number:09d}"
    sent = s["milestones"][-1]["date"]
    weight = sum(i["gross_weight_kg"] for i in s["line_items"])
    pieces = sum(i["pieces"] for i in s["line_items"])

    body = [
        "ST*214*0001",
        f"B10*{s['hawb_number']}*{s['shipment_id']}*{SCAC}",
        f"L11*PO-{s['shipment_id'][-6:]}*PO",
        f"N1*SH*{clean(s['shipper']['name'])}",
        f"N1*CN*{clean(s['consignee']['name'])}",
    ]
    for n, m in enumerate(milestones_with_estimate(s), start=1):
        status_code, _ = STATUS_CODES[m["status"]]
        reason_code, _ = REASON_CODES[m["reason"]]
        city, state, country = location(m["location"])
        body += [
            f"LX*{n}",
            f"AT7*{status_code}*{reason_code}***{ccyymmdd(m['date'])}*0800*LT",
            f"MS1*{clean(city)}*{state}*{country}",
            f"AT8*G*K*{weight:.1f}*{pieces}",
        ]
    body.append(f"SE*{len(body) + 1}*0001")

    envelope = [
        f"ISA*00*{' ' * 10}*00*{' ' * 10}*ZZ*{SENDER_ID:<15}*ZZ*{RECEIVER_ID:<15}"
        f"*{yymmdd(sent)}*0800*U*00401*{ctrl}*0*T*>",
        f"GS*QM*{SENDER_ID}*{RECEIVER_ID}*{ccyymmdd(sent)}*0800*{control_number}*X*004010",
        *body,
        f"GE*1*{control_number}",
        f"IEA*1*{ctrl}",
    ]
    return "~\n".join(envelope) + "~\n"


# ---------------------------------------------------------------------------
# Readable status report (PDF)
# ---------------------------------------------------------------------------
def status_lines(s):
    lines = [
        "SHIPMENT STATUS REPORT",
        f"Carrier: {CARRIER_NAME}    SCAC: {SCAC}",
        "",
        f"HAWB: {s['hawb_number']}    Shipment ID: {s['shipment_id']}    Mode: {s['mode']}",
        f"Route: {s['origin_code']} to {s['destination_code']} ({s['destination_country']})",
        f"Shipper: {s['shipper']['name']}",
        f"Consignee: {s['consignee']['name']}, {s['consignee']['city']}",
        f"Scheduled delivery: {s['scheduled_delivery']}    Actual delivery: {s['actual_delivery']}",
        "",
        "Milestones (X12 214 status and reason codes):",
    ]
    for m in milestones_with_estimate(s):
        sc, sd = STATUS_CODES[m["status"]]
        rc, rd = REASON_CODES[m["reason"]]
        lines.append(f"  {m['date']}  {m['location']:<12} {sc} {sd}  |  {rc} {rd}")
    lines.append("")
    if s["delay_days"]:
        lines.append(
            f"Summary: Shipment {s['hawb_number']} was delivered {s['delay_days']} days late, "
            f"on {s['actual_delivery']} instead of {s['scheduled_delivery']}.")
        lines.append(f"The delay was caused by {REASON_TEXT[s['delay_reason']]}.")
    else:
        lines.append(f"Summary: Shipment {s['hawb_number']} was delivered on time "
                     f"on {s['actual_delivery']}.")
    return lines


def build_pdf(s, path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    for i, line in enumerate(status_lines(s)):
        milestone = line.startswith("  ")
        size = 14 if i == 0 else (8 if milestone else 9.5)
        page.insert_text((50, y), line, fontsize=size, fontname="cour" if milestone else "helv")
        y += 20 if i == 0 else 14
    red = (0.85, 0, 0)
    page.insert_text((150, 770), STAMP, fontsize=9, color=red)
    page.insert_text((120, 520), STAMP, fontsize=26, color=red, fill_opacity=0.18,
                     morph=(pymupdf.Point(306, 396), pymupdf.Matrix(-35)))
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def main():
    parser = argparse.ArgumentParser(description="Write X12 214 files and status reports.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N shipments")
    args = parser.parse_args()

    EDI_OUT.mkdir(parents=True, exist_ok=True)
    PDF_OUT.mkdir(parents=True, exist_ok=True)

    lines = SHIPMENTS_PATH.read_text(encoding="utf-8").splitlines()
    shipments = [json.loads(line) for line in lines][: args.limit]

    for n, s in enumerate(shipments, start=1):
        (EDI_OUT / f"214_{s['hawb_number']}.edi").write_text(build_214(s, n), encoding="utf-8")
        build_pdf(s, PDF_OUT / f"STATUS_{s['hawb_number']}.pdf")

    late = sum(1 for s in shipments if s["delay_days"])
    print(f"214 files     : {len(shipments)} in {EDI_OUT}")
    print(f"Status reports: {len(shipments)} in {PDF_OUT}")
    print(f"Delayed shipments covered: {late}")


if __name__ == "__main__":
    main()