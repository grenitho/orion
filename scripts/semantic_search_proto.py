# scripts/semantic_search_proto.py
"""
Prototipe P6.1d: Indexing KHUSUS KODE dengan chunking yang lebih baik.
- Hanya memproses file kode (.py, .ts, .js, .tsx, .jsx).
- Chunking berdasarkan fungsi/class dengan batas ukuran.
- Menggunakan model embedding yang lebih kuat: BAAI/bge-small-en-v1.5
"""

import os
import re
import sqlite3
import sys
import time

import sqlite_vec
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = "."
DB_PATH = "scripts/semantic_search_proto.db"
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # Model yang lebih kuat untuk kode
MAX_FILE_SIZE = 1024 * 1024
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 1500
IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".orion",
    "dist",
    "build",
    "out",
    ".pytest_cache",
    ".next",
    "resources",
}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}


def get_code_files(root):
    """Hanya menghasilkan file kode (dan melewati file test)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in CODE_EXTENSIONS:
                continue
            # Lewati file test — ia berisi mock dan contoh, bukan implementasi nyata
            if filename.startswith("test_") or filename.endswith("_test.py"):
                continue
            filepath = os.path.join(dirpath, filename)
            try:
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    continue
                yield filepath
            except OSError:
                continue


def chunk_code(text, file_ext):
    """Chunking cerdas untuk file kode."""
    chunks = []

    if file_ext == ".py":
        # Pecah berdasarkan dekorator fungsi/class di level modul
        pattern = re.compile(r"^((?:async\s+)?(?:def|class)\s+\w+|@\w+)", re.MULTILINE)
    elif file_ext in {".js", ".ts", ".jsx", ".tsx"}:
        # Pecah berdasarkan fungsi/class/export
        pattern = re.compile(
            r"^((?:export\s+)?(?:async\s+)?(?:function|class|const|let)\s+\w+)",
            re.MULTILINE,
        )
    else:
        return [text] if len(text) >= MIN_CHUNK_SIZE else []

    matches = list(pattern.finditer(text))
    if not matches:
        return [text] if len(text) >= MIN_CHUNK_SIZE else []

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()

        if len(chunk) < MIN_CHUNK_SIZE:
            continue

        # Jika terlalu panjang, pecah lagi dengan overlap kecil
        if len(chunk) > MAX_CHUNK_SIZE:
            overlap = 150
            for j in range(0, len(chunk), MAX_CHUNK_SIZE - overlap):
                sub = chunk[j : j + MAX_CHUNK_SIZE]
                if len(sub) >= MIN_CHUNK_SIZE:
                    chunks.append(sub)
        else:
            chunks.append(chunk)

    return chunks


def main():
    print("=== Memulai Indexing KHUSUS KODE (Prototipe P6.1d) ===")
    start_time = time.time()

    if os.path.exists(DB_PATH):
        print(f"Menghapus database lama: {DB_PATH}")
        os.remove(DB_PATH)

    print(f"Memuat model '{MODEL_NAME}'...")
    model = SentenceTransformer(MODEL_NAME)
    embedding_dim = model.get_embedding_dimension()
    print(f"Model dimuat. Dimensi embedding: {embedding_dim}")

    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    db.execute(f"""
        CREATE VIRTUAL TABLE vec_items USING vec0(
            embedding float[{embedding_dim}]
        )
    """)
    db.execute("""
        CREATE TABLE chunks (
            rowid INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL
        )
    """)
    db.commit()

    files = list(get_code_files(PROJECT_ROOT))
    total_files = len(files)
    print(f"Ditemukan {total_files} file kode untuk diproses.")
    print("-" * 60)

    total_chunks = 0
    for i, filepath in enumerate(files):
        rel_path = os.path.relpath(filepath, PROJECT_ROOT).replace("\\", "/")
        ext = os.path.splitext(filepath)[1].lower()

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if not content.strip():
                continue

            chunks = chunk_code(content, ext)
            print(f"  [{i+1}/{total_files}] {rel_path} -> {len(chunks)} chunk(s)")

            for chunk_idx, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                embedding = model.encode(chunk, show_progress_bar=False)

                cursor = db.cursor()
                cursor.execute(
                    "INSERT INTO chunks (path, chunk_index, content) VALUES (?, ?, ?)",
                    (rel_path, chunk_idx, chunk),
                )
                rowid = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                    (rowid, sqlite_vec.serialize_float32(embedding)),
                )
                total_chunks += 1
        except Exception as e:
            print(f"    [ERROR] {rel_path}: {e}")

    db.commit()
    db.close()

    print("-" * 60)
    print(f"=== Indexing Selesai! ===")
    print(f"Total file: {total_files}")
    print(f"Total chunk: {total_chunks}")
    print(f"Durasi: {time.time() - start_time:.2f} detik.")


if __name__ == "__main__":
    main()
