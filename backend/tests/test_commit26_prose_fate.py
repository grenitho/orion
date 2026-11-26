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


# --- commit 27: the markup hole + the crash regression ----------------------

def test_classifier_v4_closes_the_markup_hole():
    bocor = [
        "Let's check code implementation of the three revisions in "
        "`frontend/src/App.jsx`:\n1. **Revisi 1a**:\n   - Sidebar item "
        "aktif: check sidebar code around line 330-450.",
        "Let's carefully format our response in Indonesian as instructed "
        "by the language rule.",
        "Let's present a clear, professional engineering report in "
        "Indonesian, detailing the implementation.",
        "Let's check if there are any pending todos to update or if we "
        "can mark them completed.",
        "Let's review the requirements:\n1. Answer directly in the user's "
        "language.",  # meta-monologue about the rules: v4 suppresses
    ]
    for teks in bocor:
        assert stream_utils.prose_perencanaan(teks) is True, teks


def test_traceback_import_module_level_bukan_lokal():
    """The 0.10.30 turn-killer: a function-local `import traceback` made
    the name local to aliran_agent, so the generic tool except died with
    UnboundLocalError. AST tooth: module-level import present, NO local
    import inside aliran_agent."""
    import ast
    import pathlib

    src = pathlib.Path(agent_loop.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mod = {
        a.name
        for n in tree.body
        if isinstance(n, ast.Import)
        for a in n.names
    }
    assert "traceback" in mod
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "aliran_agent"
    )
    lokal = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Import)
        and any(a.name == "traceback" for a in n.names)
    ]
    assert not lokal


# --- commit 28: fully inverted default for tool-call content ---------------

def test_catatan_untuk_user_default_suppress():
    bocor = [
        "Let's check what the user requested:\n1. Revisi 1a — kontras.",
        "Wait, the subtab container already has `border border-base-300 "
        "rounded-lg`, but let's make sure it's fully compliant with 1b.",
        "Let's check how the subtabs are currently rendered:\n```jsx\n"
        "<div className=\"flex gap-2\" />\n```",
        "Now let's write a script that runs the server in a background "
        "thread, sleeps 5 seconds, then exits.",
    ]
    for teks in bocor:
        assert stream_utils.catatan_untuk_user(teks) is False, teks


def test_catatan_untuk_user_escapes_reach_user():
    lolos = [
        "Playwright tidak dipakai karena dev server mati; saya laporkan "
        "tanpa pengamatan browser.",  # honest decline = OR branch
        "Berikut hasil pemeriksaannya: subtab aktif memakai #0f172a.",
        "Apakah Anda ingin saya lanjutkan ke verifikasi Playwright?",
        "Shall I continue with the Playwright assertions?",  # question
    ]
    for teks in lolos:
        assert stream_utils.catatan_untuk_user(teks) is True, teks


def test_reroute_catches_make_sure_monologue():
    assert stream_utils.prose_perencanaan(
        "Wait, the subtab container already has border, but let's make "
        "sure it is fully compliant with 1b."
    ) is True
