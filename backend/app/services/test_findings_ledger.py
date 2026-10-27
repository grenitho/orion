"""
test_findings_ledger.py — durable conversation memory (UPDATE-024 Commit 6)
=============================================================================
Owner transcript 2026-10-27: Orion denied having any record of explorer
findings it produced one message earlier (long tool results are
placeholder-pruned; old turns summarize-compressed), then searched the
blueprint on disk for them. These tests pin the three layers of the fix:
the ledger itself, its per-turn context block, and the pruning exemption
for sub-agent reports.
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.services import findings_ledger as fl
from app.services import subagent_manager as sa
from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState
from app.services.test_subagent_manager import (
    LONG, FakeExecutor, ScriptedLLM,
)


# ---------------------------------------------------------------- ledger core

def test_ledger_kosong_blok_kosong(tmp_path):
    assert fl.blok_konteks(str(tmp_path)) == ""


def test_simpan_dan_blok_memuat_sumber_dan_isi(tmp_path):
    fl.simpan_temuan(str(tmp_path), "explorer", "tiga titik rawan: invoices.py:52, auth.py:43, utils.py:33")
    blok = fl.blok_konteks(str(tmp_path))
    assert "FINDINGS LEDGER" in blok
    assert "[explorer •" in blok
    assert "invoices.py:52" in blok


def test_ledger_berbatas_entri_dan_karakter(tmp_path):
    for i in range(14):
        fl.simpan_temuan(str(tmp_path), "explorer", f"temuan {i} " + "x" * 2000)
    data = json.loads((tmp_path / ".orion" / "findings.json").read_text(encoding="utf-8"))
    assert len(data) == fl.MAX_ENTRIES                 # oldest dropped
    assert all(len(e["isi"]) <= fl.MAX_CHARS for e in data)
    blok = fl.blok_konteks(str(tmp_path))
    assert blok.count("\n- [") == fl.MAX_BLOCK         # block stays small


def test_simpan_tidak_meledak_pa_path_rusak():
    fl.simpan_temuan(str(__file__), "explorer", "x")   # dir bukan proyek: degrade senyap


# ------------------------------------------------------------ dispatch wiring

def test_dispatch_menyimpan_laporan_ke_ledger(tmp_path):
    state = SessionState()
    llm = ScriptedLLM([{"content": LONG}])
    asyncio.run(sa.dispatch(
        sa.TOOL_EXPLORER, {"task": "petakan modul"},
        state=state, llm_call=llm,
        tool_executor=FakeExecutor(project_path=str(tmp_path)),
    ))
    assert (tmp_path / ".orion" / "findings.json").exists()
    assert "explorer" in fl.blok_konteks(str(tmp_path))


# ------------------------------------------------- pruning exemption (E2E loop)

class _CtxPalsu:
    def __init__(self, history):
        self._h = history

    def get_history(self):
        return self._h

    def add_message(self, role, content, **kw):
        self._h.append({"role": role, "content": content, **kw})

    def get_project_map(self):
        return "Project Structure:\n📁 a.py"

    def set_project_map(self, m):
        pass


def _deps_dengan(history, llm, tmp_path):
    from app.services import sse
    return Deps(
        context_manager=_CtxPalsu(history),
        todo_manager=None,
        tool_executor=FakeExecutor(project_path=str(tmp_path)),
        llm_call=llm,
        kirim_status=lambda t: sse.ev('status', text=t),
        config={"max_iterations": 5, "token_budget_threshold": 999999},
        logger=logging.getLogger("t"),
        context_message={"role": "user", "content": "Current project structure:\n📁 a.py"},
        lampiran_desc="",
    )


def _riwayat_dengan_tool_lama(teks_tool):
    return [
        {"role": "user", "content": "pertanyaan lama"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "delegate_explorer", "arguments": "{}"}}]},
        {"role": "tool", "content": teks_tool, "tool_call_id": "c1"},
        {"role": "assistant", "content": "kesimpulan lama yang singkat"},
        {"role": "user", "content": "pertanyaan baru pada giliran ini"},
    ]


def test_laporan_subagent_tidak_diganti_placeholder(tmp_path):
    laporan = "[SUBAGENT REPORT • explorer • 1 iterations, 0 tool calls, status ok]\n" + "detail penting " * 400
    assert len(laporan) > 3000
    llm = ScriptedLLM([{"content": "Jawaban biasa tanpa klaim apa pun untuk giliran ini oke."}])
    deps = _deps_dengan(_riwayat_dengan_tool_lama(laporan), llm, tmp_path)

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [f async for f in agen]
        finally:
            await agen.aclose()

    asyncio.run(_kumpul())
    pesan_tool = [m for m in llm.seen[0] if m.get("role") == "tool"]
    assert pesan_tool and pesan_tool[0]["content"].startswith("[SUBAGENT REPORT")
    assert "detail penting" in pesan_tool[0]["content"]      # UTUH, bukan placeholder


def test_tool_lain_yang_panjang_tetap_dipruning(tmp_path):
    """The exemption is narrow: ordinary long tool outputs still get the
    placeholder (token discipline unchanged)."""
    llm = ScriptedLLM([{"content": "Jawaban biasa tanpa klaim apa pun untuk giliran ini oke."}])
    deps = _deps_dengan(_riwayat_dengan_tool_lama("x" * 4000), llm, tmp_path)

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [f async for f in agen]
        finally:
            await agen.aclose()

    asyncio.run(_kumpul())
    pesan_tool = [m for m in llm.seen[0] if m.get("role") == "tool"]
    assert pesan_tool and "PREVIOUS LONG OUTPUT HAS ALREADY BEEN PROCESSED" in pesan_tool[0]["content"]
