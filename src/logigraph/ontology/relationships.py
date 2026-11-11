"""Ontology relationships: which links are allowed between which classes.

Each relationship has a domain (the class it starts from) and a range (the
class it points to). Anything not listed here is rejected before it reaches
the graph, which keeps the graph clean.
"""
from logigraph.ontology.schema import CLASSES, ENUMS

RELATIONSHIPS = {
    # name: (domain, range, description)
    "SHIPPED_BY":          ("Shipment", "Company", "Shipper / USPPI (exporter) of the shipment"),
    "CONSIGNED_TO":        ("Shipment", "Company", "Ultimate consignee of the shipment"),
    "ORIGINATES_AT":       ("Shipment", "Location", "Origin airport or seaport"),
    "DESTINED_FOR":        ("Shipment", "Location", "Destination airport or seaport"),
    "HAS_LINE_ITEM":       ("Shipment", "LineItem", "A line on the HAWB / SLI"),
    "OF_COMMODITY":        ("LineItem", "Commodity", "The goods on that line"),
    "USES_SERVICE":        ("Shipment", "ServiceLevel", "Service ticked on the HAWB"),
    "CARRIED_BY":          ("Shipment", "Carrier", "Carrier reporting status (214)"),
    "HAS_EVENT":           ("Shipment", "StatusEvent", "A 214 milestone"),
    "AT_LOCATION":         ("StatusEvent", "Location", "Where the milestone happened"),
    "CAUSED_BY":           ("StatusEvent", "DelayReason", "Reason for a delay event"),
    "CONTAINS_DG":         ("Shipment", "DangerousGoods", "Dangerous goods on board"),
    "DOCUMENTED_IN":       ("Shipment", "Document", "A document that describes the shipment"),
}


class OntologyError(ValueError):
    """Raised when a fact does not fit the ontology."""


def check_relationship(name, from_class, to_class):
    """Reject a relationship that is unknown or links the wrong classes."""
    if name not in RELATIONSHIPS:
        raise OntologyError(f"Unknown relationship: {name}")
    domain, range_, _ = RELATIONSHIPS[name]
    if (from_class, to_class) != (domain, range_):
        raise OntologyError(
            f"{name} must link {domain} -> {range_}, got {from_class} -> {to_class}")


def check_node(class_name, properties):
    """Reject a node with an unknown class, a missing key or a bad enum value."""
    if class_name not in CLASSES:
        raise OntologyError(f"Unknown class: {class_name}")
    spec = CLASSES[class_name]
    key = spec["key"]
    if not properties.get(key):
        raise OntologyError(f"{class_name} needs its key property '{key}'")
    unknown = set(properties) - set(spec["properties"])
    if unknown:
        raise OntologyError(f"{class_name} has unknown properties: {sorted(unknown)}")
    for prop, value in properties.items():
        allowed = ENUMS.get(f"{class_name}.{prop}")
        if allowed and value is not None and value not in allowed:
            raise OntologyError(f"{class_name}.{prop}={value!r} is not one of {sorted(allowed)}")