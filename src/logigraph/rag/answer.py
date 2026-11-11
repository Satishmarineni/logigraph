"""Step 6.3: Hybrid question answering with citations.

Flow for one question:
  1. Plan it (6.1): intent, route (graph / vector / hybrid), filters.
  2. Retrieve: graph rows from Neo4j (6.2) and/or text passages from FAISS (5.3).
  3. Number every piece of evidence: graph rows E1, E2, ... and text passages T1, T2, ...
  4. The LLM writes the answer ONLY from that evidence and cites [E1] / [T2] after each claim.
  5. Checks in code:
       - counts come from the graph result, not from the LLM
       - every citation must point to real evidence; an answer with no valid
         citation is replaced by the evidence itself (no unsupported answers)
       - nothing found -> a plain "not found" answer, without calling the LLM
"""
import argparse
import json
import re

from neo4j import GraphDatabase
from openai import OpenAI

from logigraph.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, OPENAI_API_KEY, OPENAI_MODEL
from logigraph.rag.graph_retriever import retrieve
from logigraph.rag.query_planner import plan_question
from logigraph.vector.vector_store import load_index, search

NOT_FOUND = ("I could not find this in the shipment documents or the knowledge graph. "
             "No matching records or relevant passages were found.")
MAX_GRAPH_ROWS = 25        # rows shown to the LLM (the full count is still reported)
TEXT_HITS = 4
MIN_TEXT_SCORE = 0.40      # below this, a text passage is treated as not relevant
CITATION = re.compile(r"\[([ET]\d+)\]")

ANSWER_PROMPT = """You answer questions about logistics shipments using ONLY the evidence given.
Evidence items are labelled E1, E2, ... (facts from the knowledge graph) and T1, T2, ...
(passages from documents). Rules:
- After every factual claim, cite the evidence it came from, like [E1] or [T2].
- Use the exact numbers in the evidence. If a total count is given, use it as stated.
- When you describe a document passage, stay close to its wording. Do not add examples,
  causes or conditions that the passage does not state.
- If the evidence does not answer the question, reply with exactly: NOT_FOUND
- Never guess or use outside knowledge.
- Be concise: a short direct answer first, then supporting details or a short list."""


class Answerer:
    def __init__(self):
        self.llm = OpenAI(api_key=OPENAI_API_KEY, max_retries=3, timeout=60)
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        self.index, self.chunks = load_index()

    def close(self):
        self.driver.close()

    # ------------------------------------------------------------------
    def gather(self, plan):
        """Return graph result, text hits and the numbered evidence list."""
        evidence, graph, hits = [], None, []

        if plan["route"] in ("graph", "hybrid"):
            graph = retrieve(self.driver, plan)
            for i, row in enumerate(graph["rows"][:MAX_GRAPH_ROWS], start=1):
                # Aggregates over many shipments (e.g. late rate by mode) cite the query itself.
                docs = row.get("doc_ids") or ["knowledge graph aggregate (see Cypher)"]
                facts = {k: v for k, v in row.items() if k != "doc_ids" and v not in (None, [], "")}
                evidence.append({"id": f"E{i}", "kind": "graph", "content": facts, "sources": docs})

        if plan["route"] in ("vector", "hybrid") and plan.get("text_query"):
            hits = [h for h in search(self.llm, self.index, self.chunks, plan["text_query"], k=TEXT_HITS)
                    if h["score"] >= MIN_TEXT_SCORE]
            for i, hit in enumerate(hits, start=1):
                source = f"{hit['doc_id']} p.{hit['page']}" + (f" clause {hit['clause']}" if "clause" in hit else "")
                evidence.append({"id": f"T{i}", "kind": "text", "content": hit["text"],
                                 "sources": [source], "score": hit["score"]})
        return graph, hits, evidence

    def write(self, question, graph, evidence):
        lines = []
        if graph is not None:
            shown = min(graph["row_count"], MAX_GRAPH_ROWS)
            lines.append(f"Total matching rows in the knowledge graph: {graph['row_count']}"
                         + (f" (first {shown} shown)" if graph["row_count"] > shown else ""))
        for item in evidence:
            content = item["content"] if isinstance(item["content"], str) else json.dumps(item["content"], default=str)
            lines.append(f"[{item['id']}] {content}")
        completion = self.llm.chat.completions.create(
            model=OPENAI_MODEL, temperature=0,
            messages=[{"role": "system", "content": ANSWER_PROMPT},
                      {"role": "user", "content": f"Question: {question}\n\nEvidence:\n" + "\n".join(lines)}],
        )
        return completion.choices[0].message.content.strip()

    # ------------------------------------------------------------------
    def ask(self, question, plan=None):
        plan = plan or plan_question(self.llm, question)   # a ready plan skips the planner (benchmark)
        graph, hits, evidence = self.gather(plan)
        result = {"question": question, "route": plan["route"], "intent": plan["intent"],
                  "plan": {k: v for k, v in plan.items() if v not in (None, False) and k != "question"},
                  "cypher": graph["cypher"] if graph else None,
                  "graph_row_count": graph["row_count"] if graph else None,
                  "evidence": evidence, "fallback": False}

        if not evidence:
            result["answer"], result["citations"] = NOT_FOUND, {}
            return result

        answer = self.write(question, graph, evidence)
        if answer.strip().strip(".") == "NOT_FOUND":   # the LLM says the evidence does not answer it
            result["answer"], result["citations"] = NOT_FOUND, {}
            return result
        known = {item["id"]: item for item in evidence}
        cited = [c for c in dict.fromkeys(CITATION.findall(answer)) if c in known]

        if not cited:                            # gate: never return an uncited answer
            result["fallback"] = True
            answer = "The answer could not be verified with citations. Here is the evidence found:\n" + \
                     "\n".join(f"[{e['id']}] {e['content'] if isinstance(e['content'], str) else json.dumps(e['content'], default=str)}"
                               for e in evidence[:5])
            cited = [e["id"] for e in evidence[:5]]
        result["answer"] = answer
        result["citations"] = {c: known[c]["sources"] for c in cited}
        return result


SAMPLE_QUESTIONS = [
    "Which shipments were delayed by a customs hold?",
    "How many prepaid air shipments under EXW were there?",
    "What is the carrier's liability limit per pound?",
    "Which shipments were delayed by weather, and what does the contract say about the carrier's responsibility for delays?",
    "What is the CEO's name of the carrier?",
]


def show(result):
    print("\n" + "=" * 80)
    print(f"Q: {result['question']}")
    print(f"   route={result['route']}  intent={result['intent']}  graph rows={result['graph_row_count']}")
    print(f"\n{result['answer']}")
    if result["citations"]:
        print("\nSources:")
        for cid, sources in result["citations"].items():
            print(f"  [{cid}] {', '.join(sources)}")
    if result["fallback"]:
        print("\n(fallback: the LLM answer had no valid citations)")


def main():
    parser = argparse.ArgumentParser(description="Ask questions about the shipments.")
    parser.add_argument("--question", help="ask this question instead of the samples")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    answerer = Answerer()
    try:
        for question in [args.question] if args.question else SAMPLE_QUESTIONS:
            result = answerer.ask(question)
            print(json.dumps(result, indent=2, default=str) if args.json else "", end="")
            if not args.json:
                show(result)
    finally:
        answerer.close()


if __name__ == "__main__":
    main()