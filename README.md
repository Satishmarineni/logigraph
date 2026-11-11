# LogiGraph: Logistics Knowledge Graph with Cited Answers

Ask plain-English questions about shipments and get answers with a citation after every claim, built from a **knowledge graph (Neo4j)** and **vector search (FAISS)** over logistics documents: House Air Waybills (HAWB), Shipper's Letters of Instruction (SLI), **ANSI X12 214** status messages, and carrier status reports.

> All documents in this project are **synthetic** ("SYNTHETIC SAMPLE - NOT A REAL SHIPMENT"). Company names, EINs and shipments are generated; X12 files are marked as test (ISA15 = T).

## Why a graph?

Plain vector RAG retrieves a few passages and asks the LLM to answer from them. That works for "what does clause 8 say?" but fails on questions that need **many records**: it can only count what it retrieved. LogiGraph sends fact questions to Cypher queries over a graph built from an ontology, and contract questions to vector search, then combines both.

## Benchmark result

29 questions, gold answers **computed from ground truth** (not typed by hand), scored in code (no LLM judge). Same LLM, prompt and citation rules for both systems; only retrieval differs.

| Category | Plain vector RAG | LogiGraph (hybrid) |
|---|---|---|
| Lists of shipments | 4/9 (44%) | **9/9 (100%)** |
| Counts and aggregates | 1/6 (17%) | **6/6 (100%)** |
| Single-shipment lookups | 4/5 (80%) | **5/5 (100%)** |
| Contract terms | 5/5 (100%) | 5/5 (100%) |
| Facts + contract (hybrid) | 0/1 | **1/1** |
| Unanswerable (must refuse) | 3/3 | 3/3 |
| **Total** | **17/29 (59%)** | **29/29 (100%)** |
| Valid citations | 100% | 100% |
| Avg. seconds per question | 1.5–1.9 | 3.4–4.1 |

Typical vector-RAG failure: *"There are 4 shipments that went by Truck"* (true answer 28), because it counts only the passages it retrieved. LogiGraph answers *"28 shipments went by Truck [E0]"*, where E0 is the Cypher count over all records.

Limits: synthetic data and 29 questions written for this project. Totals were stable across three full runs (vector-only 17/29 every time, hybrid 29/29 every time); the vector-only split between categories varied slightly between runs.

## Architecture

```mermaid
flowchart LR
    subgraph Documents
        H[HAWB PDF forms]
        S[SLI PDF forms]
        E[X12 214 EDI]
        R[Status report PDFs]
        T[HAWB terms and conditions]
    end
    H & S -->|AcroForm field maps| X[Extraction]
    E -->|X12 parser + envelope checks| X
    R -->|LLM structured output + evidence quote| X
    X --> F[Facts validated against the ontology<br/>entity resolution, provenance]
    F --> N[(Neo4j knowledge graph)]
    T & R & H & S --> C[Clause-aware chunking] --> V[(FAISS index)]
    Q[Question] --> P[Query planner<br/>intent + route + filters]
    P -->|graph| N
    P -->|vector| V
    N & V --> A[Answer writer<br/>E#/T# citations + gates]
    A --> U[Streamlit UI]
```

## What is in it

| Layer | Techniques |
|---|---|
| Synthetic data | Faker with a fixed seed; delay rates and delay lengths per mode taken from the public SCMS Delivery History dataset (quantiles / inverse CDF); ground truth saved for evaluation |
| Documents | PyMuPDF AcroForm filling of real HAWB/SLI templates; X12 214 (004010) with ISA/GS/ST envelopes, AT7 status and reason codes |
| Ontology | 11 classes, 13 relationships with domain/range rules, enums, exported to OWL/Turtle; every node and edge is checked before loading |
| Extraction | Field-map extraction for forms, delimiter-aware X12 parsing, LLM extraction for free text with Pydantic structured output, verbatim evidence quotes, validation gates, cross-checks against the 214, review queue |
| Graph | Neo4j 5 in Docker; uniqueness constraints, MERGE, typed dates, source documents stored on every node |
| Vector | Clause-aware chunking with context headers, OpenAI `text-embedding-3-small`, FAISS inner product on normalized vectors (cosine), embedding cache |
| Retrieval | LLM query planner with constrained (Literal) fields; HAWB IDs taken by regex, not by the LLM; pre-written Cypher templates with parameters (no LLM-written Cypher, no injection) |
| Answering | Every piece of evidence numbered (E# graph, T# text); citation validation; "not found" when evidence is missing; totals cited as their own evidence |
| Evaluation | Gold answers from ground truth, per-category scoring, vector-only vs hybrid |
| UI | Streamlit with sources, the Cypher that ran, raw evidence, and a side-by-side comparison mode |

Extraction accuracy against ground truth: HAWB and SLI forms 100%, X12 214 100%, status reports 100 accepted / 0 conflicts.

## Project layout

```
src/logigraph/
  config.py          settings from .env
  synthetic/         ground truth, form filling, X12 214 + status reports
  forms/             field maps and X12 code lists
  ontology/          classes, relationships, OWL/Turtle export
  extraction/        form, X12 and status-report extraction; fact building
  graph/             Neo4j loader
  ingestion/         chunking
  vector/            embeddings and FAISS
  rag/               query planner, graph retriever, answer writer
  evaluation/        benchmark questions and scoring
  ui/                Streamlit app
scripts/             one-off inspection scripts
docker-compose.yml   Neo4j
```

## Run it

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker Desktop, an OpenAI API key.

```bash
uv sync
```

Create `.env` in the project root (never committed):

```
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=choose-a-password
```

The blank HAWB/SLI templates and the SCMS dataset are not included in the repo. Place the templates in `data/templates/` and the SCMS CSV in `data/external/`.

```bash
# 1. Synthetic data and documents
uv run python -m logigraph.synthetic.delay_profile
uv run python -m logigraph.synthetic.generator
uv run python -m logigraph.synthetic.fill_forms --limit 100
uv run python -m logigraph.synthetic.edi214

# 2. Ontology and extraction
uv run python -m logigraph.ontology.export
uv run python -m logigraph.extraction.form_extractor --check
uv run python -m logigraph.extraction.edi_parser --check
uv run python -m logigraph.extraction.status_extractor
uv run python -m logigraph.extraction.build_facts

# 3. Graph and vector index
docker compose up -d neo4j
uv run python -m logigraph.graph.load_neo4j
uv run python -m logigraph.ingestion.chunking
uv run python -m logigraph.vector.vector_store

# 4. Ask, evaluate, demo
uv run python -m logigraph.rag.answer --question "Which shipments were delayed by a customs hold?"
uv run python -m logigraph.evaluation.benchmark
uv run streamlit run src/logigraph/ui/app.py
```

## Example

```
Q: Which shipments were delayed by weather, and what does the contract say
   about the carrier's responsibility for delays?
route=hybrid

1. SYN12515446 - delayed 44 days due to weather [E1]
2. SYN19046318 - delayed 22 days due to weather [E2]
The Company is not responsible for loss, damage or expense caused by delay
unless the delay is due to its gross negligence or willful misconduct [T1].

Sources:
  [E1] 214_SYN12515446, HAWB_SYN12515446, SLI_Q-SYN-000048, STATUS_SYN12515446
  [E2] 214_SYN19046318, HAWB_SYN19046318, SLI_Q-SYN-000007, STATUS_SYN19046318
  [T1] HAWB_TERMS p.2 clause 20
```
