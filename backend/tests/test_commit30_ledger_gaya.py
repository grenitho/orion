"""UPDATE-025 commit 30 — the ledger that survives compression.

Evidence (0.10.33 exam transcript): browser work DID happen (navigate,
evaluate, clicks, console_messages) and the perintah challenge DID fire
("Perintah tool eksplisit belum dijalankan…"), but then (a) a
post-challenge browser_navigate paid the named-order debt via the
commit-26 rule (any successful browser call pays) while the
computed-style ledger stayed empty — the debt vanished from the block,
and (b) five "Memory full, compressing context" events ate the evaluate
results before the closing, so the final report carried NO read values.

Teeth:
1. guard_rules.perintah_tool_boleh_tutup: with butuh_gaya, only the
   gaya ledger pays the named-order debt.
2. state.gaya_bukti_turn captures read-value snippets by CODE at
   evaluate time; per-turn reset.
3. stream_utils.tambah_ledger_gaya appends the captured values to a
   closing text (no duplicates, max six, empty ledger = no-op).
"""

from app.services import guard_rules, stream_utils
from app.services.session_state import SessionState


# ----------------------------------------------------------------- 1

def _state(butuh, gaya):
    s = SessionState()
    s.perintah_butuh_gaya = butuh
    s.gaya_diperiksa_turn_ini = gaya
    return s


def test_tanpa_butuh_gaya_browser_call_boleh_membayar():
    assert guard_rules.perintah_tool_boleh_tutup(_state(False, False)) is True


def test_butuh_gaya_tanpa_ledger_tidak_boleh_membayar():
    assert guard_rules.perintah_tool_boleh_tutup(_state(True, False)) is False


def test_butuh_gaya_dengan_ledger_boleh_membayar():
    assert guard_rules.perintah_tool_boleh_tutup(_state(True, True)) is True


# ----------------------------------------------------------------- 2

def test_state_ledger_default_dan_reset():
    s = SessionState()
    assert s.gaya_bukti_turn == []
    s.gaya_bukti_turn.append("rgb(255, 255, 255)")
    s.reset_per_turn()
    assert s.gaya_bukti_turn == []


def test_state_ledger_instansi_terpisah():
    a, b = SessionState(), SessionState()
    a.gaya_bukti_turn.append("x")
    assert b.gaya_bukti_turn == []  # default_factory, bukan mutable default


# ----------------------------------------------------------------- 3

def test_ledger_kosong_noop():
    assert stream_utils.tambah_ledger_gaya("Laporan.", []) == "Laporan."
    assert stream_utils.tambah_ledger_gaya("Laporan.", None) == "Laporan."


def test_ledger_menempel_di_penutup():
    out = stream_utils.tambah_ledger_gaya(
        "Revisi 1a PASS.", ["rgb(255, 255, 255)", "1px"]
    )
    assert out.startswith("Revisi 1a PASS.")
    assert "NILAI TERBACA" in out
    assert "- rgb(255, 255, 255)" in out and "- 1px" in out


def test_ledger_tidak_menduplikat_nilai_yang_sudah_disebut():
    out = stream_utils.tambah_ledger_gaya(
        "Warna terbaca rgb(255, 255, 255) sesuai kontras.",
        ["rgb(255, 255, 255)", "0.5rem"],
    )
    assert out.count("rgb(255, 255, 255)") == 1
    assert "- 0.5rem" in out


def test_ledger_maks_enam_entri():
    out = stream_utils.tambah_ledger_gaya("L.", [f"v{i}" for i in range(9)])
    assert out.count("\n- v") == 6


def test_ledger_entri_kosong_diabaikan():
    assert stream_utils.tambah_ledger_gaya("L.", ["", None]) == "L."
