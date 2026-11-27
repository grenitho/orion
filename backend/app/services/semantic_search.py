"""
semantic_search.py — Pencarian semantik untuk Orion (P6.3 + P6.4 + P6.5).

Class `SemanticSearchEngine`:
  * index_project(force=False) — inkremental; hanya file berubah di-index ulang.
  * index_file(file_path) / remove_file(file_path) — sinkronisasi real-time per file.
  * search(query, top_k) — hybrid (semantik 0.6 + keyword 0.4).

P6.4/P6.5 incremental: tabel `indexed_files(path, mtime, size)` menyimpan sidik
per file. File yang mtime+size sama dilewati; file berubah → chunk lama
dihapus lalu re-index; file hilang → baris metadata + chunk dihapus.
"""

import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

try:
    import sqlite_vec
    from sentence_transformers import SentenceTransformer

    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001
    sqlite_vec = None
    SentenceTransformer = None
    _IMPORT_ERROR = f"{type(e).__name__}: {e}"


MODEL_NAME = "BAAI/bge-small-en-v1.5"
INDEX_FILENAME = "semantic_index.db"
MAX_FILE_SIZE = 1024 * 1024
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 1500
CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}
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
    "scripts",
}
DEFAULT_CANDIDATE_POOL = 30
DEFAULT_TOP_K = 5


def _extract_terms(query: str) -> list:
    return list(set(re.findall(r"\b[a-zA-Z]{3,}\b", query.lower())))


def _keyword_score(content: str, terms: list) -> float:
    if not terms:
        return 0.0
    low = content.lower()
    return sum(1 for t in terms if t in low) / len(terms)


def _chunk_code(text: str, file_ext: str) -> list:
    if file_ext == ".py":
        pattern = re.compile(r"^((?:async\s+)?(?:def|class)\s+\w+|@\w+)", re.MULTILINE)
    elif file_ext in {".js", ".ts", ".jsx", ".tsx"}:
        pattern = re.compile(
            r"^((?:export\s+)?(?:async\s+)?(?:function|class|const|let)\s+\w+)",
            re.MULTILINE,
        )
    else:
        return [text] if len(text) >= MIN_CHUNK_SIZE else []

    matches = list(pattern.finditer(text))
    if not matches:
        return [text] if len(text) >= MIN_CHUNK_SIZE else []

    chunks = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if len(chunk) < MIN_CHUNK_SIZE:
            continue
        if len(chunk) > MAX_CHUNK_SIZE:
            overlap = 150
            for j in range(0, len(chunk), MAX_CHUNK_SIZE - overlap):
                sub = chunk[j : j + MAX_CHUNK_SIZE]
                if len(sub) >= MIN_CHUNK_SIZE:
                    chunks.append(sub)
        else:
            chunks.append(chunk)
    return chunks


def _schema_indexed_files(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS indexed_files ("
        "path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL)"
    )


def _file_stat(path: Path) -> tuple:
    st = path.stat()
    return float(st.st_mtime), int(st.st_size)


class SemanticSearchEngine:
    def __init__(self, project_path: str, logger: Optional[logging.Logger] = None):
        self.project_path = Path(project_path).resolve()
        self.index_path = self.project_path / ".orion" / INDEX_FILENAME
        self.log = logger or logging.getLogger(__name__)
        self._model = None
        self._import_error = _IMPORT_ERROR
        self._available = _IMPORT_ERROR is None
        self._lock = threading.RLock()
        self._model_lock = threading.Lock()

    def _load_model(self):
        if self._model is None and self._available:
            with self._model_lock:
                if self._model is None:
                    self.log.info(f"Memuat model embedding: {MODEL_NAME}")
                    self._model = SentenceTransformer(MODEL_NAME)
        return self._model

    def is_available(self) -> bool:
        return self._available

    def _open_db(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.index_path), timeout=30.0)
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        return db

    def _ensure_schema(self, db, dim: int) -> None:
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items "
            f"USING vec0(embedding float[{dim}])"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS chunks (rowid INTEGER PRIMARY KEY, "
            "path TEXT NOT NULL, chunk_index INTEGER NOT NULL, content TEXT NOT NULL)"
        )
        _schema_indexed_files(db)
        db.commit()

    def _normalize_rel_path(self, file_path: str) -> Optional[tuple[str, Path]]:
        raw = re.sub(r"^\./+", "", file_path.replace("\\", "/")).strip("/")
        if not raw:
            return None
        fpath = (self.project_path / raw).resolve()
        if self.project_path not in fpath.parents:
            return None
        rel = str(fpath.relative_to(self.project_path)).replace("\\", "/")
        return rel, fpath

    def index_project(self, force: bool = False) -> str:
        if not self._available:
            return f"[SEMANTIC_SEARCH UNAVAILABLE] {self._import_error}"
        try:
            model = self._load_model()
            if model is None:
                return "[SEMANTIC_SEARCH ERROR] Gagal memuat model."
            dim = model.get_embedding_dimension()

            with self._lock:
                if force and self.index_path.exists():
                    self.index_path.unlink()
                db = self._open_db()
                try:
                    self._ensure_schema(db, dim)
                    known = {
                        row[0]: (row[1], row[2])
                        for row in db.execute(
                            "SELECT path, mtime, size FROM indexed_files"
                        )
                    }
                finally:
                    db.close()

            seen = set()
            n_skip = 0
            pending_updates = []
            for fpath in self._get_code_files():
                rel = str(fpath.relative_to(self.project_path)).replace("\\", "/")
                seen.add(rel)
                try:
                    mtime, size = _file_stat(fpath)
                except OSError:
                    continue
                if known.get(rel) == (mtime, size):
                    n_skip += 1
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError as e:
                    self.log.warning(f"Gagal baca {rel}: {e}")
                    continue

                chunks = _chunk_code(text, fpath.suffix.lower())
                encoded = [
                    (
                        idx,
                        chunk,
                        sqlite_vec.serialize_float32(
                            model.encode(chunk, show_progress_bar=False)
                        ),
                    )
                    for idx, chunk in enumerate(chunks)
                    if chunk.strip()
                ]
                pending_updates.append((rel, mtime, size, encoded))

            n_new = n_upd = n_del = 0
            with self._lock:
                db = self._open_db()
                try:
                    self._ensure_schema(db, dim)
                    for rel, mtime, size, encoded in pending_updates:
                        self._delete_rel_from_db(db, rel)
                        for idx, chunk, vec_blob in encoded:
                            cur = db.cursor()
                            cur.execute(
                                "INSERT INTO chunks (path, chunk_index, content) VALUES (?, ?, ?)",
                                (rel, idx, chunk),
                            )
                            cur.execute(
                                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                                (cur.lastrowid, vec_blob),
                            )
                        db.execute(
                            "INSERT OR REPLACE INTO indexed_files (path, mtime, size) VALUES (?, ?, ?)",
                            (rel, mtime, size),
                        )
                        if rel in known:
                            n_upd += 1
                        else:
                            n_new += 1
                        self.log.info(f"[SEMANTIC_INDEX] {rel}: {len(encoded)} chunk")

                    for old_rel in list(known.keys()):
                        if old_rel not in seen:
                            self._delete_rel_from_db(db, old_rel)
                            n_del += 1
                    db.commit()
                finally:
                    db.close()

            return (
                f"[SEMANTIC_INDEX OK] incremental — new:{n_new} updated:{n_upd} "
                f"deleted:{n_del} unchanged:{n_skip} → {self.index_path}"
            )
        except Exception as e:  # noqa: BLE001
            return f"[SEMANTIC_SEARCH ERROR] Indexing gagal: {type(e).__name__}: {e}"

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> str:
        if not self._available:
            return f"[SEMANTIC_SEARCH UNAVAILABLE] {self._import_error}"
        if not self.index_path.exists():
            return "[SEMANTIC_SEARCH ERROR] Index belum dibuat. Jalankan index_semantic_search dulu."
        try:
            model = self._load_model()
            if model is None:
                return "[SEMANTIC_SEARCH ERROR] Gagal memuat model."
            qemb = model.encode(query, show_progress_bar=False)
            terms = _extract_terms(query)

            with self._lock:
                db = self._open_db()
                try:
                    cur = db.cursor()
                    candidates = cur.execute(
                        "SELECT c.path, c.chunk_index, c.content, v.distance "
                        "FROM vec_items v JOIN chunks c ON c.rowid = v.rowid "
                        "WHERE v.embedding MATCH ? AND k = ? "
                        "ORDER BY v.distance",
                        (sqlite_vec.serialize_float32(qemb), DEFAULT_CANDIDATE_POOL),
                    ).fetchall()
                finally:
                    db.close()

            scored = []
            for path, cidx, content, dist in candidates:
                sem = 1.0 / (1.0 + dist)
                kw = _keyword_score(content, terms)
                scored.append(((sem * 0.6) + (kw * 0.4), path, cidx, content, sem, kw))
            scored.sort(reverse=True)
            results = scored[:top_k]
            if not results:
                return f"[SEMANTIC_SEARCH] Tidak ada hasil untuk: {query!r}"
            lines = [f"[SEMANTIC_SEARCH RESULTS] Query: {query!r}"]
            for i, (total, path, cidx, content, sem, kw) in enumerate(results):
                preview = content.strip().replace("\n", " ")[:300]
                lines.append(
                    f"\n--- #{i+1} (skor {total:.3f}, sem {sem:.3f}, kw {kw:.3f}) ---"
                )
                lines.append(f"Path: {path} (chunk {cidx})")
                lines.append(f"Cuplikan: {preview}...")
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"[SEMANTIC_SEARCH ERROR] Query gagal: {type(e).__name__}: {e}"

    def _get_code_files(self):
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in files:
                ext = Path(name).suffix.lower()
                if ext not in CODE_EXTENSIONS:
                    continue
                if name.startswith("test_") or name.endswith("_test.py"):
                    continue
                full = Path(root) / name
                try:
                    if full.stat().st_size > MAX_FILE_SIZE:
                        continue
                    yield full
                except OSError:
                    continue

    def _delete_rel_from_db(self, db, rel: str) -> None:
        old_ids = [
            r[0] for r in db.execute("SELECT rowid FROM chunks WHERE path = ?", (rel,))
        ]
        for rid in old_ids:
            db.execute("DELETE FROM vec_items WHERE rowid = ?", (rid,))
            db.execute("DELETE FROM chunks WHERE rowid = ?", (rid,))
        db.execute("DELETE FROM indexed_files WHERE path = ?", (rel,))

    def remove_file(self, file_path: str) -> None:
        if not self._available or not self.index_path.exists():
            return
        norm = self._normalize_rel_path(file_path)
        if norm is None:
            return
        rel, _ = norm
        with self._lock:
            db = self._open_db()
            try:
                self._delete_rel_from_db(db, rel)
                db.commit()
            finally:
                db.close()

    def index_file(self, file_path: str) -> None:
        if not self._available or not self.index_path.exists():
            return
        norm = self._normalize_rel_path(file_path)
        if norm is None:
            return
        rel, fpath = norm
        if any(part in IGNORED_DIRS for part in Path(rel).parts[:-1]):
            return
        if not fpath.is_file() or fpath.suffix.lower() not in CODE_EXTENSIONS:
            return
        if fpath.name.startswith("test_") or fpath.name.endswith("_test.py"):
            return
        try:
            mtime, size = _file_stat(fpath)
            if size > MAX_FILE_SIZE:
                return
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return

        model = self._load_model()
        if model is None:
            return
        dim = model.get_embedding_dimension()
        chunks = _chunk_code(text, fpath.suffix.lower())
        encoded = [
            (
                idx,
                chunk,
                sqlite_vec.serialize_float32(
                    model.encode(chunk, show_progress_bar=False)
                ),
            )
            for idx, chunk in enumerate(chunks)
            if chunk.strip()
        ]

        with self._lock:
            db = self._open_db()
            try:
                self._ensure_schema(db, dim)
                self._delete_rel_from_db(db, rel)
                for idx, chunk, vec_blob in encoded:
                    cur = db.cursor()
                    cur.execute(
                        "INSERT INTO chunks (path, chunk_index, content) VALUES (?, ?, ?)",
                        (rel, idx, chunk),
                    )
                    cur.execute(
                        "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                        (cur.lastrowid, vec_blob),
                    )
                db.execute(
                    "INSERT OR REPLACE INTO indexed_files (path, mtime, size) VALUES (?, ?, ?)",
                    (rel, mtime, size),
                )
                db.commit()
            finally:
                db.close()
