"""Step 6.5: Streamlit UI for LogiGraph.

Run:  uv run streamlit run src/logigraph/ui/app.py

Techniques:
  - st.cache_resource: the Answerer (OpenAI client, Neo4j driver, FAISS index)
    is created ONCE per server and reused by every question and every user,
    instead of reconnecting and reloading the index on each click.
  - st.session_state: Streamlit re-runs the whole script on every click, so the
    question history is kept in session_state to survive those re-runs.
  - Explainability: every answer shows its sources, the route the planner
    chose, the Cypher that ran, and the raw evidence, so a user can check
    exactly where each claim came from.
  - Side-by-side mode: the same question answered by hybrid GraphRAG and by
    plain vector RAG, the live version of the benchmark.
"""
import json

import pandas as pd
import streamlit as st

from logigraph.config import PROJECT_ROOT
from logigraph.rag.answer import CITATION, NOT_FOUND, Answerer

REPORT = PROJECT_ROOT / "data" / "processed" / "eval" / "report.md"
SAMPLES = [
    "Which shipments were delayed by a customs hold?",
    "How many shipments went by Truck?",
    "Which transport mode has the highest late-delivery rate?",
    "What is the total weight in kg of laptops shipped to Germany?",
    "Tell me everything about HAWB SYN19046318",
    "What is the carrier's liability limit per pound?",
    "Which shipments were delayed by weather, and what does the contract say about the carrier's responsibility for delays?",
    "What is the name of the carrier's CEO?",
]
ROUTE_COLORS = {"graph": "blue", "vector": "orange", "hybrid": "violet"}

st.set_page_config(page_title="LogiGraph", page_icon="📦", layout="wide")


@st.cache_resource(show_spinner="Connecting to Neo4j and loading the FAISS index...")
def get_answerer():
    return Answerer()


def ask(question, system):
    answerer = get_answerer()
    if system == "vector":
        plan = {"question": question, "route": "vector", "intent": "vector_only", "text_query": question}
        return answerer.ask(question, plan=plan)
    return answerer.ask(question)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def highlight(answer):
    """Make citation markers stand out: [E1] -> bold blue, [T2] -> bold orange."""
    return CITATION.sub(lambda m: f":{'blue' if m.group(1)[0] == 'E' else 'orange'}[**[{m.group(1)}]**]", answer)


def evidence_tables(evidence):
    graph = [{"id": e["id"], **{k: (json.dumps(v, default=str) if isinstance(v, (list, dict)) else v)
                                for k, v in e["content"].items()},
              "sources": ", ".join(e["sources"])}
             for e in evidence if e["kind"] == "graph"]
    text = [e for e in evidence if e["kind"] == "text"]
    return graph, text


def show_result(result, title=None):
    if title:
        st.subheader(title)
    route = result.get("route") or "-"
    color = ROUTE_COLORS.get(route, "gray")
    st.markdown(f":{color}-badge[route: {route}] :gray-badge[intent: {result.get('intent')}]"
                + (f" :gray-badge[graph rows: {result['graph_row_count']}]"
                   if result.get("graph_row_count") is not None else ""))

    if result["answer"] == NOT_FOUND:
        st.warning(result["answer"])
    else:
        st.markdown(highlight(result["answer"]))
    if result.get("fallback"):
        st.caption("The LLM answer had no valid citations, so the raw evidence is shown instead.")

    if result.get("citations"):
        st.markdown("**Sources**")
        for cid, sources in result["citations"].items():
            st.markdown(f"- **[{cid}]** {', '.join(sources)}")

    with st.expander("How it was answered"):
        plan = {k: v for k, v in result.get("plan", {}).items() if k not in ("route", "intent")}
        st.markdown("**Planner output** (filters the LLM extracted from the question)")
        st.json(plan or {})
        if result.get("cypher"):
            st.markdown("**Cypher that ran on Neo4j** (a fixed template; the values are parameters)")
            st.code(result["cypher"], language="cypher")

    graph, text = evidence_tables(result.get("evidence", []))
    if graph or text:
        with st.expander(f"Evidence ({len(graph)} graph rows, {len(text)} text passages)"):
            if graph:
                st.dataframe(pd.DataFrame(graph), hide_index=True, width="stretch")
            for e in text:
                st.markdown(f"**[{e['id']}]** {e['sources'][0]}  ·  similarity {e['score']:.2f}")
                st.caption(e["content"])


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
if "history" not in st.session_state:
    st.session_state.history = []
if "pending" not in st.session_state:
    st.session_state.pending = None

with st.sidebar:
    st.title("📦 LogiGraph")
    st.caption("Ontology-powered GraphRAG over logistics documents: HAWB, SLI, X12 214 and status reports.")
    compare = st.toggle("Compare with plain vector RAG", value=False,
                        help="Answer the same question twice: hybrid GraphRAG and vector-only RAG.")
    st.markdown("**Try a question**")
    for i, sample in enumerate(SAMPLES):
        if st.button(sample, key=f"sample_{i}", width="stretch"):
            st.session_state.pending = sample
    if st.session_state.history and st.button("Clear history", type="secondary"):
        st.session_state.history = []
        st.rerun()
    if REPORT.exists():
        with st.expander("Benchmark result"):
            st.markdown(REPORT.read_text(encoding="utf-8").split("## Misses")[0].split("\n", 2)[2])
    st.caption("All documents are SYNTHETIC samples, not real shipments.")

st.title("Ask about the shipments")
st.caption("Answers come only from the knowledge graph and the documents, with a citation after every claim.")

typed = st.chat_input("e.g. Which Truck shipments were delivered late?")
question = typed or st.session_state.pending
st.session_state.pending = None

if question:
    try:
        with st.spinner("Planning, retrieving and writing the answer..."):
            hybrid = ask(question, "hybrid")
            vector = ask(question, "vector") if compare else None
        st.session_state.history.insert(0, {"question": question, "hybrid": hybrid, "vector": vector})
    except Exception as exc:
        st.error(f"Could not answer: {exc}")
        st.info("Check that Neo4j is running (docker ps) and that OPENAI_API_KEY is set in .env.")

for n, turn in enumerate(st.session_state.history):
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        if turn["vector"] is not None:
            left, right = st.columns(2)
            with left:
                show_result(turn["hybrid"], "Hybrid GraphRAG")
            with right:
                show_result(turn["vector"], "Plain vector RAG")
        else:
            show_result(turn["hybrid"])
    if n == 0 and len(st.session_state.history) > 1:
        st.divider()