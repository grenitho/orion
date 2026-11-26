"""
test_strict_worker_coding.py — tests for strict worker-only coding mode
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import subagent_manager
from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState


class _DummyContext:
    def __init__(self, history):
        self.history = list(history)

    def get_history(self):
        return list(self.history)

    def add_message(self, role, content, **extra):
        msg = {"role": role, "content": content}
        msg.update(extra)
        self.history.append(msg)

    def get_project_map(self):
        return ""

    def safe_split_index(self, history, idx):
        return idx

    def create_backup(self):
        pass

    def clear_history(self):
        self.history.clear()


class _DummyExecutor:
    def __init__(self, project_path="."):
        self.project_path = project_path
        self.writes = []
        self.edits = []

    def write_file(self, **kwargs):
        self.writes.append(kwargs)
        return "Success: written"

    def edit_file(self, **kwargs):
        self.edits.append(kwargs)
        return "Success: edited"


def _mk_choice(content="", tool_calls=None):
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
        reasoning_content=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)], model="test-model", usage=None
    )


def _mk_tc(tc_id, name, args_json):
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args_json),
    )


def test_is_strict_worker_coding_config_and_env(monkeypatch):
    assert subagent_manager.is_strict_worker_coding({}) is False
    assert (
        subagent_manager.is_strict_worker_coding({"strict_worker_coding": True}) is True
    )
    assert (
        subagent_manager.is_strict_worker_coding(
            {"strict_worker_coding": True, "subagents_enabled": False}
        )
        is False
    )

    monkeypatch.setenv("ORION_STRICT_WORKER_CODING", "true")
    assert subagent_manager.is_strict_worker_coding({}) is True
    assert (
        subagent_manager.is_strict_worker_coding({"strict_worker_coding": False})
        is False
    )
    assert subagent_manager.read_config({})["max_per_turn"] == 8


def test_strict_mode_blocks_direct_write_and_redirects_to_worker(tmp_path):
    ctx = _DummyContext([{"role": "user", "content": "lanjut"}])
    executor = _DummyExecutor(str(tmp_path))
    state = SessionState()
    ctx_msg = {"role": "user", "content": "Current project structure: empty"}

    calls_seen = []

    async def fake_llm(messages, tools=None, profile=None):
        calls_seen.append((messages, tools, profile))
        # Turn 1 (main): tries direct 1-line edit_file -> blocked
        if len(calls_seen) == 1:
            return _mk_choice(
                tool_calls=[
                    _mk_tc(
                        "tc_1",
                        "edit_file",
                        '{"file_path": "app.py", "diff": "x=1"}',
                    )
                ]
            )
        # Turn 2 (main): delegates to delegate_workers
        if len(calls_seen) == 2:
            return _mk_choice(
                tool_calls=[
                    _mk_tc(
                        "tc_2",
                        "delegate_workers",
                        '{"tasks": [{"task": "ubah x=1 di app.py", "files": ["app.py"]}]}',
                    )
                ]
            )
        # Turn 3 (worker subagent): executes edit_file inside worker
        if len(calls_seen) == 3:
            return _mk_choice(
                tool_calls=[
                    _mk_tc(
                        "tc_w1",
                        "edit_file",
                        '{"file_path": "app.py", "diff": "x=1"}',
                    )
                ]
            )
        # Turn 4 (worker subagent): finishes report
        if len(calls_seen) == 4:
            return _mk_choice(
                content="Updated app.py line 1 to x=1 as assigned in scope."
            )
        # Turn 5 (main): closes
        return _mk_choice(content="Perubahan pada app.py sudah dikerjakan oleh worker.")

    deps = Deps(
        context_manager=ctx,
        todo_manager=None,
        tool_executor=executor,
        llm_call=fake_llm,
        kirim_status=lambda t: f"data: {t}\n\n",
        config={
            "strict_worker_coding": True,
            "subagent_models": {
                "workers": {"group": "provider_1", "model": "claude-haiku-4-5"}
            },
        },
        logger=None,
        context_message=ctx_msg,
    )

    async def _run():
        frames = []
        async for frame in aliran_agent(state, deps):
            frames.append(frame)
        return frames

    frames = asyncio.run(_run())

    # Directive otomatis masuk ke context_message meski user hanya mengetik "lanjut"
    assert "[STRICT WORKER CODING MODE]" in ctx_msg["content"]
    # Direct edit_file diblokir di main loop; worker mengeksekusinya dengan profil Haiku
    assert any("Redirecting edit_file to worker sub-agent" in f for f in frames)
    assert len(executor.edits) == 1
    assert calls_seen[2][2] == {"group": "provider_1", "model": "claude-haiku-4-5"}
    # Ledger mencatat file berubah dari worker dan tidak meninggalkan edit gagal
    assert any("app.py" in entry for entry in state.files_changed_this_turn)
    assert state.edit_gagal_belum_pulih() == []
