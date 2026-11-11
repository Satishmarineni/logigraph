"""Step 5.3b: Embed the chunks with OpenAI and store them in a FAISS index.

Techniques:
  - Embeddings: OpenAI text-embedding-3-small turns each chunk into a vector
    of numbers, so texts with similar meaning end up close together.
  - Cosine similarity with FAISS: vectors are normalized to length 1, so an
    inner-product index (IndexFlatIP) gives the cosine similarity directly.
    IndexFlatIP is exact search, ideal for a few thousand chunks.
  - Embedding cache: each vector is saved by a hash of its text, so
    re-building the index only pays for new or changed chunks.
  - The index and the chunk metadata are saved side by side; a search
    returns the chunk text, its doc_id/page (for citations) and a score.
"""
import argparse
import hashlib
import json
from pathlib import Path

import faiss
import numpy as np
from openai import OpenAI

from logigraph.config import OPENAI_API_KEY

CHUNKS_PATH = Path("data/processed/chunks.jsonl")
INDEX_DIR = Path("data/index")
INDEX_PATH = INDEX_DIR / "faiss.index"
META_PATH = INDEX_DIR / "chunks_meta.jsonl"
CACHE_PATH = INDEX_DIR / "embedding_cache.jsonl"
EMBED_MODEL = "text-embedding-3-small"
BATCH = 100


# ---------------------------------------------------------------------------
# Embeddings with a cache
# ---------------------------------------------------------------------------
def text_key(text):
    return hashlib.sha256(f"{EMBED_MODEL}|{text}".encode("utf-8")).hexdigest()


def load_cache():
    cache = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["key"]] = row["vector"]
    return cache


def embed_texts(client, texts):
    """Return one vector per text, calling the API only for texts not in the cache."""
    cache = load_cache()
    missing = [t for t in dict.fromkeys(texts) if text_key(t) not in cache]
    new_rows = []
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i + BATCH]
        response = client.embeddings.create(model=EMBED_MODEL, input=batch)
        for text, item in zip(batch, response.data):
            cache[text_key(text)] = item.embedding
            new_rows.append({"key": text_key(text), "vector": item.embedding})
    if new_rows:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row) + "\n")
    vectors = np.array([cache[text_key(t)] for t in texts], dtype="float32")
    faiss.normalize_L2(vectors)          # length 1 -> inner product = cosine similarity
    return vectors, len(missing)


# ---------------------------------------------------------------------------
# Build, save, load, search
# ---------------------------------------------------------------------------
def build_index(client):
    chunks = [json.loads(line) for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()]
    vectors, new_calls = embed_texts(client, [c["text"] for c in chunks])

    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_PATH))
    with META_PATH.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return index, chunks, new_calls


def load_index():
    index = faiss.read_index(str(INDEX_PATH))
    chunks = [json.loads(line) for line in META_PATH.read_text(encoding="utf-8").splitlines()]
    return index, chunks


def search(client, index, chunks, query, k=5, doc_type=None):
    """Return the k most similar chunks (optionally only one doc_type)."""
    vector, _ = embed_texts(client, [query])
    fetch = k if doc_type is None else min(len(chunks), k * 10)
    scores, ids = index.search(vector, fetch)
    results = []
    for score, i in zip(scores[0], ids[0]):
        if i < 0:
            continue
        chunk = chunks[i]
        if doc_type and chunk["doc_type"] != doc_type:
            continue
        results.append({**chunk, "score": round(float(score), 3)})
        if len(results) == k:
            break
    return results


SAMPLE_QUESTIONS = [
    "What is the carrier's liability limit per pound if no higher value is declared?",
    "How many days do I have to file a claim for a lost domestic shipment?",
    "What dimensional weight factor is used for international shipments?",
    "Which shipment was delayed by a customs hold?",
]


def main():
    parser = argparse.ArgumentParser(description="Build the FAISS index and run sample searches.")
    parser.add_argument("--query", help="search for this question instead of the samples")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY is missing. Add it to the .env file.")
    client = OpenAI(api_key=OPENAI_API_KEY, max_retries=3, timeout=60)

    index, chunks, new_calls = build_index(client)
    print(f"Indexed {index.ntotal} chunks, {index.d} dimensions -> {INDEX_PATH}")
    print(f"New embeddings computed: {new_calls} (the rest came from the cache)")

    for question in [args.query] if args.query else SAMPLE_QUESTIONS:
        print(f"\nQ: {question}")
        for r in search(client, index, chunks, question, k=3):
            print(f"  {r['score']:.3f}  {r['chunk_id']:<28} {r['text'][:110]}...")


if __name__ == "__main__":
    main()