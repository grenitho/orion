import asyncio
import json
import os
import pytest
import httpx

from app import main as orion_main
from app.services import auth
from app.services.context_manager import ContextManager
from app.services.session_state import (
    SessionState,
    jalur_file_sesi,
    normalisasi_session_id,
)
from app.services.todo_manager import TodoManager


@pytest.fixture(autouse=True)
def _isolasi_registry_global(monkeypatch, tmp_path):
    """Jaga agar registry global dan ~/.orion_last_project.json tidak bocor antar-test."""
    orig_contexts = dict(orion_main._project_contexts)
    orig_states = dict(orion_main._project_states)
    orig_locks = dict(orion_main._project_locks)
    orig_active = orion_main._active_project_path
    orig_cm = orion_main.context_manager
    orig_te = orion_main.tool_executor
    orig_tm = orion_main.todo_manager
    orig_mcp = orion_main.mcp_manager
    orig_cfg = dict(orion_main.orion_config)

    monkeypatch.setattr(
        orion_main, "LAST_PROJECT_FILE", str(tmp_path / ".orion_last_project.json")
    )
    orion_main._project_contexts.clear()
    orion_main._project_states.clear()
    orion_main._project_locks.clear()

    yield

    orion_main._project_contexts.clear()
    orion_main._project_contexts.update(orig_contexts)
    orion_main._project_states.clear()
    orion_main._project_states.update(orig_states)
    orion_main._project_locks.clear()
    orion_main._project_locks.update(orig_locks)
    orion_main._active_project_path = orig_active
    orion_main.context_manager = orig_cm
    orion_main.tool_executor = orig_te
    orion_main.todo_manager = orig_tm
    orion_main.mcp_manager = orig_mcp
    orion_main.orion_config = orig_cfg


def _auth_headers() -> dict:
    return {"X-Orion-Token": auth.get_shared_token()}


def test_normalisasi_dan_jalur_file_sesi(tmp_path):
    proj = str(tmp_path)

    assert normalisasi_session_id(None) == "default"
    assert normalisasi_session_id("") == "default"
    assert normalisasi_session_id("   default  ") == "default"

    # Karakter berbahaya / path traversal harus disanitasi
    sanitized = normalisasi_session_id("../../etc/passwd:tab-1")
    assert ".." not in sanitized
    assert "/" not in sanitized
    assert "\\" not in sanitized
    assert sanitized == "etc_passwd_tab-1"

    # Backward-compatible untuk default vs terisolasi untuk sesi bernama
    assert jalur_file_sesi(proj, "session_history.json", "default") == os.path.join(
        proj, ".orion", "session_history.json"
    )
    assert jalur_file_sesi(proj, "session_history.json", "tab-2") == os.path.join(
        proj, ".orion", "sessions", "tab-2", "session_history.json"
    )


def test_isolasi_disk_context_todo_dan_checkpoint_per_sesi(tmp_path):
    proj = str(tmp_path)

    cm_default = ContextManager(proj, session_id="default")
    cm_tab2 = ContextManager(proj, session_id="tab-2")

    cm_default.add_message("user", "pesan sesi default")
    cm_tab2.add_message("user", "pesan sesi tab 2")

    # Reload dari disk harus terpisah 100%
    cm_default_reloaded = ContextManager(proj, session_id="default")
    cm_tab2_reloaded = ContextManager(proj, session_id="tab-2")
    assert [m["content"] for m in cm_default_reloaded.get_history()] == [
        "pesan sesi default"
    ]
    assert [m["content"] for m in cm_tab2_reloaded.get_history()] == [
        "pesan sesi tab 2"
    ]

    # TodoManager terisolasi per sesi
    tm_default = TodoManager(proj, session_id="default")
    tm_tab2 = TodoManager(proj, session_id="tab-2")
    tm_default.set_todos([{"id": "1", "text": "Task default", "status": "pending"}])
    tm_tab2.set_todos([{"id": "1", "text": "Task tab 2", "status": "completed"}])

    assert TodoManager(proj, session_id="default").get_todos()[0]["text"] == "Task default"
    assert TodoManager(proj, session_id="tab-2").get_todos()[0]["text"] == "Task tab 2"

    # Checkpoint terisolasi per sesi dan bersih dari file .tmp
    st_tab2 = SessionState(session_id="tab-2")
    st_tab2.analyzed_files.add("src/app.py")
    st_tab2.catat_file_berubah("src/app.py")
    st_tab2.simpan_checkpoint(proj, catatan_terakhir="checkpoint tab 2")

    st_default_loaded = SessionState.muat_checkpoint(proj, session_id="default")
    st_tab2_loaded = SessionState.muat_checkpoint(proj, session_id="tab-2")
    assert st_default_loaded.analyzed_files == set()
    assert "src/app.py" in st_tab2_loaded.analyzed_files
    assert st_tab2_loaded._checkpoint_file_terakhir == ["src/app.py"]
    assert st_tab2_loaded.last_tool_note == "checkpoint tab 2"

    for root_dir, _dirs, files in os.walk(os.path.join(proj, ".orion")):
        assert not any(f.endswith(".tmp") for f in files), f"Sisa .tmp di {root_dir}"

    SessionState.hapus_checkpoint(proj, session_id="tab-2")
    assert SessionState.muat_checkpoint(proj, session_id="tab-2").analyzed_files == set()


@pytest.mark.anyio
async def test_evict_idle_sessions_menyimpan_checkpoint_dan_menjaga_sesi_aktif(tmp_path):
    proj = str(tmp_path)
    proj_ctx = orion_main._get_or_create_project_context(proj)

    s_default = proj_ctx.get_or_create_session("default")
    s_idle = proj_ctx.get_or_create_session("idle-tab")
    s_idle.state.analyzed_files.add("modul_lama.py")

    s_busy = proj_ctx.get_or_create_session("busy-tab")
    await s_busy.lock.acquire()
    try:
        evicted = proj_ctx.evict_idle_sessions(
            now=s_idle.last_accessed + 3600.0, ttl=60.0
        )
    finally:
        s_busy.lock.release()

    assert evicted == ["idle-tab"]
    assert "idle-tab" not in proj_ctx.sessions
    assert "default" in proj_ctx.sessions
    assert "busy-tab" in proj_ctx.sessions
    assert s_default.session_id == "default"

    # Checkpoint sesi yang di-evict harus tersimpan di disk & pulih saat diakses lagi
    restored = proj_ctx.get_or_create_session("idle-tab")
    assert "modul_lama.py" in restored.state.analyzed_files


@pytest.mark.anyio
async def test_multi_project_dan_multi_session_chat_serta_concurrency_lock(
    monkeypatch, tmp_path
):
    proj_a = tmp_path / "project_alpha"
    proj_b = tmp_path / "project_beta"
    (proj_a / "a.py").parent.mkdir(parents=True)
    (proj_b / "b.py").parent.mkdir(parents=True)
    (proj_a / "a.py").write_text("print('A')\n", encoding="utf-8")
    (proj_b / "b.py").write_text("print('B')\n", encoding="utf-8")

    # Matikan MCP startup nyata agar test cepat & deterministik
    monkeypatch.setattr(orion_main, "MCPManager", None)
    monkeypatch.setattr(orion_main, "mcp_manager", None)

    async def _noop_auto_index(*args, **kwargs):
        return None

    monkeypatch.setattr(orion_main, "_trigger_semantic_auto_index", _noop_auto_index)

    active_in_key: dict[tuple[str, str], int] = {}
    max_concurrent_in_key: dict[tuple[str, str], int] = {}
    total_active = 0
    max_total_concurrent = 0

    async def fake_aliran_agent(state, deps):
        nonlocal total_active, max_total_concurrent
        proj_name = os.path.basename(deps.tool_executor.project_path)
        sid = state.session_id
        key = (proj_name, sid)
        active_in_key[key] = active_in_key.get(key, 0) + 1
        max_concurrent_in_key[key] = max(
            max_concurrent_in_key.get(key, 0), active_in_key[key]
        )
        total_active += 1
        max_total_concurrent = max(max_total_concurrent, total_active)
        try:
            await asyncio.sleep(0.05)
            last_user = deps.context_manager.get_history()[-1]["content"]
            reply = f"[{proj_name}:{sid}] {last_user}"
            deps.context_manager.add_message("assistant", reply)
            yield orion_main.sse.ev("chunk", text=reply)
        finally:
            active_in_key[key] -= 1
            total_active -= 1

    monkeypatch.setattr(orion_main, "aliran_agent", fake_aliran_agent)

    transport = httpx.ASGITransport(app=orion_main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=_auth_headers()
    ) as client:
        # 1. Buka Project A lalu Project B (simulasi 2 jendela VS Code)
        res_a = await client.post("/set-project", json={"path": str(proj_a)})
        res_b = await client.post("/set-project", json={"path": str(proj_b)})
        assert res_a.status_code == 200 and res_a.json()["success"] is True
        assert res_b.status_code == 200 and res_b.json()["success"] is True

        # 2. Kirim chat paralel ke Project A (sesi-1 & sesi-2) dan Project B (sesi-1),
        # ditambah 2 request bersamaan pada (Project A, sesi-1) untuk menguji lock per sesi.
        responses = await asyncio.gather(
            client.post(
                "/chat",
                json={
                    "message": "halo A1-pertama",
                    "project_path": str(proj_a),
                    "session_id": "sesi-1",
                },
            ),
            client.post(
                "/chat",
                json={
                    "message": "halo A1-kedua",
                    "project_path": str(proj_a),
                    "session_id": "sesi-1",
                },
            ),
            client.post(
                "/chat",
                json={
                    "message": "halo A2",
                    "project_path": str(proj_a),
                    "session_id": "sesi-2",
                },
            ),
            client.post(
                "/chat",
                json={
                    "message": "halo B1",
                    "project_path": str(proj_b),
                    "session_id": "sesi-1",
                },
            ),
        )
        for r in responses:
            assert r.status_code == 200
            assert '"type": "done"' in r.text

        # Lock per (project_path, session_id) memastikan maksimal 1 eksekusi serentak per sesi,
        # sementara proyek/sesi berbeda tetap berjalan paralel (>= 2).
        assert max_concurrent_in_key[("project_alpha", "sesi-1")] == 1
        assert max_total_concurrent >= 2

        # Verifikasi file history di disk terisolasi per proyek & per session_id
        hist_a1 = json.loads(
            (proj_a / ".orion" / "sessions" / "sesi-1" / "session_history.json").read_text(
                encoding="utf-8"
            )
        )
        hist_a2 = json.loads(
            (proj_a / ".orion" / "sessions" / "sesi-2" / "session_history.json").read_text(
                encoding="utf-8"
            )
        )
        hist_b1 = json.loads(
            (proj_b / ".orion" / "sessions" / "sesi-1" / "session_history.json").read_text(
                encoding="utf-8"
            )
        )

        assert len(hist_a1) == 4  # 2 user + 2 assistant
        assert all("[project_alpha:sesi-1]" in m["content"] for m in hist_a1 if m["role"] == "assistant")
        assert len(hist_a2) == 2
        assert "[project_alpha:sesi-2]" in hist_a2[1]["content"]
        assert len(hist_b1) == 2
        assert "[project_beta:sesi-1]" in hist_b1[1]["content"]

        # 3. Clear chat hanya pada (Project A, sesi-1) — sesi-2 di Project A harus tetap utuh
        clr = await client.post(
            "/clear-chat",
            json={"project_path": str(proj_a), "session_id": "sesi-1"},
        )
        assert clr.status_code == 200
        assert clr.json()["session_id"] == "sesi-1"

        hist_a1_after = json.loads(
            (proj_a / ".orion" / "sessions" / "sesi-1" / "session_history.json").read_text(
                encoding="utf-8"
            )
        )
        hist_a2_after = json.loads(
            (proj_a / ".orion" / "sessions" / "sesi-2" / "session_history.json").read_text(
                encoding="utf-8"
            )
        )
        assert hist_a1_after == []
        assert len(hist_a2_after) == 2
