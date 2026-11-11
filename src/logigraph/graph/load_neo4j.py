"""Step 5.2: Load the validated graph facts into Neo4j.

Techniques:
  - Uniqueness constraints: one per class key (from the ontology), so the same
    shipment or company can never be stored twice, and lookups by key are fast.
  - MERGE instead of CREATE: running the loader again updates nodes instead of
    duplicating them (idempotent loading).
  - Batched UNWIND: rows are sent in batches of 500, not one query per node.
  - Typed values: ontology "date" properties are stored as real Neo4j dates,
    so Cypher can compare and subtract them.
  - Provenance: every node and relationship keeps its source documents.
"""
import argparse
import json
from datetime import date
from pathlib import Path

from neo4j import GraphDatabase

from logigraph.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from logigraph.ontology.schema import CLASSES

FACTS_PATH = Path("data/processed/facts/graph_facts.json")
BATCH_SIZE = 500


def batches(rows, size=BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def typed(cls, props):
    """Convert ontology 'date' properties from ISO strings to real dates."""
    out = dict(props)
    for prop, kind in CLASSES[cls]["properties"].items():
        if kind == "date" and isinstance(out.get(prop), str):
            out[prop] = date.fromisoformat(out[prop])
    return out


def create_constraints(session):
    for cls, spec in CLASSES.items():
        key = spec["key"]
        session.run(f"CREATE CONSTRAINT {cls.lower()}_{key} IF NOT EXISTS "
                    f"FOR (n:{cls}) REQUIRE n.{key} IS UNIQUE")


def load_nodes(session, nodes):
    by_class = {}
    for node in nodes:
        cls = node["class"]
        props = typed(cls, node["props"])
        props["sources"] = json.dumps(node["sources"])      # Neo4j stores it as a JSON string
        props["source_count"] = len(node["sources"])
        by_class.setdefault(cls, []).append({"key": node["props"][CLASSES[cls]["key"]],
                                             "props": props})
    for cls, rows in by_class.items():
        key = CLASSES[cls]["key"]
        query = (f"UNWIND $rows AS row "
                 f"MERGE (n:{cls} {{{key}: row.key}}) "
                 f"SET n += row.props")
        for chunk in batches(rows):
            session.run(query, rows=chunk)
        print(f"  {cls:<15} {len(rows):>5} nodes")


def load_relationships(session, rels):
    groups = {}
    for rel in rels:
        (from_cls, from_key), (to_cls, to_key) = rel["from"], rel["to"]
        groups.setdefault((rel["type"], from_cls, to_cls), []).append({
            "from": from_key, "to": to_key,
            "sources": json.dumps(rel["sources"]), "source_count": len(rel["sources"]),
        })
    for (rel_type, from_cls, to_cls), rows in sorted(groups.items()):
        fk, tk = CLASSES[from_cls]["key"], CLASSES[to_cls]["key"]
        query = (f"UNWIND $rows AS row "
                 f"MATCH (a:{from_cls} {{{fk}: row.from}}) "
                 f"MATCH (b:{to_cls} {{{tk}: row.to}}) "
                 f"MERGE (a)-[r:{rel_type}]->(b) "
                 f"SET r.sources = row.sources, r.source_count = row.source_count")
        for chunk in batches(rows):
            session.run(query, rows=chunk)
        print(f"  ({from_cls})-[:{rel_type}]->({to_cls}) {len(rows):>5}")


def main():
    parser = argparse.ArgumentParser(description="Load graph facts into Neo4j.")
    parser.add_argument("--reset", action="store_true",
                        help="delete ALL nodes and relationships in the database first")
    args = parser.parse_args()

    if not NEO4J_PASSWORD:
        raise SystemExit("NEO4J_PASSWORD is missing. Add it to the .env file.")
    facts = json.loads(FACTS_PATH.read_text(encoding="utf-8"))

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print(f"Connected to {NEO4J_URI}")

    with driver.session() as session:
        if args.reset:
            session.run("MATCH (n) DETACH DELETE n")
            print("Database cleared.")
        create_constraints(session)
        print("\nNodes:")
        load_nodes(session, facts["nodes"])
        print("\nRelationships:")
        load_relationships(session, facts["relationships"])

        print("\nIn the database now:")
        for record in session.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"):
            print(f"  {record['label']:<15} {record['n']:>5}")

        print("\nCheck query - delayed shipments and their reason:")
        query = """
            MATCH (s:Shipment)-[:HAS_EVENT]->(:StatusEvent)-[:CAUSED_BY]->(r:DelayReason)
            MATCH (s)-[:SHIPPED_BY]->(c:Company)
            RETURN s.hawb_number AS hawb, s.delay_days AS days, r.description AS reason, c.name AS shipper
            ORDER BY days DESC LIMIT 5
        """
        for record in session.run(query):
            print(f"  {record['hawb']}  {record['days']:>3} days  {record['reason']:<38} {record['shipper']}")

    driver.close()


if __name__ == "__main__":
    main()