"""
test_delegation_disclosure.py — UPDATE-025 commit 1 (backend 0.10.8)
====================================================================
InvoiceFlow round 2 (2026-10-28): the owner wrote "panggil
delegate_verifier untuk memeriksa hasilnya sebelum kamu melaporkan
selesai", the model read files for 24 iterations, implemented the
feature, self-verified with verify_html + npm build, checkpointed, and
reported "diverifikasi dengan sukses" — with ZERO delegate_verifier
dispatches (backend.log: [SUBAGENT verifier] count 0, quota already
raised to 6, so budget was not the blocker).

v1 tooth: the checkpoint discloses requested-minus-dispatched roles.
Detection is deliberately narrow (explicit tool names, or a role word
in a sub-agent/delegation context); a bare "verify"/"verification"
must NOT match — verify_html is a tool, not a verifier.
"""

from __future__ import annotations

import asyncio

from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState
from app.services.subagent_manager import roles_diminta
from app.services.test_soft_limit_reanchor import (
    _CM,
    _LLM,
    _Resp,
    _TM,
    _tool_call,
)
from app.services.test_todo_evidence_checkpoint import _L, _TE


def _jalankan(pesan_user, responses, todos=None, state=None):
    state = state if state is not None else SessionState()
    deps = Deps(
        context_manager=_CM([{"role": "user", "content": pesan_user}]),
        todo_manager=_TM(todos or []),
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


KLAIM = "Implementasi selesai dan telah diverifikasi dengan sukses."
TULIS = '{"file_path": "frontend/src/App.jsx", "content": "form"}'
PESAN_VERIFIER = (
    "Tambahkan confirm password di form registrasi. Setelah implementasi, "
    "panggil delegate_verifier untuk memeriksa hasilnya sebelum kamu "
    "melaporkan selesai."
)
JALAN_TULIS_KLAIM = [
    _Resp("", tool_calls=[_tool_call("write_file", TULIS)]),  # _=0
    _Resp(KLAIM),                                             # _=1
]


def test_roles_diminta_menangkap_nama_tool_dan_konteks_subagent():
    assert roles_diminta(PESAN_VERIFIER) == {"verifier"}
    assert roles_diminta("sub agent worker: perbaiki modal pelanggan") == {"worker"}
    assert roles_diminta("sub agent explorer: cari file terkait") == {"explorer"}
    assert roles_diminta("gunakan delegate_workers untuk dua task") == {"worker"}
    assert roles_diminta("delegasikan pemeriksaan ke verifier") == {"verifier"}
    # narrow on purpose: build/verify tools are NOT a verifier
    assert roles_diminta("jalankan verify_html dan npm run build") == set()
    assert roles_diminta("verifikasi manual saja dulu") == set()
    assert roles_diminta("") == set()


def test_diminta_tanpa_dispatch_diungkapkan_di_checkpoint():
    frames, deps, state = _jalankan(PESAN_VERIFIER, JALAN_TULIS_KLAIM)
    laporan = "".join(f for f in frames if ("TASK COMPLETED" in f or "LAPORAN CHECKPOINT" in f))
    assert laporan, frames
    assert "Delegasi diminta tetapi TIDAK dispatch" in laporan
    assert "`verifier`" in laporan
    assert "BUKAN verifikasi independen" in laporan
    assert state.delegasi_terpakai == set()


def test_dispatch_asli_membungkam_disclosure_dan_tercatat():
    frames, deps, state = _jalankan(
        PESAN_VERIFIER,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS)]),  # _=0
            # main loop dispatches the verifier; the sub-run consumes the
            # next queued response (plain text = sub-run ends at once).
            _Resp(
                "",
                tool_calls=[
                    _tool_call("delegate_verifier", '{"task": "periksa form"}')
                ],
            ),                                                          # _=1
            _Resp("Verifier: form sudah sesuai permintaan."),           # sub-run
            _Resp(KLAIM),                                              # _=2
        ],
    )
    laporan = "".join(f for f in frames if ("TASK COMPLETED" in f or "LAPORAN CHECKPOINT" in f))
    assert laporan, frames
    assert "Delegasi diminta tetapi TIDAK dispatch" not in laporan
    assert state.delegasi_terpakai == {"verifier"}


def test_ledger_delegasi_di_reset_di_awal_turn():
    # a stale entry from "last turn" must not launder this turn's claim:
    # pre-seed the ledger, request the role, dispatch nothing — the
    # disclosure MUST still fire because loop start wiped the ledger.
    basi = SessionState()
    basi.delegasi_terpakai.add("verifier")
    frames, deps, state = _jalankan(PESAN_VERIFIER, JALAN_TULIS_KLAIM, state=basi)
    assert state is basi
    laporan = "".join(f for f in frames if ("TASK COMPLETED" in f or "LAPORAN CHECKPOINT" in f))
    assert laporan, frames
    assert "Delegasi diminta tetapi TIDAK dispatch" in laporan
    assert basi.delegasi_terpakai == set()


def test_permintaan_tanpa_peran_tetap_senyap():
    frames, deps, state = _jalankan(
        "Tambahkan confirm password di form registrasi.", JALAN_TULIS_KLAIM
    )
    laporan = "".join(f for f in frames if ("TASK COMPLETED" in f or "LAPORAN CHECKPOINT" in f))
    assert laporan, frames
    assert "Delegasi diminta tetapi TIDAK dispatch" not in laporan


def test_klaim_jawab_normal_membawa_disclosure():
    # UPDATE-025 commit 5 (owner transcript): after a SOFT-LIMIT pause the
    # resume segment starts with a fresh SessionState — zero files changed —
    # so the completion claim skips CHECKPOINT entirely, gets challenged
    # once, and lands on JAWAB_NORMAL. Pre-0.10.9 that frame escaped EVERY
    # disclosure ("7 pytest passed" while worker/verifier stayed unrun).
    frames, deps, state = _jalankan(PESAN_VERIFIER, [_Resp(KLAIM), _Resp(KLAIM)])
    badan = "".join(f for f in frames if "token" in f)
    assert "Delegasi diminta tetapi TIDAK dispatch" in badan, frames
    assert "`verifier`" in badan
    # the disclosure is part of the SAVED assistant message too, so the
    # next turn's context still shows the pipeline was dropped.
    disimpan = [m for m in deps.context_manager.history
                if m.get("role") == "assistant"]
    assert any("Delegasi diminta tetapi TIDAK dispatch" in str(m.get("content"))
               for m in disimpan)


def test_jawab_normal_tanpa_klaim_tetap_senyap():
    panjang = "Berikut penjelasan rinci tentang arsitektur proyek ini. " * 6
    frames, deps, state = _jalankan(PESAN_VERIFIER, [_Resp(panjang)])
    badan = "".join(f for f in frames if "token" in f)
    assert "Delegasi diminta tetapi TIDAK dispatch" not in badan
