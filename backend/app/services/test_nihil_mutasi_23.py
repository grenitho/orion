"""
test_nihil_mutasi_23.py — UPDATE-025 commit 23 (backend 0.10.27)
================================================================
The InvoiceFlow transcript of 2026-11-01 closed a zero-mutation turn with a
closing that dodged EVERY keyword catalog ("Regio ... telah diperiksa
menggunakan read_file_range dan diverifikasi dengan verify_code ... build
sukses"), marked an edit-shaped todo completed after a READ only, and the
todo zero-evidence disclosure rode the CHECKPOINT ending while the field
closed on JAWAB_NORMAL - the owner saw nothing.

Three ledger/instruction-anchored teeth, all pinned here:
  🛑 TANTANG_NIHIL_MUTASI - the user's instruction ordered mutating work,
     the ledger holds zero mutated files, the closing is not a plain
     decline: intercept and quote the instruction verbatim;
  🛑 update_todos refusal - a completed edit-todo over an empty mutation
     ledger is rejected AT THE TOOL, before the list is rewritten;
  ⚠️ todo zero-evidence disclosure on BOTH endings (JAWAB_NORMAL included).
"""

from __future__ import annotations

import json

from app.services.test_computed_style_gate import _MCP, _jalankan
from app.services.test_reference_persistence import TULIS_VUE, _TERef
from app.services.test_soft_limit_reanchor import _Resp, _tool_call

INSTRUKSI_KERJA = "kerjakan kotak subtabnya sekarang juga."
# vocabulary that dodges every older catalog: no KATA_KLAIM, no
# KLAIM_VISUAL, no KLAIM_IMPLEMENTASI, no tool name - the exact shape of
# the 2026-11-01 field closing
MINGIR = (
    "Regio sidebar telah diperiksa menggunakan read_file_range dan "
    "diverifikasi dengan verify_code; build frontend sukses tanpa error."
)
JUJUR_TOLAK = (
    "Tidak ada perubahan yang saya lakukan turn ini; saya laporkan apa "
    "adanya tanpa suntingan."
)


def _injeksi(history, *prefix):
    return [
        str(m.get("content", ""))
        for m in history
        if str(m.get("content", "")).startswith(prefix)
    ]


# ------------------------------------------------- 🛑 zero-mutation turn


def test_nihil_mutasi_ditantang_dan_instruksi_dikutip_verbatim(tmp_path):
    frames, state, deps = _jalankan(
        INSTRUKSI_KERJA,
        [
            _Resp(MINGIR),       # 0 -> dodges every old catalog, intercepted
            _Resp(JUJUR_TOLAK),  # 1 -> plain decline closes
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_nihil_mutasi == 1
    kutip = _injeksi(
        deps.context_manager.history,
        "[SYSTEM - ZERO-MUTATION CHALLENGE]",
        "[SISTEM - TANTANGAN NIHIL-MUTASI]",
    )
    assert len(kutip) == 1
    assert INSTRUKSI_KERJA in kutip[0]      # the user's own sentence
    assert "not a simulation" in kutip[0]
    assert sum("🛑" in f for f in frames) >= 1
    assert any(JUJUR_TOLAK in f for f in frames)


def test_nihil_mutasi_senyap_bila_ada_mutasi(tmp_path):
    frames, state, deps = _jalankan(
        INSTRUKSI_KERJA,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp(MINGIR),       # backed by a real mutation -> silent
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_nihil_mutasi == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - ZERO-MUTATION CHALLENGE]",
        "[SISTEM - TANTANGAN NIHIL-MUTASI]",
    )


def test_nihil_mutasi_senyap_bila_instruksi_tanpa_kata_kerja(tmp_path):
    frames, state, deps = _jalankan(
        "amati warna subtab pada tema gelap lalu laporkan.",
        [
            _Resp(MINGIR),       # observation-only order: no edits owed
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_nihil_mutasi == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - ZERO-MUTATION CHALLENGE]",
        "[SISTEM - TANTANGAN NIHIL-MUTASI]",
    )


def test_nihil_mutasi_senyap_untuk_penolakan_jujur_langsung(tmp_path):
    frames, state, deps = _jalankan(
        INSTRUKSI_KERJA,
        [_Resp(JUJUR_TOLAK)],    # first closing already the OR branch
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_nihil_mutasi == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - ZERO-MUTATION CHALLENGE]",
        "[SISTEM - TANTANGAN NIHIL-MUTASI]",
    )


# --------------------------------------------- 🛑 todo completion refusal


def _todos(*items):
    return json.dumps({"todos": list(items)})


def test_todo_kerja_completed_ditolak_bila_nihil_mutasi(tmp_path):
    todo_kerja = {"id": "1", "status": "completed",
                  "text": "Sunting kontras subtab di App.jsx"}
    frames, state, deps = _jalankan(
        INSTRUKSI_KERJA,
        [
            _Resp("", tool_calls=[_tool_call("update_todos", _todos(todo_kerja))]),
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("", tool_calls=[_tool_call(
                "update_todos",
                _todos({"id": "1", "status": "completed",
                        "text": "Sunting kontras subtab di App.jsx"}))]),
            _Resp("Suntingan selesai disimpan pada App.jsx."),
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    isi_tool = [
        str(m.get("content", ""))
        for m in deps.context_manager.history
        if m.get("role") == "tool"
    ]
    ditolak = [t for t in isi_tool if t.startswith("Error: todo(s) 1")]
    assert len(ditolak) == 1
    assert "ZERO mutated files" in ditolak[0]
    diterima = [t for t in isi_tool if t.startswith("Todo list updated")]
    assert len(diterima) == 1            # accepted only after the mutation


def test_todo_verifikasi_tetap_diterima_tanpa_mutasi(tmp_path):
    """Verify-shaped todos are NOT gated (their evidence model is the
    zero-evidence disclosure, not a refusal)."""
    frames, state, deps = _jalankan(
        INSTRUKSI_KERJA,
        [
            _Resp("", tool_calls=[_tool_call("update_todos", _todos(
                {"id": "9", "status": "completed",
                 "text": "Verifikasi build frontend"}))]),
            _Resp(JUJUR_TOLAK),
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    isi_tool = [
        str(m.get("content", ""))
        for m in deps.context_manager.history
        if m.get("role") == "tool"
    ]
    assert any(t.startswith("Todo list updated") for t in isi_tool)
    assert not any(t.startswith("Error: todo(s)") for t in isi_tool)


# ----------------------------- ⚠️ zero-evidence disclosure on BOTH endings


def test_todo_nol_bukti_disclosureturut_di_jawab_normal(tmp_path):
    frames, state, deps = _jalankan(
        "amati lalu laporkan keadaan subtab tanpa suntingan apa pun.",
        [
            _Resp("", tool_calls=[_tool_call("update_todos", _todos(
                {"id": "5", "status": "completed",
                 "text": "Verifikasi tampilan subtab"}))]),
            _Resp("Laporan pengamatan subtab selesai disusun tanpa browser."),
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" not in f)
    gabungan = laporan + "".join(
        str(m.get("content", "")) for m in deps.context_manager.history
    )
    assert "Items marked complete with NO work evidence" in gabungan
    assert "`[5]`" in gabungan
