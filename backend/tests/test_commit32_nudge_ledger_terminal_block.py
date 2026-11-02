"""UPDATE-025 commit 32 — mid-turn ledger nudge + terminal build block.

Evidence (0.10.35 exam): the browser reached the app (login detour
included) and clicked subtabs, but no browser_evaluate carrying
getComputedStyle ever landed — the closing carried prose claims, three
open debts and NO read values; the existing gaya nudge fires only at
closings, too late to change the turn. The same turn also substituted
pytest plus a Vite BUILD for the ordered assertions — forbidden by a
pure-verify order, yet the commit-31 hard block covered only writing
tools, not run_terminal.

Teeth:
1. guard_rules.perlu_nudge_ledger(state): butuh_gaya + empty ledger +
   >=2 successful browser calls + not yet nudged -> the next tool
   result carries the user's own order back in (agent_loop, mid-turn).
2. guard_rules.perintah_build_terlarang(teks): server-start /
   production-build command patterns; the zero-mutation hard block now
   refuses run_terminal on them too.
3. session_state.nudge_ledger counter with per-turn reset.
"""

from app.services import guard_rules
from app.services.session_state import SessionState


# ----------------------------------------------------------------- 1

def _state(panggilan, gaya, nudge=0, butuh=True):
    s = SessionState()
    s.perintah_butuh_gaya = butuh
    s.gaya_diperiksa_turn_ini = gaya
    s.panggilan_browser_turn_ini = panggilan
    s.nudge_ledger = nudge
    return s


def test_nudge_setelah_dua_panggilan_tanpa_ledger():
    assert guard_rules.perlu_nudge_ledger(_state(2, False)) is True


def test_nudge_diam_satu_panggilan():
    assert guard_rules.perlu_nudge_ledger(_state(1, False)) is False


def test_nudge_diam_bila_ledger_terisi():
    assert guard_rules.perlu_nudge_ledger(_state(5, True)) is False


def test_nudge_hanya_sekali_per_turn():
    assert guard_rules.perlu_nudge_ledger(_state(5, False, nudge=1)) is False


def test_nudge_diam_tanpa_pesanan_gaya():
    assert guard_rules.perlu_nudge_ledger(_state(5, False, butuh=False)) is False


# ----------------------------------------------------------------- 2

def test_build_dan_server_ditolak():
    for cmd in (
        "npm run build",
        "npx vite build",
        "npm run dev",
        "pnpm start",
        "yarn serve",
        "python -m http.server 3000",
        "uvicorn app.main:app --port 8000",
    ):
        assert guard_rules.perintah_build_terlarang(cmd), cmd


def test_perintah_benar_tetap_lolos():
    for cmd in (
        "git status --short",
        "python -m pytest -q",
        "npm install",
        "ls frontend/src",
    ):
        assert not guard_rules.perintah_build_terlarang(cmd), cmd


# ----------------------------------------------------------------- 3

def test_nudge_ledger_default_dan_reset():
    s = SessionState()
    assert s.nudge_ledger == 0
    s.nudge_ledger = 1
    s.reset_per_turn()
    assert s.nudge_ledger == 0
