"""
test_todo_evidence_checkpoint.py — UPDATE-025 hotfix-2, commit 5 v1
===================================================================
InvoiceFlow incident: the todo "Implementasikan UI interaktif
(Dashboard, Pelanggan, Invoice, Cetak PDF)" was marked completed on a
turn whose only file change was a status markdown, and the checkpoint
happily reported "TASK COMPLETED". Since hotfix-2, flipping a todo to
'completed' snapshots the work evidence present at that moment (files
changed / successful run_terminal / sub-agent runs THIS turn), and the
checkpoint DISCLOSES every item completed with none of them.

Disclosure first, rejection later — but the user can no longer be told
"complete" without seeing which items the ledger cannot vouch for.
"""

from __future__ import annotations

from app.services.session_state import SessionState
from app.services.test_soft_limit_reanchor import (
    _CM,
    _LLM,
    _Resp,
    _TM,
    _tool_call,
)
from app.services.agent_loop import Deps, aliran_agent
import asyncio

KLAIM = (
    "Seluruh tahapan pengembangan telah selesai dan diverifikasi dengan "
    "baik pada turn ini."
)


class _TE:
    project_path = "/tmp/proyek-uji"

    def write_file(self, **kw):
        return "Success: wrote the file"


class _L:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


def _jalankan(responses, todos):
    deps = Deps(
        context_manager=_CM(
            [{"role": "user", "content": "kerjakan implementasi UI nya"}]
        ),
        todo_manager=_TM(todos),
        tool_executor=_TE(),
        llm_call=_LLM(responses),
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
    return frames, deps


TODO_AWAL = [{"id": "1", "text": "Implementasikan UI interaktif", "status": "pending"}]
SELESAI = (
    '{"todos": [{"id": "1", "text": "Implementasikan UI interaktif", '
    '"status": "completed"}]}'
)
TULIS = '{"file_path": "IMPLEMENTATION_STATUS.md", "content": "status"}'


def test_todo_selesai_tanpa_bukti_diungkapkan_di_checkpoint():
    frames, deps = _jalankan(
        [
            _Resp("", tool_calls=[_tool_call("update_todos", SELESAI)]),  # _=0
            _Resp("", tool_calls=[_tool_call("write_file", TULIS)]),      # _=1
            _Resp(KLAIM),                                                 # _=2
        ],
        [dict(t) for t in TODO_AWAL],
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NO work evidence this turn" in laporan
    assert "`[1]`" in laporan
    assert "verify these items manually" in laporan


def test_todo_selesai_dengan_bukti_tidak_diungkapkan():
    frames, deps = _jalankan(
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS)]),      # _=0
            _Resp("", tool_calls=[_tool_call("update_todos", SELESAI)]),  # _=1
            _Resp(KLAIM),                                                 # _=2
        ],
        [dict(t) for t in TODO_AWAL],
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NO work evidence this turn" not in laporan


def test_todo_dibuat_langsung_completed_juga_tercatat():
    frames, deps = _jalankan(
        [
            # a brand-new item arriving already 'completed' has no prior
            # status at all — it must still get an evidence snapshot.
            _Resp(
                "",
                tool_calls=[
                    _tool_call(
                        "update_todos",
                        '{"todos": [{"id": "9", "text": "Item misterius", '
                        '"status": "completed"}]}',
                    )
                ],
            ),
            _Resp("", tool_calls=[_tool_call("write_file", TULIS)]),
            _Resp(KLAIM),
        ],
        [dict(t) for t in TODO_AWAL],
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert "`[9]`" in laporan
