"""
test_subagent_manager.py — unit tests for sub-agent orchestration (UPDATE-016)
===============================================================================
All fakes: no subprocess, no network, no disk history. Guarded contracts:

  * pure guards: disjoint-scope check (normpath-aware), config clamping;
  * one sub-run: role tool allowances, write-scope enforcement, iteration
    cap, empty-response and exception paths never raise;
  * isolation: each sub-run owns an in-memory MiniContext (the project's
    session_history.json is never touched by design - there is no handle
    to it inside subagent_manager at all);
  * dispatch: enabled flag, per-turn budget via SessionState, worker cap,
    overlap rejection BEFORE any work, fixed-order merged worker reports;
  * agent-loop branch: two frozen 'status' frames per delegation, the
    report lands as an ordinary tool result, budget counter increments.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from app.services import subagent_manager as sa
from app.services.session_state import SessionState


# --------------------------------------------------------------------------- fakes

class FakeExecutor:
    def __init__(self, project_path="."):
        self.calls = []
        self.project_path = project_path

    def read_file(self, file_path="", **kw):
        self.calls.append(("read_file", file_path))
        return f"content of {file_path}"

    def read_file_range(self, file_path="", start_line=1, end_line=10, **kw):
        self.calls.append(("read_file_range", file_path))
        return f"lines of {file_path}"

    def search_code(self, query="", **kw):
        self.calls.append(("search_code", query))
        return "no matches"

    def list_files(self, path=".", **kw):
        self.calls.append(("list_files", path))
        return "seed.txt"

    def write_file(self, file_path="", content="", **kw):
        self.calls.append(("write_file", file_path))
        return f"Success: wrote {file_path}"

    def edit_file(self, file_path="", **kw):
        self.calls.append(("edit_file", file_path))
        return f"Success: edited {file_path}"

    def verify_code(self, file_path="", **kw):
        self.calls.append(("verify_code", file_path))
        return "no problems found"


class ScriptedLLM:
    """Pops one scripted response per call; records the messages it saw."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def __call__(self, messages=None, tools=None):
        # yield once so parallel sub-runs interleave like real network calls
        await asyncio.sleep(0)
        self.seen.append(messages)
        entry = self.script.pop(0) if self.script else {"content": "fallback report " * 6}
        msg = SimpleNamespace(
            content=entry.get("content"),
            tool_calls=entry.get("tool_calls"),
            reasoning_content=None,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model="mock")


def tc(name, args, idx=1):
    return SimpleNamespace(
        id=f"call_{idx}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


LONG = "This is a sufficiently long sub-agent report sentence, well over the minimum."


# ------------------------------------------------------------------- pure guards

def test_check_scopes_disjoint():
    assert sa.check_scopes_disjoint([["a.txt"], ["b.txt"]]) is None
    assert sa.check_scopes_disjoint([[], []]) is None
    err = sa.check_scopes_disjoint([["a.txt"], ["a.txt"]])
    assert err and err.startswith(sa.PREFIX_SCOPE) and "workers 1 and 2" in err
    # normpath-aware: same file via different spelling
    assert sa.check_scopes_disjoint([["pkg/a.txt"], ["pkg/./a.txt"]]) is not None


def test_read_config_default_dan_clamp():
    cfg = sa.read_config(None)
    assert cfg == {
        "enabled": True,
        "max_iter": sa.DEFAULT_MAX_ITER,
        "max_per_turn": sa.DEFAULT_MAX_PER_TURN,
        "workers_max": sa.DEFAULT_WORKERS_MAX,
    }
    cfg = sa.read_config({
        "subagents_enabled": False,
        "subagent_max_iterations": 999,
        "subagent_max_per_turn": 0,
        "subagent_workers_max": True,
    })
    assert cfg["enabled"] is False
    assert cfg["max_iter"] == 30
    assert cfg["max_per_turn"] == 1  # 0 clamped to the floor, not to "unlimited"
    assert cfg["workers_max"] == sa.DEFAULT_WORKERS_MAX  # bool rejected


def test_role_dan_predicate():
    assert sa.is_subagent_tool("delegate_explorer")
    assert not sa.is_subagent_tool("read_file")
    assert sa.role_of_tool("delegate_workers") == sa.WORKER


# ------------------------------------------------------------------- one sub-run

def test_subrun_explorer_ok():
    executor = FakeExecutor()
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("read_file", {"file_path": "seed.txt"})]},
        {"content": LONG},
    ])
    out = asyncio.run(sa.run_subagent(
        sa.EXPLORER, "inspect", llm_call=llm, tool_executor=executor, max_iter=4
    ))
    assert out["status"] == "ok" and out["iterations"] == 2 and out["tool_calls"] == 1
    assert ("read_file", "seed.txt") in executor.calls
    assert out["summary"] == LONG


def test_subrun_tool_terlarang_ditangkap_guard():
    executor = FakeExecutor()
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("write_file", {"file_path": "x.txt", "content": "x"})]},
        {"content": LONG},
    ])
    out = asyncio.run(sa.run_subagent(
        sa.EXPLORER, "inspect", llm_call=llm, tool_executor=executor, max_iter=4
    ))
    assert out["status"] == "ok"
    assert executor.calls == []  # the write never reached the executor
    # the guard string reached the sub-agent's own history as a tool result
    tool_msgs = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert any(sa.PREFIX_GUARD in m["content"] for m in tool_msgs)


def test_subrun_worker_scope_ditegakkan():
    executor = FakeExecutor()
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("write_file", {"file_path": "evil.txt", "content": "x"})]},
        {"content": LONG},
    ])
    out = asyncio.run(sa.run_subagent(
        sa.WORKER, "work", llm_call=llm, tool_executor=executor,
        max_iter=4, scope=["ok.txt"],
    ))
    assert out["status"] == "ok"
    assert executor.calls == []
    tool_msgs = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert any(sa.PREFIX_SCOPE in m["content"] for m in tool_msgs)

    executor2 = FakeExecutor()
    llm2 = ScriptedLLM([
        {"content": None, "tool_calls": [tc("write_file", {"file_path": "ok.txt", "content": "x"})]},
        {"content": LONG},
    ])
    out2 = asyncio.run(sa.run_subagent(
        sa.WORKER, "work", llm_call=llm2, tool_executor=executor2,
        max_iter=4, scope=["ok.txt"],
    ))
    assert ("write_file", "ok.txt") in executor2.calls and out2["status"] == "ok"


def test_subrun_cap_dan_jawaban_kosong():
    executor = FakeExecutor()
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("list_files", {})]},
        {"content": None, "tool_calls": [tc("list_files", {})]},
    ])
    out = asyncio.run(sa.run_subagent(
        sa.EXPLORER, "never ends", llm_call=llm, tool_executor=executor, max_iter=2
    ))
    assert out["status"] == "capped" and out["iterations"] == 2


def test_subrun_model_mati_tidak_raise():
    executor = FakeExecutor()

    async def boom(**kw):
        raise RuntimeError("router terbakar")

    out = asyncio.run(sa.run_subagent(
        sa.EXPLORER, "x", llm_call=boom, tool_executor=executor, max_iter=2
    ))
    assert out["status"] == "error" and sa.PREFIX_ERROR in out["summary"]


def test_subrun_choices_kosong_tidak_crash():
    executor = FakeExecutor()

    async def kosong(**kw):
        return SimpleNamespace(choices=[], usage=None, model="mock")

    out = asyncio.run(sa.run_subagent(
        sa.EXPLORER, "x", llm_call=kosong, tool_executor=executor, max_iter=2
    ))
    assert out["status"] in ("capped", "error")


def test_subrun_paralel_history_terisolasi():
    executor = FakeExecutor()
    llm_a = ScriptedLLM([{"content": LONG + " A"}])
    llm_b = ScriptedLLM([{"content": LONG + " B"}])

    async def both():
        return await asyncio.gather(
            sa.run_subagent(sa.EXPLORER, "A", llm_call=llm_a, tool_executor=executor),
            sa.run_subagent(sa.EXPLORER, "B", llm_call=llm_b, tool_executor=executor),
        )

    ra, rb = asyncio.run(both())
    assert ra["summary"].endswith("A") and rb["summary"].endswith("B")
    # each sub-run saw ONLY its own conversation (system + user [+ ...])
    assert all(m["content"] != "B" for m in llm_a.seen[0])
    assert all(m["content"] != "A" for m in llm_b.seen[0])


# ----------------------------------------------------------------------- dispatch

def test_dispatch_disabled_dan_budget():
    state = SessionState()
    executor = FakeExecutor()
    llm = ScriptedLLM([{"content": LONG}])

    out = asyncio.run(sa.dispatch(
        sa.TOOL_EXPLORER, {"task": "x"}, state=state, llm_call=llm,
        tool_executor=executor, config={"subagents_enabled": False},
    ))
    assert out.startswith(sa.PREFIX_DISABLED) and state.subagent_calls == 0

    state.subagent_calls = sa.DEFAULT_MAX_PER_TURN
    out = asyncio.run(sa.dispatch(
        sa.TOOL_EXPLORER, {"task": "x"}, state=state, llm_call=llm,
        tool_executor=executor, config=None,
    ))
    assert out.startswith(sa.PREFIX_BUDGET)


def test_dispatch_explorer_sukses_dan_counter():
    state = SessionState()
    executor = FakeExecutor()
    llm = ScriptedLLM([{"content": LONG}])
    out = asyncio.run(sa.dispatch(
        sa.TOOL_EXPLORER, {"task": "survey the repo"}, state=state,
        llm_call=llm, tool_executor=executor,
    ))
    assert out.startswith(sa.PREFIX_REPORT) and "• explorer •" in out
    assert state.subagent_calls == 1
    # the report carries the budget-transparency counters
    assert "1 iterations, 0 tool calls, status ok" in out


def test_dispatch_verifier_menyertakan_claims():
    state = SessionState()
    llm = ScriptedLLM([{"content": LONG}])
    asyncio.run(sa.dispatch(
        sa.TOOL_VERIFIER, {"task": "check x", "claims": "fungsi y aman"},
        state=state, llm_call=llm, tool_executor=FakeExecutor(),
    ))
    joined = json.dumps(llm.seen[0])
    assert "Claims to check" in joined and "fungsi y aman" in joined


def test_dispatch_workers_overlap_ditolak_sebelum_kerja():
    state = SessionState()
    executor = FakeExecutor()
    llm = ScriptedLLM([{"content": LONG}])
    out = asyncio.run(sa.dispatch(
        sa.TOOL_WORKERS,
        {"tasks": [{"task": "a", "files": ["x.txt"]}, {"task": "b", "files": ["x.txt"]}]},
        state=state, llm_call=llm, tool_executor=executor,
    ))
    assert out.startswith(sa.PREFIX_SCOPE)
    assert state.subagent_calls == 0 and executor.calls == [] and llm.script  # untouched


def test_dispatch_workers_melebihi_cap():
    state = SessionState()
    tasks = [{"task": str(i), "files": [f"f{i}.txt"]} for i in range(5)]
    out = asyncio.run(sa.dispatch(
        sa.TOOL_WORKERS, {"tasks": tasks}, state=state,
        llm_call=ScriptedLLM([]), tool_executor=FakeExecutor(),
        config={"subagent_workers_max": 2},
    ))
    assert out.startswith(sa.PREFIX_BUDGET) and "limit is 2" in out


def test_dispatch_workers_paralel_laporan_berurutan():
    state = SessionState()
    executor = FakeExecutor()
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("write_file", {"file_path": "a.txt", "content": "A"})]},
        {"content": None, "tool_calls": [tc("write_file", {"file_path": "b.txt", "content": "B"})]},
        {"content": LONG + " A"},
        {"content": LONG + " B"},
    ])
    out = asyncio.run(sa.dispatch(
        sa.TOOL_WORKERS,
        {"tasks": [{"task": "buat a", "files": ["a.txt"]},
                   {"task": "buat b", "files": ["b.txt"]}]},
        state=state, llm_call=llm, tool_executor=executor,
    ))
    assert out.index("--- worker 1 ---") < out.index("--- worker 2 ---")
    assert ("write_file", "a.txt") in executor.calls and ("write_file", "b.txt") in executor.calls
    assert state.subagent_calls == 1  # one delegation, two workers


def test_dispatch_tidak_raise_saatu_llm_mati_di_worker():
    state = SessionState()

    async def boom(**kw):
        raise RuntimeError("mati")

    out = asyncio.run(sa.dispatch(
        sa.TOOL_WORKERS,
        {"tasks": [{"task": "a", "files": ["a.txt"]}]},
        state=state, llm_call=boom, tool_executor=FakeExecutor(),
    ))
    assert sa.PREFIX_ERROR in out and state.subagent_calls == 1


# ------------------------------------------------------------- agent-loop branch

class _ContextManagerPalsu:
    def __init__(self):
        self.messages = []

    def get_history(self):
        return []

    def add_message(self, role, content, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    def get_project_map(self):
        return "Project Structure:\n📁 seed.txt"

    def set_project_map(self, project_map):
        pass


def test_agent_loop_cabang_subagent_frame_dan_budget(tmp_path):
    from app.services import sse
    from app.services.agent_loop import Deps, aliran_agent

    executor = FakeExecutor(str(tmp_path))
    # main call 1 -> delegate_explorer; sub call 1 -> report; main call 2 -> final
    llm = ScriptedLLM([
        {"content": None, "tool_calls": [tc("delegate_explorer", {"task": "survey seed.txt"})]},
        {"content": LONG},
        {"content": "Final answer for the loop test, long enough to close normally."},
    ])
    state = SessionState()

    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=executor,
        llm_call=llm,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 5, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_subagent_manager"),
        context_message={"role": "user", "content": "Current project structure:\n📁 seed.txt"},
        lampiran_desc="",
        mcp=None,
    )

    async def _kumpul():
        agen = aliran_agent(state, deps)
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = asyncio.run(_kumpul())

    mulai = [f for f in frames if "Sub-agent explorer: survey seed.txt ..." in f]
    selesai = [f for f in frames if "Sub-agent explorer finished." in f]
    assert len(mulai) == 1 and len(selesai) == 1
    assert frames.index(mulai[0]) < frames.index(selesai[0])
    assert state.subagent_calls == 1
    # the report reached the history as an ordinary tool message
    assert any(
        m["role"] == "tool" and m["content"].startswith(sa.PREFIX_REPORT)
        for m in deps.context_manager.messages
    )
    assert frames[-1] == 'data: {"type": "done"}\n\n'


# ------------------------------------------------------------- prompts (017)

def test_system_prompt_memuat_kebijakan_delegasi():
    from app.services.prompts import SYSTEM_PROMPT

    assert "DELEGATION & BORROWED-TOOLS POLICY" in SYSTEM_PROMPT
    for nama in ("delegate_explorer", "delegate_verifier", "delegate_workers"):
        assert nama in SYSTEM_PROMPT
    assert "mcp__" in SYSTEM_PROMPT
    # budget & disabled strings are taught, not just produced
    assert "[SUBAGENT BUDGET]" in SYSTEM_PROMPT
    assert "[SUBAGENTS DISABLED]" in SYSTEM_PROMPT


def test_role_prompts_disiplin_kata_klaim():
    for role, prompt in sa.ROLE_PROMPTS.items():
        assert "completion-claim words" in prompt, role
        assert "selesai" in prompt and "berhasil" in prompt, role
