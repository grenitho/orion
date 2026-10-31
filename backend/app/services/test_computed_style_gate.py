"""
test_computed_style_gate.py — UPDATE-025 commit 17 (backend 0.10.21)
====================================================================
Owner-approved queue item (Playwright MCP), born from the SAME incident
lineage as commits 11/12/15: the InvoiceFlow UI was rebuilt in invented
indigo #4f46e5 while the green/gold handoff sat unread, "verified" by
`npm run build`; on 2026-10-30 Orion self-certified "fully follows
standard responsive design…" without ever looking at the rendered
result. Commits 11–16 force Orion to READ the reference and reconcile
tokens — but reconciliation from source code is still self-report:
source in, pixels out, and nothing in between but the model's say-so.

The tooth (zero prompt tokens, zero new dependencies): when a browser
MCP server is connected (Playwright or kin, plugged by the owner via
<project>/.orion/mcp.json — the UPDATE-014 manager needs no backend
code for it), a UI-changing turn with saved references must produce
COMPUTED-STYLE evidence — a successful mcp__*__browser_evaluate call
carrying getComputedStyle, recorded in the turn ledger — or:
  1. one mid-turn COMPUTED-STYLE VERIFICATION challenge at the closing
     moment (cap 1/turn, ordered AFTER the reference reconciliation
     gate: read first, compare second), naming the hex tokens
     extracted deterministically from the user's OWN reference files;
  2. the "visual claims NOT verified against rendered styles"
     disclosure, escalated to "reminded mid-turn and still closed"
     when the challenge was ignored — ONE emitter on BOTH endings
     (house rule #4, born compliant this time).
Fate-accurate both ways: a FAILED evaluate ("[MCP …]" string, the
UPDATE-014 failure contract) is not evidence, and an evaluate WITHOUT
getComputedStyle is not evidence either.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.agent_loop import (
    Deps,
    _blok_gaya_tanpa_verifikasi,
    _perlu_nudge_gaya,
    _token_desain_dari_referensi,
    _tool_browser_mcp,
    aliran_agent,
)
from app.services.session_state import SessionState
from app.services.test_reference_persistence import TULIS_VUE, _TERef
from app.services.test_soft_limit_reanchor import (
    _CM,
    _LLM,
    _Resp,
    _TM,
    _tool_call,
)
from app.services.test_todo_evidence_checkpoint import _L

REF_MD = (
    "# Token desain InvoiceFlow\n"
    "- side #10302a\n- acc #d9a21b\n- bg #f4f5f2\n"
    "Dilarang: indigo #4f46e5\n"
)
REF_HTML = "<html><style>:root{--side:#10302a;--acc:#d9a21b}</style></html>"
REF = ".orion/attachments/FRONTEND_HANDOFF.md"
ALAT_PLAYWRIGHT = {
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_evaluate",
}
EVAL_GCS = json.dumps(
    {
        "function": "() => { const cs = getComputedStyle("
        "document.querySelector('.sidebar')); return cs.backgroundColor "
        "+ '|' + cs.fontFamily; }"
    }
)
EVAL_POLOS = json.dumps({"function": "() => document.title"})


class _MCP:
    """Fake MCPManager honoring the UPDATE-014 loop contract: the loop
    only calls openai_tools/tool_names/has_tool/status_label/call, and
    call() never raises (failures come back as '[MCP …]' strings)."""

    def __init__(self, tools=None, hasil=None):
        self._tools = set(tools if tools is not None else ALAT_PLAYWRIGHT)
        self._hasil = hasil or (lambda name, args: "ok")
        self.calls = []

    def openai_tools(self):
        return [
            {
                "type": "function",
                "function": {"name": n, "description": n, "parameters": {}},
            }
            for n in sorted(self._tools)
        ]

    def tool_names(self):
        return set(self._tools)

    def has_tool(self, name):
        return name in self._tools

    def status_label(self, name):
        return str(name).split("__", 1)[-1]

    async def call(self, name, args):
        self.calls.append((name, args))
        return self._hasil(name, args)


def _buat_referensi_bertoken(tmp_path):
    refdir = tmp_path / ".orion" / "attachments"
    refdir.mkdir(parents=True, exist_ok=True)
    (refdir / "FRONTEND_HANDOFF.md").write_text(REF_MD, encoding="utf-8")
    (refdir / "invoiceflow.html").write_text(REF_HTML, encoding="utf-8")
    return refdir


def _jalankan(pesan, responses, executor, mcp=None, state=None):
    state = state if state is not None else SessionState()
    deps = Deps(
        context_manager=_CM([{"role": "user", "content": pesan}]),
        todo_manager=_TM(None),
        tool_executor=executor,
        llm_call=_LLM(responses),
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": 10, "token_budget_threshold": 12000},
        logger=_L(),
        context_message={"role": "user", "content": "Current project structure: x"},
        mcp=mcp,
    )
    frames = []

    async def _run():
        async for f in aliran_agent(state, deps):
            frames.append(f)

    asyncio.run(_run())
    return frames, state, deps


def _injeksi_gaya(deps):
    return [
        str(m.get("content", ""))
        for m in deps.context_manager.history
        if str(m.get("content", "")).startswith(
            (
                "[SYSTEM - COMPUTED-STYLE VERIFICATION]",
                "[SISTEM - VERIFIKASI GAYA TERKOMPUTASI]",
            )
        )
    ]


# ------------------------------------------------------- pure helpers


def test_token_desain_diekstrak_dari_referensi(tmp_path):
    _buat_referensi_bertoken(tmp_path)
    token = _token_desain_dari_referensi(str(tmp_path))
    # file order (sorted names), appearance order, dedup, lowercase
    assert token == ["#10302a", "#d9a21b", "#f4f5f2", "#4f46e5"]
    assert len(_token_desain_dari_referensi(str(tmp_path), maks=2)) == 2
    # missing directory -> empty list, never raises
    assert _token_desain_dari_referensi(str(tmp_path / "tidak-ada")) == []


def test_deteksi_tool_browser_dari_nama_bukan_nama_server():
    deps = SimpleNamespace(mcp=None, tool_executor=None)
    assert _tool_browser_mcp(deps) == set()          # no MCP at all
    deps.mcp = _MCP(tools={"mcp__fetch__get"})
    assert _tool_browser_mcp(deps) == set()          # MCP without browser
    deps.mcp = _MCP(tools={"mcp__pw__browser_evaluate",
                           "mcp__pw__browser_navigate"})
    # detection is by TOOL name — the owner picks the server key freely
    assert _tool_browser_mcp(deps) == {
        "mcp__pw__browser_evaluate", "mcp__pw__browser_navigate"
    }

    class _Rusak:
        def tool_names(self):
            raise RuntimeError("boom")

    deps.mcp = _Rusak()
    assert _tool_browser_mcp(deps) == set()          # broken -> silent


# ------------------------------------------------------- end-to-end


def test_nudge_gaya_setelah_rekonsiliasi_lalu_dipenuhi(tmp_path):
    """Happy path + gate ordering: the reconciliation challenge fires
    FIRST (read the reference), the computed-style challenge SECOND
    (look at the render); a successful getComputedStyle evaluate
    satisfies it and the turn closes clean."""
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    mcp = _MCP()
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> recon gate
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]), # 2
            _Resp("Referensi sudah saya baca ulang; warna sidebar dan "
                  "aksen sesuai handoff."),                                # 3 -> style gate
            _Resp("", tool_calls=[_tool_call(
                "mcp__playwright__browser_navigate",
                json.dumps({"url": "http://localhost:5173/"}))]),          # 4
            _Resp("", tool_calls=[_tool_call(
                "mcp__playwright__browser_evaluate", EVAL_GCS)]),          # 5 -> evidence
            _Resp("Verifikasi computed style selesai: sidebar #10302a, "
                  "aksen #d9a21b — semua token cocok."),                   # 6 -> close
        ],
        executor=ex,
        mcp=mcp,
    )
    assert state.nudge_referensi == 1
    assert state.nudge_gaya == 1
    assert state.gaya_diperiksa_turn_ini is True
    # the injection names the FULL paths, the extracted tokens and the
    # real namespaced tools — no vision, no hardcoded palette
    injeksi = _injeksi_gaya(deps)
    assert len(injeksi) == 1
    teks = injeksi[0]
    assert "`.orion/attachments/FRONTEND_HANDOFF.md`" in teks
    assert "#10302a" in teks and "#d9a21b" in teks and "#4f46e5" in teks
    assert "mcp__playwright__browser_navigate" in teks
    assert "mcp__playwright__browser_evaluate" in teks
    assert "getComputedStyle" in teks
    assert "self-certification" in teks
    # both MCP calls were dispatched through the manager, in order
    assert [n for n, _ in mcp.calls] == [
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_evaluate",
    ]
    assert sum("computed-style verification owed" in f for f in frames) == 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT verified against rendered styles" not in laporan
    assert "NOT consulted" not in laporan


def test_nudge_gaya_dianggarkan_mengeskalasi_blok_checkpoint(tmp_path):
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    mcp = _MCP()
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]), # 1
            _Resp("Semua warna sudah sesuai referensi, tugas selesai."),   # 2 -> style gate
            _Resp("Laporan penutup: seluruh pekerjaan selesai dan sesuai "
                  "handoff."),                                             # 3 -> defiance
        ],
        executor=ex,
        mcp=mcp,
    )
    assert mcp.calls == []                        # browser never opened
    assert state.gaya_diperiksa_turn_ini is False
    assert state.nudge_gaya == 1
    assert sum("computed-style verification owed" in f for f in frames) == 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT verified against rendered styles" in laporan
    assert "UNVERIFIED" in laporan
    assert "still closed without running it" in laporan
    assert laporan.count("NOT verified against rendered styles") == 1


def test_blok_gaya_hadir_di_ujung_jawab_normal(tmp_path):
    """House rule #4 from birth: the SAME single emitter rides the
    normal (non-claim) ending — a long claim-free close cannot slip
    past the disclosure."""
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    mcp = _MCP()
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]), # 1
            _Resp("Saya menghentikan pekerjaan di sini; silakan periksa "
                  "sendiri tampilan halaman faktur tersebut."),           # 2 -> style gate
            _Resp("Pekerjaan saya tinggalkan pada kondisi ini; tidak ada "
                  "klaim apa pun tentang hasil akhirnya."),               # 3 -> JAWAB_NORMAL
        ],
        executor=ex,
        mcp=mcp,
    )
    assert not any("TASK COMPLETED" in f for f in frames), frames
    assert state.nudge_gaya == 1
    jawaban = "".join(f for f in frames if '"type": "token"' in f)
    assert "NOT verified against rendered styles" in jawaban
    assert "still closed without running it" in jawaban
    assert jawaban.count("NOT verified against rendered styles") == 1


def test_gigi_gaya_senyap_tanpa_tool_browser(tmp_path):
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    mcp = _MCP(tools={"mcp__fetch__get"})          # MCP yes, browser no
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]), # 1
            _Resp("Tugas selesai, semua warna sesuai referensi."),         # 2 -> close
        ],
        executor=ex,
        mcp=mcp,
    )
    assert state.nudge_gaya == 0
    assert _injeksi_gaya(deps) == []
    assert mcp.calls == []
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT verified against rendered styles" not in laporan


def test_evaluate_gagal_atau_tanpa_getcomputedstyle_bukan_bukti(tmp_path):
    """Fate-accurate ledger: an evaluate that returns the UPDATE-014
    '[MCP …]' failure string, or one that never asks for
    getComputedStyle, does NOT silence the disclosure."""
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    mcp = _MCP(
        hasil=lambda name, args: (
            "[MCP ERROR] dead subprocess"
            if "getComputedStyle" in str(args)
            else "InvoiceFlow"
        )
    )
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]), # 1
            _Resp("", tool_calls=[_tool_call(
                "mcp__playwright__browser_evaluate", EVAL_POLOS)]),        # 2 no getCS
            _Resp("Tampilan sudah saya cek sekilas, tugas selesai."),      # 3 -> style gate
            _Resp("", tool_calls=[_tool_call(
                "mcp__playwright__browser_evaluate", EVAL_GCS)]),          # 4 -> FAILS
            _Resp("Laporan penutup: semua beres, pekerjaan selesai."),     # 5 -> defiance close
        ],
        executor=ex,
        mcp=mcp,
    )
    assert len(mcp.calls) == 2
    assert state.gaya_diperiksa_turn_ini is False  # neither call is evidence
    assert state.nudge_gaya == 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT verified against rendered styles" in laporan
    assert "still closed without running it" in laporan


def test_perlu_nudge_gaya_matriks_kondisi(tmp_path):
    _buat_referensi_bertoken(tmp_path)
    ex = _TERef(tmp_path)
    deps = SimpleNamespace(mcp=_MCP(), tool_executor=ex)
    st = SessionState()
    # premise false: nothing changed yet
    assert _perlu_nudge_gaya(st, deps) is False
    st.files_changed_this_turn.append("- `src/App.vue` (write_file)")
    assert _perlu_nudge_gaya(st, deps) is True
    # evidence exists -> silent
    st.gaya_diperiksa_turn_ini = True
    assert _perlu_nudge_gaya(st, deps) is False
    st.gaya_diperiksa_turn_ini = False
    # cap 1/turn
    st.nudge_gaya = 1
    assert _perlu_nudge_gaya(st, deps) is False
    st.nudge_gaya = 0
    # no browser tools -> silent; block emitter agrees
    deps.mcp = _MCP(tools={"mcp__fetch__get"})
    assert _perlu_nudge_gaya(st, deps) is False
    assert _blok_gaya_tanpa_verifikasi(st, deps) == ""
    deps.mcp = _MCP()
    assert _blok_gaya_tanpa_verifikasi(st, deps) != ""
