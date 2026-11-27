import struct
import sqlite3
from unittest.mock import patch

import pytest

from app.services import semantic_search
from app.services.semantic_search import SemanticSearchEngine
from app.services.tool_executor import ToolExecutor


class _FakeModel:
    def get_embedding_dimension(self) -> int:
        return 4

    def encode(self, text: str, show_progress_bar: bool = False):
        val = float((len(text) % 7) + 1) / 10.0
        return [val, 0.2, 0.3, 0.4]


class _FakeSqliteVec:
    @staticmethod
    def load(db: sqlite3.Connection) -> None:
        pass

    @staticmethod
    def serialize_float32(vec) -> bytes:
        return struct.pack(f"{len(vec)}f", *[float(x) for x in vec])


def _patch_engine_schema(monkeypatch):
    monkeypatch.setattr(semantic_search, "_IMPORT_ERROR", None)
    monkeypatch.setattr(semantic_search, "sqlite_vec", _FakeSqliteVec)
    monkeypatch.setattr(
        semantic_search, "SentenceTransformer", lambda _name: _FakeModel()
    )

    def _fake_ensure_schema(self, db, dim: int) -> None:
        db.execute(
            "CREATE TABLE IF NOT EXISTS vec_items ("
            "rowid INTEGER PRIMARY KEY, embedding BLOB)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS chunks (rowid INTEGER PRIMARY KEY, "
            "path TEXT NOT NULL, chunk_index INTEGER NOT NULL, content TEXT NOT NULL)"
        )
        semantic_search._schema_indexed_files(db)
        db.commit()

    monkeypatch.setattr(SemanticSearchEngine, "_ensure_schema", _fake_ensure_schema)


def _indexed_paths(db_path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in con.execute("SELECT path FROM indexed_files")}
    finally:
        con.close()


def test_semantic_engine_has_lock_and_incremental_file_hooks(tmp_path, monkeypatch):
    _patch_engine_schema(monkeypatch)

    src_dir = tmp_path / "app"
    src_dir.mkdir()
    initial = src_dir / "billing.py"
    initial.write_text(
        "def calculate_invoice_total(items):\n    return sum(i.price for i in items) * 1.11\n"
        * 3,
        encoding="utf-8",
    )

    ex = ToolExecutor(str(tmp_path))
    eng = ex._get_semantic_engine()
    assert hasattr(eng, "_lock")

    res = ex.index_semantic_search()
    assert "[SEMANTIC_INDEX OK]" in res
    assert _indexed_paths(eng.index_path) == {"app/billing.py"}

    # write_file otomatis mengindeks file baru & melewati IGNORED_DIRS
    w_res = ex.write_file(
        "app/tax.py",
        "def compute_vat(amount: float) -> float:\n    return round(amount * 0.11, 2)\n"
        * 3,
    )
    assert w_res.startswith("Success:")
    ex.write_file(
        "dist/bundle.js",
        "function bundledCode() { return 'ignored artifact directory content'; }\n"
        * 4,
    )
    assert _indexed_paths(eng.index_path) == {"app/billing.py", "app/tax.py"}

    # edit_file memperbarui mtime/size di indexed_files
    e_res = ex.edit_file(
        "app/tax.py",
        "<<<<<<< SEARCH\ndef compute_vat(amount: float) -> float:\n    return round(amount * 0.11, 2)\n======="
        "\ndef compute_vat(amount: float) -> float:\n    return round(amount * 0.12, 2)\n>>>>>>> REPLACE",
    )
    assert e_res.startswith("Success:")
    assert "app/tax.py" in _indexed_paths(eng.index_path)

    # move_file menghapus path lama & mengindeks path baru
    m_res = ex.move_file("app/tax.py", "app/vat.py")
    assert m_res.startswith("Success:")
    assert _indexed_paths(eng.index_path) == {"app/billing.py", "app/vat.py"}

    # delete_file menghapus entri dari indeks
    d_res = ex.delete_file("app/vat.py")
    assert d_res.startswith("Success:")
    assert _indexed_paths(eng.index_path) == {"app/billing.py"}


def test_trigger_semantic_auto_index_deduplicates_and_respects_flag(tmp_path, monkeypatch):
    import asyncio
    import time
    from app import main as app_main

    calls = []

    class _DummyExec:
        project_path = str(tmp_path)

        def index_semantic_search(self):
            calls.append(self.project_path)
            time.sleep(0.05)
            return "[SEMANTIC_INDEX OK]"

    async def _run():
        monkeypatch.setattr(app_main, "tool_executor", _DummyExec())
        monkeypatch.setitem(app_main.orion_config, "semantic_auto_index", False)
        await app_main._trigger_semantic_auto_index("disabled-check")
        assert calls == []

        monkeypatch.setitem(app_main.orion_config, "semantic_auto_index", True)
        await asyncio.gather(
            app_main._trigger_semantic_auto_index("auto-restore"),
            app_main._trigger_semantic_auto_index("set-project"),
        )
        assert len(calls) == 1

        # Setelah selesai, kunci proyek dilepas sehingga trigger berikutnya bisa jalan
        await app_main._trigger_semantic_auto_index("re-index")
        assert len(calls) == 2

    asyncio.run(_run())
