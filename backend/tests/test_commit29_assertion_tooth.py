"""UPDATE-025 commit 29 — the assertion tooth + closing monologue strip.

Evidence (0.10.32 pure-verify exam): the debt block carried only
[nihil_mutasi] — a successful navigate to port 3000 'honoured' the named
order while the ordered getComputedStyle assertions never ran, and the
final answer OPENED with four 'Let's …' planning paragraphs before the
real Indonesian report.

Teeth:
1. guard_rules.perintah_verifikasi_gaya(teks) -> (perintah, butuh_gaya);
   butuh_gaya True iff the user text names getComputedStyle; the anchor
   sentence falls back to the getComputedStyle sentence.
2. _perlu_tantang_perintah_tool: with butuh_gaya, a browser call WITHOUT
   the computed-style ledger does NOT honour the order — challenge fires,
   debt stays openable; with the ledger (or without butuh_gaya) it honours.
3. stream_utils.buang_paragraf_perencanaan_awal strips LEADING planning
   paragraphs only; keeps declines/questions/user-addressed paragraphs;
   never returns an empty string; leaves a clean report untouched.
4. session_state carries perintah_butuh_gaya with per-turn reset.
"""

import pytest

from app.services import guard_rules, stream_utils
from app.services.session_state import SessionState


# ----------------------------------------------------------------- 1

TEKS_REVISI3 = (
    "Task: verifikasi murni dua revisi UI terkunci pada frontend InvoiceFlow.\n"
    "DILARANG membuat atau mengubah file apa pun turn ini.\n"
    "PORT OTORITAS: frontend hidup di http://localhost:3000.\n"
    "Server SUDAH hidup (owner yang menjalankannya).\n"
    "VERIFIKASI WAJIB via Playwright MCP (jangan diganti build/verify_code):\n"
    "1. Subtab aktif: getComputedStyle warna teks dan latar.\n"
    "2. Sidebar: borderTopWidth dan borderRadius tidak boleh 0.\n"
    "3. Total: tepat Rp 433.467.\n"
    "Lapor PASS/FAIL dengan nilai terbaca."
)


def test_perintah_verifikasi_gaya_menandai_getcomputedstyle():
    perintah, butuh = guard_rules.perintah_verifikasi_gaya(TEKS_REVISI3)
    assert butuh is True
    assert perintah  # anchor non-empty: challenge always quotes the user


def test_perintah_verifikasi_gaya_tanpa_getcomputedstyle():
    perintah, butuh = guard_rules.perintah_verifikasi_gaya(
        "Periksa backend lalu restart servernya."
    )
    assert butuh is False


def test_perintah_verifikasi_gaya_fallback_anchor_kalimat():
    """No named-tool order sentence, but getComputedStyle present ->
    the getComputedStyle sentence itself becomes the anchor."""
    teks = (
        "Cek dulu kodenya, jangan ubah apa pun.\n"
        "Pakai browser, baca getComputedStyle warna teks subtab aktif dan "
        "laporkan nilai terbacanya.\n"
        "Selesai."
    )
    perintah, butuh = guard_rules.perintah_verifikasi_gaya(teks)
    assert butuh is True
    assert perintah and "getComputedStyle" in perintah
    assert len(perintah) <= 300


def test_perintah_verifikasi_gaya_input_kosong_aman():
    perintah, butuh = guard_rules.perintah_verifikasi_gaya("")
    assert butuh is False
    assert perintah == ""


# ----------------------------------------------------------------- 2

@pytest.fixture()
def state():
    s = SessionState()
    s.perintah_tool_eksplisit = "VERIFIKASI WAJIB via Playwright MCP"
    s.perintah_butuh_gaya = True
    return s


def test_navigate_tanpa_gaya_tidak_menggugurkan(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.panggilan_browser_turn_ini = 1
    state.gaya_diperiksa_turn_ini = False
    state.tantang_perintah_tool = 0  # first closing check after navigate
    assert _perlu_tantang_perintah_tool(state) is True


def test_navigate_dengan_gaya_lunas(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.panggilan_browser_turn_ini = 1
    state.gaya_diperiksa_turn_ini = True
    state.tantang_perintah_tool = 2
    assert _perlu_tantang_perintah_tool(state) is False


def test_tanpa_butuh_gaya_navigate_cukup(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.perintah_butuh_gaya = False
    state.panggilan_browser_turn_ini = 1
    state.gaya_diperiksa_turn_ini = False
    assert _perlu_tantang_perintah_tool(state) is False


def test_cap_satu_setelah_panggilan_browser(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.panggilan_browser_turn_ini = 1  # cap = 1 when browser called
    state.tantang_perintah_tool = 1
    assert _perlu_tantang_perintah_tool(state) is False


def test_cap_dua_tanpa_panggilan_browser(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.panggilan_browser_turn_ini = 0
    state.tantang_perintah_tool = 1
    assert _perlu_tantang_perintah_tool(state) is True
    state.tantang_perintah_tool = 2
    assert _perlu_tantang_perintah_tool(state) is False


def test_tanpa_perintah_eksplisit_tidak_menantang(state):
    from app.services.agent_loop import _perlu_tantang_perintah_tool

    state.perintah_tool_eksplisit = ""
    state.panggilan_browser_turn_ini = 0
    assert _perlu_tantang_perintah_tool(state) is False


# ----------------------------------------------------------------- 3

MONOLOG = (
    "Let's inspect further down in `frontend/src/App.jsx` to see the subtab "
    "containers and live total summary row.\n\n"
    "Let's check `frontend/src/App.jsx` around lines 640-690 for the subtab "
    "styling and around lines 855-870 for the live total summary.\n\n"
    "Pemeriksaan murni terhadap dua revisi UI terkunci selesai.\n\n"
    "Revisi 1 — kontras subtab aktif (baris 650-651): kelas aktif memakai "
    "`btn-primary shadow-sm text-primary-content`; non-aktif `btn-ghost`.\n\n"
    "TIDAK ADA FILE YANG DIUBAH sesuai instruksi verifikasi murni."
)


def test_strip_paragraf_perencanaan_awal():
    hasil = stream_utils.buang_paragraf_perencanaan_awal(MONOLOG)
    assert hasil.startswith("Pemeriksaan murni")
    assert "Let's" not in hasil
    # the report body (including the decline sentence) survives intact
    assert "TIDAK ADA FILE YANG DIUBAH" in hasil


def test_strip_tidak_menyentuh_laporan_bersih():
    teks = "Revisi 1 PASS: warna teks rgb(255,255,255).\n\nRevisi 2 PASS: Total tepat Rp 433.467."
    assert stream_utils.buang_paragraf_perencanaan_awal(teks) == teks


def test_strip_tidak_memakan_penolakan_jujur_di_awal():
    teks = (
        "Saya tidak bisa menjalankan assertion itu karena server frontend "
        "tidak hidup di port mana pun.\n\n"
        "Let's check the report next."
    )
    # leading paragraph is a decline -> nothing stripped
    assert stream_utils.buang_paragraf_perencanaan_awal(teks) == teks


def test_strip_semua_perencanaan_kembali_utuh():
    teks = "Let's check the file first.\n\nLet's inspect the styles next."
    assert stream_utils.buang_paragraf_perencanaan_awal(teks) == teks


def test_strip_input_kosong_dan_tunggal():
    assert stream_utils.buang_paragraf_perencanaan_awal("") == ""
    assert stream_utils.buang_paragraf_perencanaan_awal("OK.") == "OK."


# ----------------------------------------------------------------- 4

def test_state_reset_per_turn():
    s = SessionState()
    s.perintah_butuh_gaya = True
    s.reset_per_turn()
    assert s.perintah_butuh_gaya is False


def test_state_default():
    assert SessionState().perintah_butuh_gaya is False
