"""
test_pipeline_break.py — UPDATE-025 commit 8 (backend 0.10.12)
==============================================================
Owner incident 2026-10-29 15:42 (perf log): explorer ran four successful
glm calls, died on the fifth, retry-1 died too (minute-scale outage),
salvage handed the partial findings to main — and main then absorbed the
WHOLE pipeline alone: ten main calls, zero worker/verifier dispatches,
and a final report that said NOTHING about any of it ("payload sudah
selaras … pytest 7 passed").

Why every older tooth stayed silent:
  * checkpoint disclosure needs files_changed_this_turn — the turn was
    read-only;
  * JAWAB_NORMAL tooth B needs kata_klaim — a findings report carries no
    completion-claim keywords;
  * the success-only delegation ledger correctly hides crashed sub-runs
    (commit 1 semantics), so "requested minus dispatched" saw explorer
    as never-requested-and-never-ran… but nothing named the DEATH.

Teeth here: SessionState.nasib_role (per-role fate, failures included)
plus a turn-end block that fires when a REQUESTED role DIED — read-only
turns and claim-free replies included. A legitimate conditional skip
(explorer ok, "worker only if findings" sits out) stays silent.
"""

from __future__ import annotations

import asyncio

from app.services import subagent_manager as sa
from app.services.agent_loop import Deps, _blok_pipeline_pecah, aliran_agent
from app.services.session_state import SessionState
from app.services.test_soft_limit_reanchor import (
    _CM,
    _LLM,
    _Resp,
    _TM,
    _tool_call,
)
from app.services.test_todo_evidence_checkpoint import _L, _TE

PESAN_PIPELINE = (
    "Tugaskan sub agent explorer memeriksa file frontend yang terlewat "
    "untuk input pelanggan; jika ada, delegasikan perbaikannya ke sub "
    "agent worker, lalu delegate_verifier memverifikasi sebelum kamu "
    "melaporkan selesai."
)
LAPORAN_TANPA_KLAIM = (
    "Payload input pelanggan di frontend/src/App.jsx sudah selaras dengan "
    "skema backend; rangkaian pytest menghasilkan 7 passed."
)


def _jalankan(pesan_user, responses, state=None, monkey=None):
    state = state if state is not None else SessionState()
    deps = Deps(
        context_manager=_CM([{"role": "user", "content": pesan_user}]),
        todo_manager=_TM([]),
        tool_executor=_TE(),
        llm_call=_LLM(responses),
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": 8, "token_budget_threshold": 12000},
        logger=_L(),
        context_message={"role": "user", "content": "Current project structure: x"},
    )
    frames = []

    async def _run():
        async for f in aliran_agent(state, deps):
            frames.append(f)

    asyncio.run(_run())
    return frames, deps, state


def _outcome(status, retries=0):
    return {
        "summary": f"{sa.PREFIX_ERROR} explorer sub-run failed" if status == "error"
        else "pemetaan selesai. " * 8,
        "iterations": 2,
        "tool_calls": 1,
        "status": status,
        "salvage": "read_file(file_path=app/schemas.py) -> ..." if status == "error" else "",
        "retries": retries,
    }


def test_roles_diminta_mengenal_gaya_pesanan_owner():
    # the exact 2026-10-29 wording, verbatim shape: command verbs as
    # delegation context — and jargon must still NOT match
    pesan = (
        "tugaskan sekali lagi explorer untuk memeriksa apakah masih ada "
        "file yang terlewatkan terkait input data pelanggan di frontend? "
        "Jika masih ada, maka serahkan ke worker untuk menyelesaikan. "
        "Jika worker selesai mengerjakannya, berikan ke verifier untuk "
        "memverifikasi perubahan tersebut."
    )
    assert sa.roles_diminta(pesan) == {"explorer", "worker", "verifier"}
    assert sa.roles_diminta("gunakan worker thread untuk paralelisme") == set()
    assert sa.roles_diminta("pakai explorer pool yang lama") == set()


def test_catat_nasib_status_terbaik_menang():
    st = SessionState()
    sa._catat_nasib(st, sa.EXPLORER, "error", 1)
    assert st.nasib_role[sa.EXPLORER] == ("error", 1)
    sa._catat_nasib(st, sa.EXPLORER, "ok", 0)
    assert st.nasib_role[sa.EXPLORER] == ("ok", 1)   # best status, max retries
    sa._catat_nasib(None, sa.VERIFIER, "ok", 0)      # stateless hook tolerates None
    assert sa.VERIFIER not in st.nasib_role


def test_pipeline_pecah_terungkap_tanpa_klaim_dan_tanpa_file(monkeypatch):
    async def _mati(*a, **k):
        return _outcome("error", retries=2)

    monkeypatch.setattr(sa, "run_subagent", _mati)
    frames, deps, state = _jalankan(PESAN_PIPELINE, [
        _Resp("", tool_calls=[_tool_call(
            "delegate_explorer", '{"task": "periksa frontend"}')]),
        _Resp(LAPORAN_TANPA_KLAIM),
    ])
    assert state.nasib_role.get(sa.EXPLORER, ("",))[0] == "error"
    badan = "".join(f for f in frames if "token" in f)
    assert "Sub-agent pipeline broke this turn" in badan, frames
    assert "`explorer` ended early (status error, retries 2)" in badan
    assert "`worker` was never dispatched" in badan
    assert "`verifier` was never dispatched" in badan
    # the disclosure is durable: saved assistant message carries it too
    disimpan = [m for m in deps.context_manager.history
                if m.get("role") == "assistant"]
    assert any("Sub-agent pipeline broke this turn" in str(m.get("content"))
               for m in disimpan)


def test_skip_kondisional_sah_tetap_senyap(monkeypatch):
    # explorer finished healthy; the user's own conditionals ("worker
    # ONLY if findings") lawfully let worker/verifier sit out — silence.
    async def _sehat(*a, **k):
        return _outcome("ok")

    monkeypatch.setattr(sa, "run_subagent", _sehat)
    frames, deps, state = _jalankan(PESAN_PIPELINE, [
        _Resp("", tool_calls=[_tool_call(
            "delegate_explorer", '{"task": "periksa frontend"}')]),
        _Resp(LAPORAN_TANPA_KLAIM),
    ])
    assert state.nasib_role.get(sa.EXPLORER) == ("ok", 0)
    badan = "".join(f for f in frames if "token" in f)
    assert "Sub-agent pipeline broke this turn" not in badan
    assert _blok_pipeline_pecah(state) == ""


def test_ledger_nasib_di_reset_di_awal_turn(monkeypatch):
    basi = SessionState()
    basi.nasib_role[sa.EXPLORER] = ("error", 1)   # stale death from "last turn"

    async def _sehat(*a, **k):
        return _outcome("ok")

    monkeypatch.setattr(sa, "run_subagent", _sehat)
    frames, deps, state = _jalankan(PESAN_PIPELINE, [
        _Resp("", tool_calls=[_tool_call(
            "delegate_explorer", '{"task": "periksa frontend"}')]),
        _Resp(LAPORAN_TANPA_KLAIM),
    ], state=basi)
    assert state is basi
    assert basi.nasib_role == {sa.EXPLORER: ("ok", 0)}   # stale entry wiped
    badan = "".join(f for f in frames if "token" in f)
    assert "Sub-agent pipeline broke this turn" not in badan
