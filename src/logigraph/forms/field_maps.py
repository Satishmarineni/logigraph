"""Field maps: PDF form field name -> business field name.

Like an EDI mapping spec: each form's internal field names are mapped to
clear business names in a `group.attribute` pattern. The groups line up with
the ontology classes (shipper, consignee, commodity, ...).
"""

# ======================================================================
# HAWB  (CEVA Transport Document, page 1)
# Field names are generic (Text41...), so they were mapped from the
# labeled page image. NOTE: there is NO form field for the Air Bill #;
# it is written onto the page as text instead.
# ======================================================================

HAWB_TEXT_FIELDS = {
    # Header
    "Text54": "shipment.date",
    "Text55": "shipment.origin",
    "Text56": "shipment.destination",
    # Shipper
    "Text42": "shipper.name_address",
    "Text41": "shipper.account_number",
    "Text43": "shipper.contact_ein",
    "Text44": "shipper.phone",
    "Text45": "shipper.po_number",
    "Text46": "shipper.bol_number",
    # Consignee
    "Text48": "consignee.name_address",
    "Text47": "consignee.account_number",
    "Text49": "consignee.contact_broker",
    "Text50": "consignee.phone",
    "Text51": "consignee.po_number",
    "Text52": "consignee.bol_number",
    # Billing (if other than shipper or consignee)
    "Text1": "bill_to.address",
    "Text53": "bill_to.account_number",
    # Service, values, instructions
    "Text57": "service.specific_date",
    "Text58": "value.customs",
    "Text59": "value.declared",
    "Text60": "value.cod_amount",
    "Text61": "value.insurance_amount",
    "Text62": "shipment.special_instructions",
    # Dangerous goods
    "Text114": "dangerous_goods.un_number",
    "Text115": "dangerous_goods.emergency_contact",
}

# Line items: 9 rows x 5 columns. Text65 does not exist in the form,
# so row 2 of "pieces" is Text64 and row 3 is Text66.
HAWB_LINE_ITEM_COLUMNS = {
    "pieces":       ["Text63", "Text64", "Text66", "Text67", "Text68",
                     "Text69", "Text70", "Text71", "Text72"],
    "gross_weight": ["Text74", "Text75", "Text76", "Text77", "Text78",
                     "Text79", "Text80", "Text81", "Text82"],
    "description":  ["Text84", "Text85", "Text86", "Text87", "Text88",
                     "Text89", "Text90", "Text91", "Text92"],
    "schedule_b":   ["Text94", "Text95", "Text96", "Text97", "Text98",
                     "Text99", "Text100", "Text101", "Text102"],
    "dimensions":   ["Text104", "Text105", "Text106", "Text107", "Text108",
                     "Text109", "Text110", "Text111", "Text112"],
}

HAWB_TOTAL_FIELDS = {
    "Text73": "totals.pieces",
    "Text83": "totals.gross_weight",
    "Text93": "totals.description",
    "Text103": "totals.schedule_b",
    "Text113": "totals.dimensions",
}

# Checkbox groups, mapped by position (column x, row y) to the printed labels.
# Service columns can have several boxes ticked (e.g. LIFTGATE + INSIDE),
# so each group is a list of selected values, not a single value.
HAWB_CHECKBOX_GROUPS = {
    # Column at x=270: DOMESTIC/TRANSBORDER
    "service.domestic": {
        "Check Box1": "charter",
        "Check Box2": "nfo_same_day",
        "Check Box3": "priority",          # next day by noon
        "Check Box4": "regular_day",       # next day by 5 PM
        "Check Box5": "second_day",
        "Check Box6": "economy",
        "Check Box7": "date_specific",     # date goes in Text57
        "Check Box8": "saturday",
        "Check Box9": "sunday",
        "Check Box10": "truckload",
        "Check Box11": "hold_for_pickup",
    },
    # Column at x=379: LOCAL
    "service.local": {
        "Check Box12": "direct",
        "Check Box13": "hot_shot",
        "Check Box14": "regular",
        "Check Box15": "next_day",
        "Check Box16": "liftgate",
        "Check Box17": "two_person",
        "Check Box18": "inside",
    },
    # Column at x=443: OCEAN
    "service.ocean": {
        "Check Box19": "door_to_port",
        "Check Box20": "port_to_port",
        "Check Box21": "port_to_door",
        "Check Box22": "door_to_door",
        "Check Box23": "customs_clearance",
        "Check Box24": "free_domicile",
    },
    # Column at x=517: INT'L AIR
    "service.intl_air": {
        "Check Box25": "door_to_port",
        "Check Box26": "port_to_port",
        "Check Box27": "port_to_door",
        "Check Box28": "door_to_door",
        "Check Box29": "customs_clearance",
        "Check Box30": "free_domicile",
        "Check Box31": "charter",
        "Check Box32": "now",
        "Check Box33": "premium",
        "Check Box34": "value",
        "Check Box35": "direct_iata",
    },
    # Payment terms (one choice)
    "payment.terms": {
        "Check Box36": "prepaid",
        "Check Box37": "collect",
        "Check Box38": "third_party",
        "Check Box39": "cod",
    },
    # "This shipment contains dangerous goods" (UN# in Text114)
    "dangerous_goods.present": {
        "Check Box40": "yes",
    },
}


# ======================================================================
# SLI  (CEVA Shipper's Letter of Instructions, form 11201-C3)
# Field names are meaningful, so they map directly.
# ======================================================================

SLI_TEXT_FIELDS = {
    "Date": "sli.date",
    "Quote": "sli.quote_number",
    # Box 1-3: U.S. Principal Party in Interest (the exporter)
    "U.S. Principle Party": "usppi.name_address",
    "ZipCode": "usppi.zip_code",
    "USPPI": "usppi.ein",
    # Box 4 and 6: consignees
    "Intermediate Consignee": "intermediate_consignee.name_address",
    "Ultimate Consignee": "ultimate_consignee.name_address",
    # Box 7-12: references and route
    "Shipper Ref": "shipment.shipper_reference",
    "Consignee PO": "shipment.consignee_po",
    "Point of Origin": "shipment.origin_state",
    "Country of Ultimate": "shipment.destination_country",
    "In Bond Number": "shipment.in_bond_number",
    # Box 13-16: values, service, payment
    "Insurance": "value.insurance_amount",
    "Declared Value": "value.declared",
    "Requested": "service.requested",
    "Air": "service.air",
    "Ocean": "service.ocean",
    "Shipper/Carrier": "service.carrier_contract_number",
    "Location": "payment.location",
    "GBL": "payment.gbl_number",
    # Box 17
    "Special Instructions": "shipment.special_instructions",
    # Box 18-24: commodity lines (each box holds several lines, one per item)
    "D/F": "commodity.domestic_foreign",
    "Schedule B Number": "commodity.schedule_b",
    "License No": "commodity.license",
    "ECCN": "commodity.eccn",
    "Quantity Schedule B Unit": "commodity.quantity",
    "Shipping Weight": "commodity.weight_kg",
    "Value": "commodity.value_usd",
    # Box 25
    "Pieces & Dimensions": "shipment.pieces_dimensions",
    # Box 26-27: "other" document names
    "GBL1": "documents.attached_gbl_number",
    "Other2": "documents.attached_other_1",
    "Other4": "documents.attached_other_2",
    "Other1": "documents.to_prepare_other_1",
    "Other3": "documents.to_prepare_other_2",
    "Other5": "documents.to_prepare_other_3",
    # Box 28-33: signatory and dangerous goods initials
    "Duly Authorized": "signatory.name",
    "Title": "signatory.title",
    "Date2": "signatory.date",
    "Authentication": "export.authentication",
    "DOES NOT CONTAIN DANGEROUS GOODS": "dangerous_goods.no_initials",
    "DOES CONTAIN DANGEROUS GOODS": "dangerous_goods.yes_initials",
    # Box 35: pickup
    "Trk Line": "pickup.trucking_line",
    "Carrier Pro": "pickup.carrier_pro_number",
    "Driver Agent": "pickup.driver_agent",
    "Time": "pickup.time",
    "Date3": "pickup.date",
}

# Fields we never generate or extract: personal ID numbers and signatures.
SLI_SKIPPED_FIELDS = {"One", "TypeOne", "Two", "TypeTwo",
                      "PHOTO", "PHOTO2", "Signature2", "Signature3"}

# Checkbox groups: each group is one business field; the ticked box is its value.
SLI_CHECKBOX_GROUPS = {
    "parties.related": {"Related": "related", "Non-Related": "non_related"},
    "consignee.type": {
        "DIRECT CONSUMER": "direct_consumer",
        "RESELLER": "reseller",
        "GOVERNMENT ENTITY": "government_entity",
        "OTHER/UNKNOWN": "other_unknown",
    },
    "service.air_level": {
        "NOW": "now", "VALUE": "value",
        "PREMIUM": "premium", "DIRECT IATA": "direct_iata",
    },
    "service.ocean_level": {
        "FCL": "fcl", "LCL": "lcl", "PYRAMID LINES": "pyramid_lines",
    },
    "shipment.incoterm": {
        "EXW": "EXW", "FCA": "FCA", "CPT": "CPT", "CIP": "CIP",
        "DAP": "DAP", "DPU": "DPU", "DDP": "DDP",
        "FAS": "FAS", "FOB": "FOB", "CFR": "CFR", "CIF": "CIF",
    },
    "shipment.routed_export": {"YES": "yes", "NO": "no"},
    "pickup.received_at": {
        "SHIPPER DOOR": "shipper_door",
        "CEVE DOOR": "ceva_door",          # the form itself spells it "CEVE"
        "OTHER CARRIER DOOR": "other_carrier_door",
    },
}

# Document checkboxes. Two names appear in BOTH columns, so these are
# told apart by position: x < 420 = "Attached" column, x >= 420 = "To Be Prepared".
SLI_DOCUMENT_COLUMN_SPLIT_X = 420
SLI_DOCUMENT_CHECKBOXES = {
    "COMMERCIAL INVOICE": "commercial_invoice",
    "PROFORMA INVOICE": "proforma_invoice",
    "CERTIFICATE OF ORIGIN": "certificate_of_origin",   # appears twice
    "EXPORT LICENSE": "export_license",                 # appears twice
    "LETTER OF CREDIT": "letter_of_credit",
    "INSURANCE CERTIFICATE": "insurance_certificate",
    "DG DECLARATION": "dg_declaration",
    "SIGHT DRAFT": "sight_draft",
    "GBLCHECK": "gbl",
    "OTHER1": "other", "OTHER2": "other", "OTHER3": "other",
    "OTHER4": "other", "OTHER5": "other",
}