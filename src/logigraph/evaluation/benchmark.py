"""Step 6.4b: Benchmark - vector-only RAG vs. hybrid GraphRAG on the same questions.

The two systems:
  - vector : classic RAG. The question is embedded, the top passages are taken
             from FAISS, and the LLM answers from them. No planner, no graph.
  - hybrid : LogiGraph. Planner -> Cypher templates on Neo4j (+ FAISS when the
             question also needs contract wording) -> cited answer.
Both use the SAME answer prompt, LLM, citation gate and "not found" rule, so the
only difference is how the evidence is retrieved.

Scoring (done in code, no LLM judge):
  - set      : HAWB numbers in the answer must equal the gold list exactly
               (precision and recall are also reported)
  - number   : the first number in the answer (after removing HAWB IDs,
               citations and dates) must equal the gold value
  - text     : every required phrase (or one of its alternatives) must appear
  - refusal  : the answer must be the "not found" reply
  - any answerable question that gets "not found" is wrong
  - citations: every [E#]/[T#] in the answer must point to real evidence
"""
import argparse
import json
import re
import time
from collections import defaultdict

from logigraph.config import PROJECT_ROOT
from logigraph.evaluation.questions import OUT_FILE as QUESTIONS_FILE, build_questions, load_shipments
from logigraph.rag.answer import CITATION, NOT_FOUND, Answerer

EVAL_DIR = PROJECT_ROOT / "data" / "processed" / "eval"
SYSTEMS = ("vector", "hybrid")
HAWB = re.compile(r"\bSYN\d{8}\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def is_refusal(answer):
    return answer.strip() == NOT_FOUND


def first_number(answer):
    text = CITATION.sub(" ", HAWB.sub(" ", DATE.sub(" ", answer)))
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)            # 4,068 -> 4068
    match = NUMBER.search(text)
    return float(match.group()) if match else None


def has_phrases(answer, required):
    low = answer.lower()
    return all(any(alt.lower() in low for alt in ([r] if isinstance(r, str) else r)) for r in required)


def score(item, result):
    answer = result["answer"]
    kind, gold = item["kind"], item["gold"]
    out = {"refused": is_refusal(answer)}

    if kind == "refusal":
        out["correct"] = out["refused"]
        return out
    if out["refused"]:
        out["correct"] = False
        return out

    if kind in ("set", "set+text"):
        found, expected = set(HAWB.findall(answer)), set(gold)
        hit = len(found & expected)
        out["precision"] = round(hit / len(found), 2) if found else 0.0
        out["recall"] = round(hit / len(expected), 2) if expected else 1.0
        out["correct"] = found == expected
        if kind == "set+text":
            out["correct"] = out["correct"] and has_phrases(answer, item["phrases"])
    elif kind == "number":
        value = first_number(answer)
        out["got"] = value
        out["correct"] = value is not None and abs(value - float(gold)) < 0.05
    elif kind == "text":
        out["correct"] = has_phrases(answer, gold)

    cited = CITATION.findall(answer)
    known = {e["id"] for e in result["evidence"]}
    out["citations_valid"] = bool(cited) and all(c in known for c in cited) and not result["fallback"]
    return out


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def run_system(answerer, system, questions):
    rows = []
    for item in questions:
        start = time.perf_counter()
        try:
            if system == "vector":
                plan = {"question": item["question"], "route": "vector", "intent": "vector_only",
                        "text_query": item["question"]}
                result = answerer.ask(item["question"], plan=plan)
            else:
                result = answerer.ask(item["question"])
            error = None
        except Exception as exc:                       # one failing question must not stop the run
            result = {"answer": f"ERROR: {exc}", "evidence": [], "fallback": False, "route": None}
            error = str(exc)
        seconds = round(time.perf_counter() - start, 2)
        marks = score(item, result)
        rows.append({**item, "system": system, "route": result.get("route"), "answer": result["answer"],
                     "evidence_ids": [e["id"] for e in result["evidence"]], "seconds": seconds,
                     "error": error, **marks})
        print(f"  {system:<6} {item['id']} {'OK ' if marks['correct'] else 'X  '} "
              f"route={result.get('route')!s:<6} {seconds:>5}s  {item['question'][:55]}")
    return rows


def summarize(rows):
    table = defaultdict(lambda: {s: [0, 0] for s in SYSTEMS})
    cites = {s: [0, 0] for s in SYSTEMS}
    seconds = {s: [] for s in SYSTEMS}
    for r in rows:
        for key in (r["category"], "ALL"):
            table[key][r["system"]][0] += r["correct"]
            table[key][r["system"]][1] += 1
        if "citations_valid" in r:
            cites[r["system"]][0] += r["citations_valid"]
            cites[r["system"]][1] += 1
        seconds[r["system"]].append(r["seconds"])
    return table, cites, seconds


def pct(pair):
    return f"{pair[0]}/{pair[1]} ({100 * pair[0] / pair[1]:.0f}%)" if pair[1] else "-"


def report(rows, systems):
    table, cites, seconds = summarize(rows)
    order = ["graph_list", "aggregate", "lookup", "contract", "hybrid", "unanswerable", "ALL"]
    lines = ["# LogiGraph benchmark: vector-only RAG vs. hybrid GraphRAG", "",
             f"{len({r['id'] for r in rows})} questions; gold answers computed from the synthetic ground truth.", "",
             "| Category | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    for cat in order:
        if cat in table:
            label = f"**{cat}**" if cat == "ALL" else cat
            lines.append(f"| {label} | " + " | ".join(pct(table[cat][s]) for s in systems) + " |")
    lines.append("| valid citations (answered) | " + " | ".join(pct(cites[s]) for s in systems) + " |")
    lines.append("| avg seconds / question | " + " | ".join(
        f"{sum(seconds[s]) / len(seconds[s]):.1f}" if seconds[s] else "-" for s in systems) + " |")
    lines += ["", "## Misses", ""]
    for r in rows:
        if not r["correct"]:
            lines.append(f"- {r['system']} {r['id']} ({r['category']}): {r['question']}  "
                         f"gold={r['gold']}  answer={r['answer'][:160]!r}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Benchmark vector-only vs. hybrid answering.")
    parser.add_argument("--system", choices=SYSTEMS, help="run only one system")
    parser.add_argument("--ids", help="comma-separated question ids, e.g. Q01,Q14")
    args = parser.parse_args()

    questions = build_questions(load_shipments())
    QUESTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUESTIONS_FILE.write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    systems = [args.system] if args.system else list(SYSTEMS)

    answerer = Answerer()
    rows = []
    try:
        for system in systems:
            print(f"\nRunning {system} on {len(questions)} questions")
            rows += run_system(answerer, system, questions)
    finally:
        answerer.close()

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVAL_DIR / "results.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    text = report(rows, systems)
    (EVAL_DIR / "report.md").write_text(text, encoding="utf-8")
    print("\n" + text.split("## Misses")[0])
    print(f"Full report: {(EVAL_DIR / 'report.md').relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()