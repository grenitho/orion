"""
test_proactive_advisory.py — UPDATE-025 commit 9 (backend 0.10.13)
==================================================================
Owner directive (2026-10-29), verbatim spirit: "Orion harus bisa
proaktif, dan memberikan saran. Dia tidak boleh seperti kuda yang
ditutup matanya, mengikuti kemauan penunggangnya."

The incident: add-customer kept failing across turns; Orion answered
port questions and pipeline reports while the structural candidate —
InvoiceFlow having NO persistence layer at all — was never named,
because nobody asked "is there a database?" and a blindfolded model
only answers what it is asked.

Teeth under test:
  * guard_rules: failure-report detection (sticky thread opener),
    advisory-wording detection (thread closer), data-topic detection;
  * loop_guards gate 5.5: TANTANG_SARAN, one-shot per turn, only while
    the thread is open and the closing answer carries no advisory;
  * agent_loop: the thread survives UNRELATED turns (proactivity has a
    memory), and project_facts' deterministic persistence scan rides a
    fact block onto the final frame when the thread is about data and
    the project shows no persistence marker whatsoever.
"""

from __future__ import annotations

import asyncio

from app.services import guard_rules, project_facts
from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState
from app.services.test_soft_limit_reanchor import _CM, _LLM, _Resp, _TM
from app.services.test_todo_evidence_checkpoint import _L, _TE

PESAN_GAGAL = (
    "percobaan untuk menambahkan data pelanggan masih gagal. ini kegagalan "
    "dalam menyusun code atau karena belum ada penyimpanan?"
)
PESAN_TETANGGA = "InvoiceFlow memakai port berapa sebelumnya? saya coba tidak terhubung."
PESAN_BIASA = "Port berapa yang dipakai backend InvoiceFlow sekarang?"
JAWAB_POLLOS = (
    "Backend InvoiceFlow berjalan di port 8001 dan frontend di port 3000 "
    "dengan proxy /api; dokumentasi API tersedia pada jalur /docs dan "
    "pemeriksaan kesehatan pada jalur /health."
)
JAWAB_SARAN = (
    "Backend berjalan di port 8001. Penyebab paling mungkin dari kegagalan "
    "simpan pelanggan: tidak ada lapisan persistensi di proyek ini. Opsi "
    "Anda: SQLite + SQLAlchemy, atau JSON-file store, atau biarkan "
    "in-memory untuk trial."
)


class _TEProj(_TE):
    def __init__(self, path):
        self.project_path = str(path)


def _jalankan(pesan_user, responses, state=None, executor=None):
    state = state if state is not None else SessionState()
    deps = Deps(
        context_manager=_CM([{"role": "user", "content": pesan_user}]),
        todo_manager=_TM([]),
        tool_executor=executor or _TE(),
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


def test_pola_utas_kata_saran_dan_topik_data():
    assert guard_rules.utas_gagal_dari(PESAN_GAGAL)
    assert guard_rules.utas_gagal_dari("saya coba tidak terhubung ke 5173")
    assert guard_rules.utas_gagal_dari(PESAN_TETANGGA)  # "tidak terhubung"
    assert guard_rules.utas_gagal_dari("port berapa yang dipakai?") == ""
    assert guard_rules.utas_gagal_dari("jangan gagal dong kali ini") == ""
    assert guard_rules.kata_saran(JAWAB_SARAN) is True
    assert guard_rules.kata_saran(JAWAB_POLLOS) is False
    assert guard_rules.utas_tentang_data(PESAN_GAGAL) is True
    assert guard_rules.utas_tentang_data("koneksi websocket masih gagal") is False


def test_gate_tantang_saran_sekali_lalu_tutup():
    frames, deps, state = _jalankan(PESAN_GAGAL, [
        _Resp(JAWAB_POLLOS),     # closing without advisory -> challenged
        _Resp(JAWAB_SARAN),      # advisory wording -> closes the thread
    ])
    assert any("Open failure thread" in f for f in frames), frames
    assert state.saran_count == 1
    assert state.utas_gagal == ""          # closed on demonstrated advisory
    assert any("token" in f and "penyebab" in f.lower() for f in frames)


def test_utas_sticky_menagih_di_turn_tetangga():
    # the thread was opened by an EARLIER turn; this turn asks about
    # ports only — and still owes the owner a cause + options.
    basi = SessionState()
    basi.utas_gagal = "menambahkan data pelanggan masih gagal"
    frames, deps, state = _jalankan(PESAN_TETANGGA, [
        _Resp(JAWAB_POLLOS),
        _Resp(JAWAB_SARAN),
    ], state=basi)
    assert state is basi
    assert any("Open failure thread" in f for f in frames), frames
    assert basi.utas_gagal == ""


def test_blok_persistensi_menumpang_frame_saat_proyek_tanpa_db(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = object()\n")
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    assert project_facts.ada_persistensi(str(tmp_path)) is False

    basi = SessionState()
    basi.utas_gagal = "menambahkan data pelanggan masih gagal"
    frames, deps, state = _jalankan(PESAN_BIASA, [
        _Resp(JAWAB_SARAN),      # advisory present: no challenge, fact rides
    ], state=basi, executor=_TEProj(tmp_path))
    badan = "".join(f for f in frames if "token" in f)
    assert "NO persistence layer" in badan, frames
    assert "SQLite + SQLAlchemy" in badan
    assert state.fakta_persistensi_disampaikan is True
    assert state.utas_gagal == ""          # model advisory closed the thread

    # once per thread: a fresh thread on the same project re-fires, but a
    # second delivery inside the SAME thread never happens
    assert state.utas_gagal == ""
    basi.utas_gagal = "input pelanggan masih gagal lagi"
    # flag stays True for the lifetime of the delivered thread state?
    # new thread resets the flag at loop start:
    frames2, _, state2 = _jalankan("input pelanggan masih gagal lagi", [
        _Resp(JAWAB_SARAN),
    ], state=basi, executor=_TEProj(tmp_path))
    badan2 = "".join(f for f in frames2 if "token" in f)
    assert "NO persistence layer" in badan2   # new thread => flag reset


def test_persistensi_ada_maka_blok_senyap(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("class Customer: ...\n")
    (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\n")
    assert project_facts.ada_persistensi(str(tmp_path)) is True

    basi = SessionState()
    basi.utas_gagal = "menambahkan data pelanggan masih gagal"
    frames, deps, state = _jalankan(PESAN_BIASA, [
        _Resp(JAWAB_SARAN),
    ], state=basi, executor=_TEProj(tmp_path))
    badan = "".join(f for f in frames if "token" in f)
    assert "NO persistence layer" not in badan
    assert state.fakta_persistensi_disampaikan is False
