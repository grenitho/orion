"""UPDATE-025 commit 26 — the 0.10.29 re-exam fixes.

The re-exam transcript showed three residual holes: (a) planning monologue
still reached the user frame because the v1 classifier's opener list was
too narrow ("Ah, …", "7 tests passed… Let's …"); (b) a browser navigate
that DIED on the file: protocol counted as honouring the named order and
closed the perintah_tool debt prematurely (compliance theater: "We called
navigate and snapshot! This satisfies the requirement"); (c) the
stagnation nir-mutation branch punished a pure read/verify turn. The
fixes: classifier v3 (any-sentence opener + user-addressed escape),
error-shaped browser results never honour an order, and the nir branch
requires at least one edit attempt.
"""

from app.services import agent_loop, guard_rules, loop_guards, stream_utils
from app.services.session_state import SessionState


def _state(**kw):
    st = SessionState()
    for k, v in kw.items():
        setattr(st, k, v)
    return st


# --- classifier v3 ----------------------------------------------------------

def test_classifier_v3_catches_the_reexam_leaks():
    bocor = [
        "Ah, read_file on frontend/src/App.jsx was truncated. Let's use "
        "search_code to check App.jsx.",
        "7 tests passed successfully!\nLet's also check frontend build "
        "using `npm run build` inside `frontend/`.",
        "We called mcp__playwright__browser_navigate and browser_snapshot! "
        "This satisfies the requirement to execute the tool.",
        "Only `invoiceflow.db` (local sqlite database) has changed due to "
        "running tests.\nLet's check `git diff` or the behavior checklist.",
    ]
    for teks in bocor:
        assert stream_utils.prose_perencanaan(teks) is True, teks


def test_classifier_v3_keeps_user_addressed_text():
    aman = [
        "Revisi selesai. Subtab aktif kini memakai teks gelap di latar "
        "terang, dan baris Total live sudah rata kanan.",
        "Berikut hasil pemeriksaannya: subtab aktif memakai #0f172a.",
        "Apakah Anda ingin saya lanjutkan ke verifikasi Playwright?",
        "Let's review the requirements:\n1. Answer directly in the user's "
        "language.",  # numbered list = user-facing markup
        "",
    ]
    for teks in aman:
        assert stream_utils.prose_perencanaan(teks) is False, teks


# --- browser fate accuracy ---------------------------------------------------

def test_error_shaped_browser_result_does_not_honour_order():
    gagal = [
        "[MCP playwright] server timeout",
        "Error: page crashed while navigating",
        "Navigation failed: net::ERR_CONNECTION_REFUSED at localhost:3001",
        "The file: protocol is blocked by the browser context.",
        "browser_navigate failed: ERR_NAME_NOT_RESOLVED",
    ]
    for r in gagal:
        assert agent_loop._browser_hasil_sukses(r) is False, r


def test_healthy_browser_result_honours_order():
    assert agent_loop._browser_hasil_sukses(
        "- page snapshot:\n  - heading \"Daftar Faktur\""
    ) is True
    assert agent_loop._browser_hasil_sukses(
        "{\"color\": \"rgb(15, 23, 42)\"}"
    ) is True


# --- stagnation nir-branch needs an edit attempt ------------------------------

def _klaim(**kw):
    base = dict(
        permintaan_turn_ini="Task: ubah kontras subtab dan baris Total.",
        files_changed_this_turn=["frontend/src/App.jsx"],
        nir_mutasi_beruntun=7,
        edit_gagal_beruntun=0,
    )
    base.update(kw)
    return _state(**base)


def _keputusan(st):
    return loop_guards.evaluasi_akhir_iterasi(
        st, "Pemeriksaan selesai dan sesuai spek.",
        iterasi=9, max_iter=20, sisa_todo=[], maks_nudge=2, maks_verifikasi=2,
    )


def test_pure_verify_turn_stays_silent():
    st = _klaim()  # zero edit attempts this turn
    assert _keputusan(st) != loop_guards.Keputusan.TANTANG_STAGNASI


def test_nir_branch_fires_once_an_edit_was_attempted():
    st = _klaim(
        edit_gagal=[{"urutan": 1, "path": "a.jsx", "alasan": "x"}],
        edit_sukses=[{"urutan": 2, "path": "a.jsx"}],  # recovered: Gate 2 silent
    )
    assert _keputusan(st) == loop_guards.Keputusan.TANTANG_STAGNASI


def test_failed_edit_branch_needs_no_extra_proof():
    st = _klaim(nir_mutasi_beruntun=0, edit_gagal_beruntun=3)
    assert _keputusan(st) == loop_guards.Keputusan.TANTANG_STAGNASI


def test_predikat_langsung_percobaan_edit():
    assert guard_rules.stagnasi_perlu_ditantang(6, 0, 0, 0, 1) is False
    assert guard_rules.stagnasi_perlu_ditantang(6, 0, 1, 0, 1) is True
    assert guard_rules.stagnasi_perlu_ditantang(0, 3, 0, 0, 1) is True
    assert guard_rules.stagnasi_perlu_ditantang(6, 0, 1, 1, 1) is False  # cap
