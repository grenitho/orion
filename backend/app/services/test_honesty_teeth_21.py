"""
test_honesty_teeth_21.py — UPDATE-025 commit 21 (backend 0.10.25)
=================================================================
The honesty half of the InvoiceFlow-exam fix. On 2026-10-31 Orion closed
TWO turns with "telah diimplementasikan … lolos pemeriksaan sintaks" over
a ledger holding zero mutated files (the browser review showed nothing
had changed), and ignored an explicit "gunakan mcp playwright" order
without a single browser call. The existing teeth were honest but narrow:
the visual-claim challenge only hunts verification words, and no tooth
guarded a named tool order at all.

Two ledger-driven challenges, cap 1/turn each, quoting the model's (or the
user's) own sentence verbatim:
  ✍️ TANTANG_KLAIM_IMPLEMENTASI — closing claims implementation while
     files_changed_this_turn is empty: edit now or withdraw;
  🔧 PERINTAH_TOOL_EKSPLISIT — the turn instruction named a tool and the
     ledger holds zero successful browser/MCP calls: run it now or state
     plainly why not.
Negation-aware keywords keep honest closings ("belum diimplementasikan",
"tidak diterapkan") silent; a turn that actually mutated files, or that
actually called the browser, never trips either tooth.
"""

from __future__ import annotations

import json

from app.services.test_computed_style_gate import _MCP, _jalankan
from app.services.test_reference_persistence import TULIS_VUE, _TERef
from app.services.test_soft_limit_reanchor import _Resp, _tool_call

KLAIM_IMPL = "Kotak subtab telah saya tambah dan warnanya diperbarui."
# NOTE: "sudah saya ..." is a KATA_KLAIM word and would route into the
# OLDER verification gate first; "telah saya tambah" keeps this sentence
# in the commit-21 tooth's own territory (JAWAB_NORMAL closing).
JUJUR = "Tidak ada file yang diubah turn ini; belum ada pengerjaan."
PERINTAH = "ubah warna subtab dan gunakan mcp playwright untuk mengamati hasilnya."


def _injeksi(history, *prefix):
    return [
        str(m.get("content", ""))
        for m in history
        if str(m.get("content", "")).startswith(prefix)
    ]


# ------------------------------------------------ ✍️ implementation claim


def test_klaim_implementasi_ditantang_dan_dikutip_verbatim(tmp_path):
    frames, state, deps = _jalankan(
        "kerjakan kotak subtabnya",
        [
            _Resp(KLAIM_IMPL),   # 0 -> intercepted, quoted
            _Resp(JUJUR),        # 1 -> cap reached, closes honestly
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_klaim_implementasi == 1
    kutip = _injeksi(
        deps.context_manager.history,
        "[SYSTEM - IMPLEMENTATION CLAIM CHALLENGE]",
        "[SISTEM - TANTANGAN KLAIM IMPLEMENTASI]",
    )
    assert len(kutip) == 1
    assert KLAIM_IMPL in kutip[0]
    assert "not a simulation" in kutip[0]
    assert sum("✍️" in f for f in frames) == 1
    assert any(JUJUR in f for f in frames)          # honest close lands


def test_klaim_implementasi_senyap_bila_ada_mutasi(tmp_path):
    frames, state, deps = _jalankan(
        "kerjakan kotak subtabnya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),  # 0
            _Resp(KLAIM_IMPL),                                            # 1 -> backed
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_klaim_implementasi == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - IMPLEMENTATION CLAIM CHALLENGE]",
        "[SISTEM - TANTANGAN KLAIM IMPLEMENTASI]",
    )


def test_klaim_implementasi_senyap_untuk_cabang_jujur(tmp_path):
    frames, state, deps = _jalankan(
        "kerjakan kotak subtabnya",
        [
            _Resp("Warna subtab belum diimplementasikan dan kotak belum "
                  "diterapkan; saya laporkan apa adanya."),
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_klaim_implementasi == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - IMPLEMENTATION CLAIM CHALLENGE]",
        "[SISTEM - TANTANGAN KLAIM IMPLEMENTASI]",
    )


# ------------------------------------------------ 🔧 explicit tool order


def test_perintah_tool_ditantang_bila_browser_nihil(tmp_path):
    # >>> UPDATE-025 commit 25: the cap became TWO shots while zero
    # browser calls exist (the 2026-11-01 transcript paid the single shot
    # with a disclosure and closed). The scenario now walks BOTH shots
    # and the carrying debt disclosed at the ending. <<<
    frames, state, deps = _jalankan(
        PERINTAH,
        [
            _Resp(KLAIM_IMPL),          # 0 -> ✍️ fires first
            _Resp(KLAIM_IMPL),          # 1 -> ✍️ capped, 🔧 shot 1 fires
            _Resp("Playwright tidak dipakai karena dev server mati; saya "
                  "laporkan tanpa pengamatan browser."),   # 2 -> 🔧 shot 2
            _Resp("Playwright tidak dipakai karena dev server mati; saya "
                  "laporkan tanpa pengamatan browser."),   # 3 -> cap: closes
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.tantang_klaim_implementasi == 1
    assert state.tantang_perintah_tool == 2
    kutip = _injeksi(
        deps.context_manager.history,
        "[SYSTEM - EXPLICIT TOOL ORDER]",
        "[SISTEM - PERINTAH TOOL EKSPLISIT]",
    )
    assert len(kutip) == 2
    assert PERINTAH in kutip[0]          # the user's own sentence, verbatim
    assert "kesempatan KEDUA" in kutip[1]  # shot 2 names the carrying debt
    assert sum("🔧" in f for f in frames) == 2
    # the unpaid order rides out of the turn as a disclosed debt
    assert any("utang verifikasi masih terbuka" in f.lower() for f in frames)
    assert any(d["jenis"] == "perintah_tool" for d in state.debt_verifikasi)


def test_perintah_tool_senyap_bila_browser_dipakai(tmp_path):
    frames, state, deps = _jalankan(
        PERINTAH,
        [
            _Resp("", tool_calls=[_tool_call(
                "mcp__playwright__browser_navigate",
                json.dumps({"url": "http://localhost:3001/"}))]),   # 0
            _Resp("Pengamatan subtab ditutup dengan bukti browser."),  # 1
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.panggilan_browser_turn_ini == 1
    assert state.tantang_perintah_tool == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - EXPLICIT TOOL ORDER]",
        "[SISTEM - PERINTAH TOOL EKSPLISIT]",
    )


def test_perintah_tool_senyap_bila_instruksi_tidak_menyebut_tool(tmp_path):
    frames, state, deps = _jalankan(
        "ubah warna subtabnya saja",
        [
            _Resp("Pekerjaan subtab ditutup untuk putaran ini tanpa browser."),
        ],
        executor=_TERef(tmp_path),
        mcp=_MCP(),
    )
    assert state.perintah_tool_eksplisit == ""
    assert state.tantang_perintah_tool == 0
    assert not _injeksi(
        deps.context_manager.history,
        "[SYSTEM - EXPLICIT TOOL ORDER]",
        "[SISTEM - PERINTAH TOOL EKSPLISIT]",
    )
