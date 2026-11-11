"""Export the ontology as OWL in Turtle format and print a summary.

Run:  uv run python -m logigraph.ontology.export
"""
from pathlib import Path

from logigraph.ontology.relationships import RELATIONSHIPS, OntologyError, check_node, check_relationship
from logigraph.ontology.schema import CLASSES

OUTPUT = Path("data/processed/ontology.ttl")
BASE = "http://logigraph.example/ontology#"
XSD = {"string": "xsd:string", "date": "xsd:date", "integer": "xsd:integer",
       "decimal": "xsd:decimal", "boolean": "xsd:boolean"}


def to_turtle():
    out = [
        f"@prefix lg: <{BASE}> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        "<http://logigraph.example/ontology> a owl:Ontology ;",
        '    rdfs:label "LogiGraph logistics ontology" .',
        "",
    ]
    for name, spec in CLASSES.items():
        out += [f"lg:{name} a owl:Class ;",
                f'    rdfs:label "{name}" ;',
                f'    rdfs:comment "{spec["description"]}" .', ""]
        for prop, kind in spec["properties"].items():
            out += [f"lg:{name}_{prop} a owl:DatatypeProperty ;",
                    f"    rdfs:domain lg:{name} ;",
                    f"    rdfs:range {XSD[kind]} .", ""]
    for name, (domain, range_, description) in RELATIONSHIPS.items():
        out += [f"lg:{name} a owl:ObjectProperty ;",
                f"    rdfs:domain lg:{domain} ;",
                f"    rdfs:range lg:{range_} ;",
                f'    rdfs:comment "{description}" .', ""]
    return "\n".join(out)


def self_check():
    """Show that good facts pass and bad facts are rejected."""
    check_node("Shipment", {"hawb_number": "SYN19046318", "mode": "Air"})
    check_relationship("SHIPPED_BY", "Shipment", "Company")
    bad = [
        lambda: check_node("Shipment", {"hawb_number": "X", "mode": "Rocket"}),
        lambda: check_node("Shipment", {"mode": "Air"}),
        lambda: check_relationship("SHIPPED_BY", "Company", "Shipment"),
        lambda: check_relationship("OWNS", "Company", "Shipment"),
    ]
    for test in bad:
        try:
            test()
            print("  NOT rejected (unexpected)")
        except OntologyError as e:
            print(f"  rejected: {e}")


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(to_turtle(), encoding="utf-8")
    print(f"Classes      : {len(CLASSES)}  ({', '.join(CLASSES)})")
    print(f"Relationships: {len(RELATIONSHIPS)}")
    for name, (d, r, _) in RELATIONSHIPS.items():
        print(f"  ({d})-[:{name}]->({r})")
    print("\nValidation check:")
    self_check()
    print(f"\nOWL/Turtle saved: {OUTPUT}")


if __name__ == "__main__":
    main()