# scripts/query_semantic_search.py
"""
Prototipe P6.1d: Query hybrid pada database kode.
Skor = 0.6 * semantik + 0.4 * keyword.
"""

import os
import re
import sqlite3
import sys

import sqlite_vec
from sentence_transformers import SentenceTransformer

DB_PATH = "scripts/semantic_search_proto.db"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
CANDIDATE_POOL = 30
TOP_K_RESULTS = 5


def extract_terms(query):
    return list(set(re.findall(r"\b[a-zA-Z]{3,}\b", query.lower())))


def keyword_score(content, terms):
    if not terms:
        return 0.0
    low = content.lower()
    return sum(1 for t in terms if t in low) / len(terms)


def main():
    if len(sys.argv) < 2:
        print('Penggunaan: python scripts/query_semantic_search.py "pertanyaan"')
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f'Query: "{query}"')

    if not os.path.exists(DB_PATH):
        print(f"Error: Database tidak ditemukan di {DB_PATH}.")
        sys.exit(1)

    print("Memuat model embedding...")
    model = SentenceTransformer(MODEL_NAME)
    query_embedding = model.encode(query, show_progress_bar=False)
    terms = extract_terms(query)

    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    query_vector = sqlite_vec.serialize_float32(query_embedding)
    cursor = db.cursor()
    candidates = cursor.execute(
        """
        SELECT chunks.path, chunks.chunk_index, chunks.content, vec_items.distance
        FROM vec_items
        JOIN chunks ON chunks.rowid = vec_items.rowid
        WHERE vec_items.embedding MATCH ? AND k = ?
        ORDER BY vec_items.distance
        """,
        (query_vector, CANDIDATE_POOL),
    ).fetchall()
    db.close()

    scored = []
    for path, chunk_idx, content, distance in candidates:
        sem_score = 1.0 / (1.0 + distance)
        kw = keyword_score(content, terms)
        total = (sem_score * 0.6) + (kw * 0.4)
        scored.append((total, path, chunk_idx, content, distance, sem_score, kw))

    scored.sort(reverse=True)
    results = scored[:TOP_K_RESULTS]

    print("-" * 60)
    print(f"Menampilkan {len(results)} hasil teratas:\n")
    for i, (total, path, chunk_idx, content, distance, sem, kw) in enumerate(results):
        print(f"--- Hasil #{i+1} ---")
        print(f"  Path      : {path} (chunk {chunk_idx})")
        print(f"  Skor      : {total:.4f} [sem: {sem:.3f}, kw: {kw:.3f}]")
        print(f"  Potongan  : {content.strip().replace(chr(10), ' ')[:200]}...")
        print()


if __name__ == "__main__":
    main()
