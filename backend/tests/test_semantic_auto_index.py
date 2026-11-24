import asyncio
from unittest.mock import MagicMock
import pytest
from app import main
from app.services.tool_executor import ToolExecutor


def test_trigger_semantic_auto_index_respects_flag(monkeypatch):
    calls = []
    dummy_executor = MagicMock()
    dummy_executor.index_semantic_search.side_effect = lambda: calls.append("indexed") or "ok"

    monkeypatch.setattr(main, "tool_executor", dummy_executor)

    # 1. Saat semantic_auto_index = False -> tidak boleh berjalan
    monkeypatch.setitem(main.orion_config, "semantic_auto_index", False)
    asyncio.run(main._trigger_semantic_auto_index("test-off"))
    assert calls == []

    # 2. Saat semantic_auto_index = True -> harus memanggil index_semantic_search
    monkeypatch.setitem(main.orion_config, "semantic_auto_index", True)
    asyncio.run(main._trigger_semantic_auto_index("test-on"))
    assert calls == ["indexed"]


def test_tool_executor_syncs_index_only_when_db_exists(tmp_path, monkeypatch):
    executor = ToolExecutor(str(tmp_path))
    events = []

    engine = executor._get_semantic_engine()
    monkeypatch.setattr(engine, "index_file", lambda p: events.append(("index", p)))
    monkeypatch.setattr(engine, "remove_file", lambda p: events.append(("remove", p)))

    # 1. Belum ada semantic_index.db -> mutasi file tidak boleh memicu sync
    executor.write_file("app.py", "def hello():\n    return 1\n")
    assert events == []

    # 2. Simulasikan semantic_index.db sudah ada
    engine.index_path.parent.mkdir(parents=True, exist_ok=True)
    engine.index_path.write_text("dummy")

    executor.write_file("main.py", "def run():\n    return True\n")
    executor.edit_file(
        "main.py",
        "<<<<<<< SEARCH\n    return True\n=======\n    return False\n>>>>>>> REPLACE",
    )
    executor.move_file("main.py", "core.py")
    executor.delete_file("core.py")

    assert events == [
        ("index", "main.py"),
        ("index", "main.py"),
        ("remove", "main.py"),
        ("index", "core.py"),
        ("remove", "core.py"),
    ]