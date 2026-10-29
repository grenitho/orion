"""
test_sse_keepalive.py — UPDATE-025 commit 7 (backend 0.10.11)
=============================================================
Owner incident (2026-10-29, InvoiceFlow pipeline): twice the chat showed
"**Cannot reach the Orion backend**" while the backend was alive and the
perf log kept filling. Root cause chain, all client-side:

  * the sub-agent dispatch is ONE bare await wrapping several 60-160 s
    provider calls (perf log: a single 159 s glm-5.3-flash row) — minutes
    of ZERO bytes on the SSE stream;
  * Node's fetch (undici) aborts a response body silent for ~300 s
    (default bodyTimeout) with a TypeError("fetch failed");
  * ChatPanel._describeError maps every TypeError/ECONNRESET onto the
    "Cannot reach the Orion backend" guide — a CLIENT-side timeout
    mislabeled as a dead server.

The main LLM await has had a 15 s heartbeat since ORION-29; the dispatch
and the two summarize passes had nothing. Fix: sse.KEEPALIVE (an SSE
COMMENT line — parsers only act on 'data: ' lines) yielded every
ORION_SSE_KEEPALIVE_SEC while those awaits are silent.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import agent_loop, sse
from app.services.agent_loop import Deps, _keepalive, aliran_agent
from app.services.session_state import SessionState
from app.services.test_soft_limit_reanchor import _CM, _LLM, _Resp, _TM, _tool_call
from app.services.test_todo_evidence_checkpoint import _L, _TE


def test_keepalive_comment_tidak_dimakan_parser_webview():
    # the webview reader only acts on lines starting with 'data: ';
    # a comment line keeps bytes flowing without becoming a UI event
    assert sse.KEEPALIVE.startswith(": ")
    assert not sse.KEEPALIVE.startswith("data: ")
    assert sse.KEEPALIVE.endswith("\n\n")


def test_keepalive_mengalir_selama_await_senyap():
    async def _senyap():
        await asyncio.sleep(0.05)
        return "hasil-dispatch"

    async def _run():
        frame = []
        hasil: list = []
        async for ka in _keepalive(_senyap(), hasil, interval=0.01):
            frame.append(ka)
        return frame, hasil

    frame, hasil = asyncio.run(_run())
    assert len(frame) >= 2                    # silence broken every 0.01 s
    assert all(f == sse.KEEPALIVE for f in frame)
    assert hasil == ["hasil-dispatch"]


def test_keepalive_meneruskan_exception_dan_membatalkan_tugas():
    async def _meledak():
        await asyncio.sleep(0.05)
        raise ValueError("cascade gagal total")

    async def _run():
        hasil: list = []
        with pytest.raises(ValueError):
            async for _ka in _keepalive(_meledak(), hasil, interval=0.01):
                pass
        return hasil

    assert asyncio.run(_run()) == []          # no phantom result


def test_dispatch_senyap_menghasilkan_keepalive_di_stream(monkeypatch):
    # integration: a dispatch that stays silent longer than the keepalive
    # interval must produce comment frames INSIDE the chat stream, and the
    # run must still finish with a legitimate 'done'.
    monkeypatch.setattr(agent_loop, "SSE_KEEPALIVE_SEC", 0.01)

    async def _dispatch_lambat(*args, **kwargs):
        await asyncio.sleep(0.05)
        return "[SUBAGENT REPORT • explorer • 1 iterations, 0 tool calls, status ok]\n" + (
            "pemetaan selesai. " * 8
        )

    import app.services.subagent_manager as sa
    monkeypatch.setattr(sa, "dispatch", _dispatch_lambat)

    deps = Deps(
        context_manager=_CM([{
            "role": "user",
            "content": "pakai explorer dulu untuk memetakan modul",
        }]),
        todo_manager=_TM([]),
        tool_executor=_TE(),
        llm_call=_LLM([
            _Resp("", tool_calls=[_tool_call(
                "delegate_explorer", '{"task": "petakan modul"}')]),
            _Resp("Baik, berikut ringkasan pemetaan modul proyek ini. " * 4),
        ]),
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": 8, "token_budget_threshold": 12000},
        logger=_L(),
        context_message={"role": "user", "content": "Current project structure: x"},
    )
    frames = []

    async def _run():
        async for f in aliran_agent(SessionState(), deps):
            frames.append(f)

    asyncio.run(_run())
    assert any(f == sse.KEEPALIVE for f in frames), frames
    assert any('"type": "done"' in f for f in frames)
    # keepalive bytes are their OWN frames: no token/status frame shifted
    assert all(sse.KEEPALIVE not in f or f == sse.KEEPALIVE for f in frames)
