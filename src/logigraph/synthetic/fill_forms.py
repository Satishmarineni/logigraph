"""Step 2c-2: Fill the blank HAWB and SLI forms from the synthetic shipments.

Reads data/processed/synthetic/shipments.jsonl, fills the PDF form fields
through the field maps, and stamps every page as a synthetic sample.
Output goes to data/raw/hawb/ and data/raw/sli/ (the folders we ingest later).
"""
import argparse
import json
from datetime import date
from pathlib import Path

import pymupdf

from logigraph.forms.field_maps import (
    HAWB_CHECKBOX_GROUPS, HAWB_LINE_ITEM_COLUMNS, HAWB_TEXT_FIELDS, HAWB_TOTAL_FIELDS,
    SLI_CHECKBOX_GROUPS, SLI_DOCUMENT_COLUMN_SPLIT_X, SLI_TEXT_FIELDS,
)

SHIPMENTS_PATH = Path("data/processed/synthetic/shipments.jsonl")
HAWB_TEMPLATE = Path("data/templates/Hawb_2020_logo.pdf")
SLI_TEMPLATE = Path("data/templates/SLI 8-23-23.pdf")
HAWB_OUT = Path("data/raw/hawb")
SLI_OUT = Path("data/raw/sli")

# The HAWB has no form field for the Air Bill number, so it is written
# onto the page at the "MOVEMENT#" line (x, y in PDF points).
AIR_BILL_POSITION = (372, 135)
STAMP = "SYNTHETIC SAMPLE - NOT A REAL SHIPMENT"
RED = (0.85, 0, 0)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def us_date(iso):
    return date.fromisoformat(iso).strftime("%m/%d/%Y")


def name_address(party):
    return (f"{party['name']}\n{party['address']}\n"
            f"{party['city']}, {party['state_or_country']} {party['postal_code']}")


def invert(text_fields):
    """{"Text42": "shipper.name_address"} -> {"shipper.name_address": "Text42"}"""
    return {business: field for field, business in text_fields.items()}


def checkbox_name(groups, group, value):
    """Find the form checkbox for a business value, e.g. ("payment.terms", "collect")."""
    for field_name, v in groups[group].items():
        if v == value:
            return field_name
    raise KeyError(f"No checkbox for {group}={value}")


def fill(doc, text_values, checked, checked_at=None):
    """Set text values and tick checkboxes on a blank template.

    text_values: {form field name: text}  (fields not listed stay blank)
    checked:     set of checkbox field names to tick
    checked_at:  set of (field name, x0) for checkboxes that share a name
    """
    checked_at = checked_at or set()
    for page in doc:
        for w in page.widgets():
            kind = w.field_type_string
            if kind == "Text" and w.field_name in text_values:
                w.field_value = text_values[w.field_name]
                # 4096 = multiline flag; 0 = auto-fit the text to a one-line box
                w.text_fontsize = 7 if w.field_flags & 4096 else 0
                w.text_color = (0, 0, 0)
                w.update()
            elif kind == "CheckBox":
                tick = (w.field_name in checked
                        or (w.field_name, round(w.rect.x0)) in checked_at)
                w.field_value = w.on_state() if tick else "Off"
                w.update()


def stamp(doc):
    """Mark every page so the document can never pass for a real one."""
    for page in doc:
        page.insert_text((150, 788), STAMP, fontsize=9, color=RED)
        r = page.rect
        center = pymupdf.Point(r.width / 2, r.height / 2)
        page.insert_text(
            (center.x - 230, center.y + 40), STAMP, fontsize=28, color=RED,
            fill_opacity=0.18, morph=(center, pymupdf.Matrix(-35)),
        )


# ---------------------------------------------------------------------------
# HAWB
# ---------------------------------------------------------------------------
def hawb_values(s):
    f = invert(HAWB_TEXT_FIELDS)
    sid = s["shipment_id"][-6:]
    v = {
        f["shipment.date"]: us_date(s["ship_date"]),
        f["shipment.origin"]: s["origin_code"],
        f["shipment.destination"]: s["destination_code"],
        f["shipper.name_address"]: name_address(s["shipper"]),
        f["shipper.account_number"]: s["shipper"]["account_number"],
        f["shipper.contact_ein"]: s["shipper"]["contact"],
        f["shipper.phone"]: s["shipper"]["phone"],
        f["shipper.po_number"]: f"PO-{sid}",
        f["shipper.bol_number"]: f"BOL-{sid}",
        f["consignee.name_address"]: name_address(s["consignee"]),
        f["consignee.account_number"]: s["consignee"]["account_number"],
        f["consignee.contact_broker"]: s["consignee"]["contact"],
        f["consignee.phone"]: s["consignee"]["phone"],
        f["value.customs"]: f"{s['declared_value_usd']:,}",
        f["value.declared"]: f"{s['declared_value_usd']:,}",
    }
    if s["insurance_usd"]:
        v[f["value.insurance_amount"]] = f"{s['insurance_usd']:,}"
    if s["payment_terms"] == "cod":
        v[f["value.cod_amount"]] = f"{s['declared_value_usd']:,}"
    if s["dangerous_goods_un"]:
        v[f["dangerous_goods.un_number"]] = s["dangerous_goods_un"]
        v[f["dangerous_goods.emergency_contact"]] = s["shipper"]["phone"]
        v[f["shipment.special_instructions"]] = (
            "Contains lithium-ion batteries. Shipper's DG declaration attached.")

    cols = HAWB_LINE_ITEM_COLUMNS
    for row, item in enumerate(s["line_items"][:9]):
        v[cols["pieces"][row]] = str(item["pieces"])
        v[cols["gross_weight"][row]] = f"{item['gross_weight_kg']} kg"
        v[cols["description"][row]] = item["description"]
        v[cols["schedule_b"][row]] = item["schedule_b"]
        v[cols["dimensions"][row]] = f"{item['dimensions_in']} in"

    totals = invert(HAWB_TOTAL_FIELDS)
    v[totals["totals.pieces"]] = str(sum(i["pieces"] for i in s["line_items"]))
    v[totals["totals.gross_weight"]] = f"{sum(i['gross_weight_kg'] for i in s['line_items']):.1f} kg"
    return v


def hawb_checked(s):
    column, level = s["service_level"].split(".")          # e.g. "intl_air", "door_to_door"
    checked = {
        checkbox_name(HAWB_CHECKBOX_GROUPS, f"service.{column}", level),
        checkbox_name(HAWB_CHECKBOX_GROUPS, "payment.terms", s["payment_terms"]),
    }
    if s["dangerous_goods_un"]:
        checked.add(checkbox_name(HAWB_CHECKBOX_GROUPS, "dangerous_goods.present", "yes"))
    return checked


def make_hawb(s):
    doc = pymupdf.open(HAWB_TEMPLATE)
    fill(doc, hawb_values(s), hawb_checked(s))
    doc[0].insert_text(AIR_BILL_POSITION, s["hawb_number"], fontsize=11, color=(0, 0, 0))
    stamp(doc)
    out = HAWB_OUT / f"HAWB_{s['hawb_number']}.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


# ---------------------------------------------------------------------------
# SLI
# ---------------------------------------------------------------------------
def sli_values(s):
    f = invert(SLI_TEXT_FIELDS)
    items = s["line_items"]
    shipper = s["shipper"]
    initials = "".join(part[0] for part in shipper["contact"].split()[:2]).upper()
    v = {
        f["sli.date"]: us_date(s["ship_date"]),
        f["sli.quote_number"]: s["sli_quote_number"],
        f["usppi.name_address"]: name_address(shipper),
        f["usppi.zip_code"]: shipper["postal_code"],
        f["usppi.ein"]: f"00-{s['shipment_id'][-6:]}0",   # "00" prefix: never a real EIN
        f["ultimate_consignee.name_address"]: name_address(s["consignee"]),
        f["shipment.shipper_reference"]: s["shipment_id"],
        f["shipment.consignee_po"]: f"PO-{s['shipment_id'][-6:]}",
        f["shipment.origin_state"]: shipper["state_or_country"],
        f["shipment.destination_country"]: s["destination_country"],
        f["value.declared"]: f"{s['declared_value_usd']:,}",
        f["payment.location"]: "Origin" if s["payment_terms"] == "prepaid" else "Destination",
        f["commodity.domestic_foreign"]: "\n".join("D" for _ in items),
        f["commodity.schedule_b"]: "\n".join(f"{i['schedule_b']}  {i['description']}" for i in items),
        f["commodity.license"]: "\n".join(s["license"] for _ in items),
        f["commodity.eccn"]: "\n".join(s["eccn"] for _ in items),
        f["commodity.quantity"]: "\n".join(f"{i['pieces']} PCS" for i in items),
        f["commodity.weight_kg"]: "\n".join(str(i["gross_weight_kg"]) for i in items),
        f["commodity.value_usd"]: "\n".join(str(i["value_usd"]) for i in items),
        f["shipment.pieces_dimensions"]: "\n".join(
            f"{i['pieces']} pcs @ {i['dimensions_in']} in - {i['description']}" for i in items),
        f["signatory.name"]: shipper["contact"],
        f["signatory.title"]: "Export Manager",
        f["signatory.date"]: us_date(s["ship_date"]),
        f["pickup.date"]: us_date(s["ship_date"]),
    }
    if s["insurance_usd"]:
        v[f["value.insurance_amount"]] = f"{s['insurance_usd']:,}"
    if s["mode"] in ("Air", "Air Charter"):
        v[f["service.air"]] = "X"
    elif s["mode"] == "Ocean":
        v[f["service.ocean"]] = "X"
    if s["dangerous_goods_un"]:
        v[f["dangerous_goods.yes_initials"]] = initials
        v[f["shipment.special_instructions"]] = (
            f"Dangerous goods: {s['dangerous_goods_un']} lithium-ion batteries. DG declaration attached.")
    else:
        v[f["dangerous_goods.no_initials"]] = initials
    return v


def sli_checked(s):
    g = SLI_CHECKBOX_GROUPS
    checked = {
        checkbox_name(g, "parties.related", "non_related"),
        checkbox_name(g, "consignee.type", s["consignee_type"]),
        checkbox_name(g, "shipment.routed_export", "yes" if s["routed_export"] else "no"),
        checkbox_name(g, "pickup.received_at", "shipper_door"),
        "COMMERCIAL INVOICE",
    }
    if s["incoterm"] in g["shipment.incoterm"]:
        checked.add(checkbox_name(g, "shipment.incoterm", s["incoterm"]))
    if s["mode"] == "Air":
        checked.add(checkbox_name(g, "service.air_level", "value"))
    elif s["mode"] == "Ocean":
        checked.add(checkbox_name(g, "service.ocean_level", "lcl"))
    if s["dangerous_goods_un"]:
        checked.add("DG DECLARATION")
    return checked


def make_sli(s):
    doc = pymupdf.open(SLI_TEMPLATE)
    # "Certificate of origin" exists in both document columns with the same name;
    # tick only the "To Be Prepared" one (right column, x >= split).
    checked_at = set()
    for w in doc[0].widgets():
        if w.field_name == "CERTIFICATE OF ORIGIN" and w.rect.x0 >= SLI_DOCUMENT_COLUMN_SPLIT_X:
            checked_at.add((w.field_name, round(w.rect.x0)))
    fill(doc, sli_values(s), sli_checked(s), checked_at)
    stamp(doc)
    out = SLI_OUT / f"SLI_{s['sli_quote_number']}.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


def main():
    parser = argparse.ArgumentParser(description="Fill HAWB and SLI forms from synthetic shipments.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N shipments")
    args = parser.parse_args()

    HAWB_OUT.mkdir(parents=True, exist_ok=True)
    SLI_OUT.mkdir(parents=True, exist_ok=True)

    lines = SHIPMENTS_PATH.read_text(encoding="utf-8").splitlines()
    shipments = [json.loads(line) for line in lines][: args.limit]

    for n, s in enumerate(shipments, start=1):
        make_hawb(s)
        make_sli(s)
        if n % 50 == 0 or n == len(shipments):
            print(f"  {n}/{len(shipments)} shipments done")

    print(f"HAWBs: {len(list(HAWB_OUT.glob('*.pdf')))} in {HAWB_OUT}")
    print(f"SLIs : {len(list(SLI_OUT.glob('*.pdf')))} in {SLI_OUT}")


if __name__ == "__main__":
    main()