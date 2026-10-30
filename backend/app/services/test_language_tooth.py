"""
test_language_tooth.py — UPDATE-025 commit 13 (backend 0.10.17)
================================================================
Owner report: "Orion cenderung menggunakan bahasa Inggris, walaupun
tahu usernya menggunakan bahasa Indonesia." The SYSTEM_PROMPT LANGUAGE
RULE already mandated replying in the user's language — and still LOST,
because every deterministic block the loop appends (checkpoint
template, disclosures, ask_user questions) spoke English: the model
mirrors the dominant system voice, and one prompt rule cannot out-vote
a wall of English templates.

Teeth here (no prompt tokens spent): deterministic per-turn language
detection (guard_rules.deteksi_bahasa), a one-line reminder in the
TARGET language riding the ephemeral per-iteration context message,
and every user-facing literal rendered through _L(state, en, id).
"""

from __future__ import annotations

from app.services import guard_rules
from app.services.agent_loop import _blok_referensi_belum_dibaca, _L
from app.services.session_state import SessionState
from app.services.test_reference_persistence import (
    TULIS_VUE,
    _Resp,
    _TERef,
    _buat_referensi,
    _jalankan,
    _tool_call,
)

PESAN_ID = (
    "tolong rapikan halaman daftar faktur untuk aplikasi ini dan "
    "jangan lupa warnanya"
)
PESAN_EN = "please tidy the invoice list page for this app and fix the colors"


def test_deteksi_bahasa_deterministik():
    assert guard_rules.deteksi_bahasa(PESAN_ID) == "id"
    assert guard_rules.deteksi_bahasa(PESAN_EN) == "en"
    assert guard_rules.deteksi_bahasa("") == "en"
    assert (
        guard_rules.deteksi_bahasa(
            "data pelanggan hilang lagi setelah restart, tolong cek"
        )
        == "id"
    )


def test_checkpoint_berbahasa_indonesia_bila_user_indonesia(tmp_path):
    frames, state, deps = _jalankan(
        PESAN_ID,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("Halaman faktur sudah saya rapikan."),
        ],
        executor=_TERef(tmp_path),
    )
    assert state.bahasa_turn == "id"
    laporan = "".join(f for f in frames if "LAPORAN CHECKPOINT" in f)
    assert laporan, frames
    assert "TUGAS SELESAI - LAPORAN CHECKPOINT" in laporan
    assert "File yang berubah:" in laporan
    assert "Langkah berikutnya:" in laporan
    assert "Tugas selesai. Ada yang perlu disesuaikan" in "".join(frames)
    # the ephemeral reminder rode the context message, in Indonesian
    assert "[BAHASA]" in deps.context_message["content"]


def test_checkpoint_tetap_inggris_bila_user_inggris(tmp_path):
    frames, state, deps = _jalankan(
        PESAN_EN,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("The invoice list page is finished and all colors have been corrected."),
        ],
        executor=_TERef(tmp_path),
    )
    assert state.bahasa_turn == "en"
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert "TASK COMPLETED - CHECKPOINT REPORT" in laporan
    assert "Next steps:" in laporan
    assert "[BAHASA]" not in deps.context_message["content"]


def test_blok_referensi_mengikuti_bahasa_turn(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    st = SessionState()
    st.files_changed_this_turn.append("- `src/App.vue` (write_file)")
    st.bahasa_turn = "id"
    blok = _blok_referensi_belum_dibaca(st, str(tmp_path))
    assert "TIDAK dibuka turn ini" in blok
    st.bahasa_turn = "en"
    assert "NOT consulted this turn" in _blok_referensi_belum_dibaca(
        st, str(tmp_path)
    )


def test_helper_L_memilih_berdasar_bahasa():
    st = SessionState()
    assert _L(st, "yes", "ya") == "yes"
    st.bahasa_turn = "id"
    assert _L(st, "yes", "ya") == "ya"


# ------------------------- commit 14: challenges speak the turn language


class _TEGagal(_TERef):
    def write_file(self, **kw):
        return "Error: SEARCH block not found in the original file."


class _TEGagalLaluSukses(_TEGagal):
    def __init__(self, path):
        super().__init__(path)
        self.panggilan = 0

    def write_file(self, **kw):
        self.panggilan += 1
        if self.panggilan == 1:
            return "Error: SEARCH block not found in the original file."
        return "Success: wrote the file"


PESAN_KOMENTAR_ID = (
    "tolong tambahkan satu baris komentar di atas fungsi utama di "
    "frontend/src/App.jsx"
)
PESAN_KOMENTAR_EN = (
    "please add one comment line above the main function in "
    "frontend/src/App.jsx"
)


def test_tantang_edit_gagal_indonesia_mengutip_permintaan(tmp_path):
    frames, state, deps = _jalankan(
        PESAN_KOMENTAR_ID,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),  # fail
            _Resp("Semua pekerjaan selesai dan terverifikasi."),           # claim
            _Resp("Editnya gagal; file tidak bisa diubah karena format berubah."),
        ],
        executor=_TEGagal(tmp_path),
    )
    assert state.bahasa_turn == "id"
    sejarah = deps.context_manager.get_history()
    inj = [
        m for m in sejarah
        if "[SISTEM - KLAIM ANDA DITOLAK]" in str(m.get("content"))
    ]
    assert inj, frames
    # the turn's REAL request is quoted inside the challenge, and the
    # "this is not a simulation" sentence kills the rationalization
    # that dropped the owner's one-line-comment request.
    assert "tolong tambahkan satu baris komentar" in inj[-1]["content"]
    assert "bukan simulasi" in inj[-1]["content"]
    gabungan = "".join(frames)
    assert "Edit gagal TIDAK PERNAH pulih" in gabungan


def test_tantang_edit_gagal_inggris_tetap_inggris(tmp_path):
    frames, state, deps = _jalankan(
        PESAN_KOMENTAR_EN,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("All of the requested work is completed and fully verified by the test suite."),
            _Resp("The edit failed because the file format changed since my last read, so I cannot apply it blindly."),
        ],
        executor=_TEGagal(tmp_path),
    )
    assert state.bahasa_turn == "en"
    sejarah = deps.context_manager.get_history()
    inj = [
        m for m in sejarah
        if "[SYSTEM - YOUR CLAIM IS REJECTED]" in str(m.get("content"))
    ]
    assert inj, frames
    assert "please add one comment line" in inj[-1]["content"]
    assert "not a simulation" in inj[-1]["content"]
    assert "Failed edits were NEVER recovered" in "".join(frames)


def test_disclosure_edit_gagal_senyap_bila_pulih(tmp_path):
    frames, state, _ = _jalankan(
        PESAN_KOMENTAR_ID,
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),  # fail
            _Resp("Semua pekerjaan selesai dan terverifikasi."),           # claim
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),  # retry ok
            _Resp("Komentar sudah terpasang dan build lolos."),
        ],
        executor=_TEGagalLaluSukses(tmp_path),
    )
    gabungan = "".join(frames)
    assert "LAPORAN CHECKPOINT" in gabungan
    assert "Edit gagal TIDAK PERNAH pulih" not in gabungan
    assert "Failed edits were NEVER recovered" not in gabungan
