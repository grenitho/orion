"""UPDATE-025 commit 31 — zero-mutation hard block + honest checkpoint.

Evidence (0.10.34 exam): Orion edit_file-ed `app/main.py` INSIDE a turn
whose own order said "DILARANG membuat atau mengubah file apa pun turn
ini", then printed "## ✅ TUGAS SELESAI - LAPORAN CHECKPOINT" while
carrying [stagnasi] + [perintah_tool] debts, and claimed the navigation
tool was unused while a Playwright-controlled browser sat on
localhost:3000 (owner screenshot).

Teeth:
1. guard_rules.zero_mutasi_diperintah(teks) -> (bool, quote): detects
   the forbid-all-mutation order and returns its sentence.
2. agent_loop gates write_file/edit_file/delete_file/move_file on it
   (_TOOL_MUTASI) — refused before touching disk (inline; predicate
   tested here).
3. guard_rules.teks_status_checkpoint(ada_utang): the checkpoint header
   may only claim completion with zero open debts.
4. session_state carries zero_mutasi_turn/kutipan_zero_mutasi with
   per-turn reset.
"""

from app.services import guard_rules
from app.services.session_state import SessionState

REVISI = (
    "Task: verifikasi murni dua revisi UI terkunci pada frontend\n"
    "InvoiceFlow. DILARANG membuat atau mengubah file apa pun turn ini —\n"
    "tidak ada suntingan, tidak ada file baru; tugasnya MEMERIKSA.\n"
)


# ----------------------------------------------------------------- 1

def test_detector_menangkap_larangan_mutasi():
    ya, kutip = guard_rules.zero_mutasi_diperintah(REVISI)
    assert ya is True
    assert "DILARANG membuat atau mengubah file" in kutip
    assert len(kutip) <= 200


def test_detector_diam_untuk_teks_biasa():
    assert guard_rules.zero_mutasi_diperintah(
        "Periksa backend lalu laporkan hasilnya."
    ) == (False, "")


def test_detector_dilarang_tanpa_file_bukan_zero_mutasi():
    assert guard_rules.zero_mutasi_diperintah(
        "Dilarang berbicara kasar dalam laporan."
    ) == (False, "")


def test_detector_butuh_kata_mengubah_atau_membuat():
    assert guard_rules.zero_mutasi_diperintah(
        "DILARANG menghapus cache file build."
    ) == (False, "")


# ----------------------------------------------------------------- 3

def test_checkpoint_jujur_tanpa_utang():
    en, idn = guard_rules.teks_status_checkpoint(False)
    assert "TASK COMPLETED" in en and "TUGAS SELESAI" in idn
    assert "BELUM" not in idn


def test_checkpoint_jujur_dengan_utang():
    en, idn = guard_rules.teks_status_checkpoint(True)
    assert "NOT COMPLETED" in en and "BELUM selesai" in idn
    assert "utang verifikasi" in idn


# ----------------------------------------------------------------- 4

def test_state_zero_mutasi_default_dan_reset():
    s = SessionState()
    assert s.zero_mutasi_turn is False
    assert s.kutipan_zero_mutasi == ""
    s.zero_mutasi_turn = True
    s.kutipan_zero_mutasi = "x"
    s.reset_per_turn()
    assert s.zero_mutasi_turn is False
    assert s.kutipan_zero_mutasi == ""


def test_tool_mutasi_konstanta():
    from app.services import agent_loop

    for t in ("write_file", "edit_file", "delete_file", "move_file"):
        assert t in agent_loop._TOOL_MUTASI
    assert "read_file" not in agent_loop._TOOL_MUTASI
