"""test_commit39_inspection_anchor.py — UPDATE-026 commit 39 (0.10.43).

Owner uji-1 (2026-11-02): a PURE INSPECTION order — "Buka
frontend/src/App.jsx dan jelaskan implementasi tabs/subtab di sekitar
baris 35-38, lalu nyatakan fakta verifikasinya." — closed with an honest
descriptive report and STILL received the end-of-turn debt block:

    ⚠️ Utang verifikasi masih terbuka:
    - [nihil_mutasi] "<the inspection order, verbatim>"
    - [implementasi] "<the inspection order, verbatim>"

Two vocabulary false positives, both proven against the code:
  1. KATA_KERJA_MUTASI held the bare substring "implement", so the NOUN
     "implementasi" inside the inspection order anchored a mutating-work
     challenge over a (correctly) zero-mutation ledger.
  2. KLAIM_IMPLEMENTASI holds "diterapkan", so the DESCRIPTIVE passive
     ("kelas tab-active diterapkan pada elemen <a>") read as a work claim
     on a zero-write turn.
Teeth: verb-form mutation catalog + an instruction anchor gate on the
implementation-claim challenge (first-person perfective claims keep it
alive). Disclosure philosophy untouched: real work orders still earn
every challenge.
"""

from app.services import agent_loop, guard_rules
from app.services.session_state import SessionState

INSTRUKSI_INSPEKSI = (
    "Buka frontend/src/App.jsx dan jelaskan implementasi tabs/subtab di "
    "sekitar baris 35-38, lalu nyatakan fakta verifikasinya."
)
CLOSING_DESKRIPTIF = (
    "Implementasi subtab pada frontend/src/App.jsx baris 342-345 memakai "
    "komponen DaisyUI; kelas tab-active font-semibold diterapkan pada "
    "elemen <a> yang sesuai."
)
CLOSING_NIRMUTASI_JUJUR_BUKAN_INSPEKSI = (
    "Semua sudah beres dan pemeriksaan sintaks lolos."
)


def _state(instruksi: str) -> SessionState:
    s = SessionState()
    s.permintaan_turn_ini = instruksi
    return s


# ------------------------------------------------- katalog verba (gigi 1)


def test_perintah_kerja_senyap_untuk_nomina_implementasi():
    assert guard_rules.kalimat_perintah_kerja(INSTRUKSI_INSPEKSI) == ""
    assert guard_rules.kalimat_perintah_kerja(
        "The implementation is broken, please inspect it."
    ) == ""


def test_perintah_kerja_hidup_untuk_verba():
    assert guard_rules.kalimat_perintah_kerja(
        "Implementasikan fitur ekspor PDF sekarang."
    ) != ""
    assert guard_rules.kalimat_perintah_kerja(
        "Please implement the export feature."
    ) != ""
    assert guard_rules.kalimat_perintah_kerja(
        "kerjakan kotak subtabnya sekarang juga."
    ) != ""


# ------------------------------------------------- anchor gate (gigi 2)


def test_tantang_senyap_pada_turn_inspeksi():
    s = _state(INSTRUKSI_INSPEKSI)
    assert not agent_loop._perlu_tantang_klaim_implementasi(
        s, CLOSING_DESKRIPTIF
    )
    assert not agent_loop._perlu_tantang_nihil_mutasi(s, CLOSING_DESKRIPTIF)


def test_tantang_hidup_pada_perintah_mutasi():
    s = _state("kerjakan kotak subtabnya sekarang juga.")
    assert agent_loop._perlu_tantang_klaim_implementasi(
        s, "UI telah diimplementasikan sepenuhnya."
    )
    assert agent_loop._perlu_tantang_nihil_mutasi(
        s, CLOSING_NIRMUTASI_JUJUR_BUKAN_INSPEKSI
    )


def test_lubang_klaim_orang_pertama_tetap_tertutup():
    s = _state(INSTRUKSI_INSPEKSI)
    assert agent_loop._perlu_tantang_klaim_implementasi(
        s, "Saya implementasikan modul pembayaran baru tadi."
    )


def test_debt_tidak_tercatat_pada_turn_inspeksi():
    """Wiring: the intercept arms that mint debts stay silent, so the
    end-of-turn block has nothing to show on an inspection turn."""
    s = _state(INSTRUKSI_INSPEKSI)
    for pemangsa in (
        agent_loop._perlu_tantang_klaim_implementasi,
    ):
        assert not pemangsa(s, CLOSING_DESKRIPTIF)
    assert not agent_loop._perlu_tantang_nihil_mutasi(s, CLOSING_DESKRIPTIF)
    assert s.utang_verifikasi_terbuka() == []
