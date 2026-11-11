"""Ontology classes: the kinds of things the knowledge graph can hold.

Each class becomes a Neo4j node label. `key` is the property that makes a
node unique (used for MERGE, so the same shipment is never stored twice).
Classes come from what the documents actually contain:
  HAWB  -> shipment, shipper, consignee, line items, service, payment, DG
  SLI   -> exporter (USPPI), Incoterm, Schedule B, ECCN, license
  214   -> status events, delay reasons, carrier
"""

CLASSES = {
    "Shipment": {
        "description": "One export shipment, identified by its HAWB number.",
        "key": "hawb_number",
        "properties": {
            "hawb_number": "string", "shipment_id": "string", "po_number": "string",
            "mode": "string", "ship_date": "date",
            "scheduled_delivery": "date", "actual_delivery": "date",
            "delay_days": "integer", "delay_reason": "string",
            "incoterm": "string", "payment_terms": "string",
            "declared_value_usd": "decimal", "insurance_usd": "decimal",
            "routed_export": "boolean",
        },
    },
    "Company": {
        "description": "A shipper (USPPI / exporter) or consignee. Its role comes from the relationship.",
        "key": "company_id",
        "properties": {
            "company_id": "string", "name": "string", "address": "string", "city": "string",
            "state_or_country": "string", "account_number": "string",
        },
    },
    "Location": {
        "description": "An airport, seaport or city where a shipment starts, ends or has an event.",
        "key": "code",
        "properties": {"code": "string", "city": "string", "country": "string", "kind": "string"},
    },
    "LineItem": {
        "description": "One line on the HAWB / SLI: pieces, weight, value of one commodity.",
        "key": "line_id",
        "properties": {
            "line_id": "string", "pieces": "integer", "gross_weight_kg": "decimal",
            "value_usd": "decimal", "dimensions_in": "string",
        },
    },
    "Commodity": {
        "description": "A kind of goods, identified by its Schedule B number.",
        "key": "schedule_b",
        "properties": {"schedule_b": "string", "description": "string",
                       "eccn": "string", "license": "string"},
    },
    "ServiceLevel": {
        "description": "The service ticked on the HAWB, e.g. intl_air.charter.",
        "key": "code",
        "properties": {"code": "string"},
    },
    "Carrier": {
        "description": "The carrier that sends 214 status messages, identified by SCAC.",
        "key": "scac",
        "properties": {"scac": "string", "name": "string"},
    },
    "StatusEvent": {
        "description": "One milestone from an X12 214 AT7 segment.",
        "key": "event_id",
        "properties": {
            "event_id": "string", "status_code": "string", "status": "string",
            "reason_code": "string", "date": "date",
        },
    },
    "DelayReason": {
        "description": "Why a shipment was delayed (X12 AT702 reason code).",
        "key": "code",
        "properties": {"code": "string", "description": "string"},
    },
    "DangerousGoods": {
        "description": "A dangerous goods classification, identified by UN number.",
        "key": "un_number",
        "properties": {"un_number": "string"},
    },
    "Document": {
        "description": "A source document: HAWB, SLI, EDI 214 or status report.",
        "key": "doc_id",
        "properties": {"doc_id": "string", "doc_type": "string", "path": "string"},
    },
}

# Allowed values for properties that come from a fixed list.
ENUMS = {
    "Shipment.mode": {"Air", "Air Charter", "Ocean", "Truck"},
    "Shipment.incoterm": {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP",
                          "FAS", "FOB", "CFR", "CIF"},
    "Shipment.payment_terms": {"prepaid", "collect", "third_party", "cod"},
    "Location.kind": {"airport", "seaport", "city"},
    "Document.doc_type": {"HAWB", "SLI", "EDI214", "STATUS_REPORT"},
}

# Every fact written to the graph carries this provenance (as properties on
# the node or relationship), so every answer can cite its source.
PROVENANCE_FIELDS = ["doc_id", "page", "chunk_id", "evidence", "confidence"]