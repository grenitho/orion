"""
test_session_state.py — tests for SessionState (Phase C step 2)
================================================================
Covers what the FASE_C_PLAN.md step-2 acceptance requires — reset_per_turn
and the file-change accounting — plus notice queuing, sisa_todo parity with
the old inline guard, pending_image rebinding (the former ruff F823 holder),
and per-instance isolation of the default factories.
"""

from __future__ import annotations

from app.services.session_state import SessionState


class _TodoPalsu:
    """Stand-in for TodoManager: only get_todos()/has_pending_or_in_progress()
    are relevant to sisa_todo."""

    def __init__(self, todos):
        self._todos = todos

    def get_todos(self):
        return self._todos

    def has_pending_or_in_progress(self):
        return any(
            t.get("status") in ("pending", "in_progress") for t in self._todos
        )


def test_state_baru_bersih_dan_independen():
    a = SessionState()
    b = SessionState()
    assert a.files_changed_this_turn == []
    assert a.pending_system_notices == []
    assert a.analyzed_files == set()
    assert a.nudge_count == 0
    assert a.verifikasi_count == 0
    assert a.current_iter == 0
    assert a.pending_image is None
    a.catat_file_berubah("- `x.py` (write_file)")
    assert b.files_changed_this_turn == []  # default factories are not shared


def test_catat_file_berubah_menjaga_urutan():
    state = SessionState()
    state.catat_file_berubah("- `a.py` (write_file)")
    state.catat_file_berubah("- `b.py` (edit_file)")
    assert state.files_changed_this_turn == [
        "- `a.py` (write_file)",
        "- `b.py` (edit_file)",
    ]
    # the claim gate / checkpoint test the list for truthiness
    assert bool(state.files_changed_this_turn) is True
    assert bool(SessionState().files_changed_this_turn) is False


def test_tambah_notice_masuk_antrean():
    state = SessionState()
    state.tambah_notice("[SYSTEM EMERGENCY STOP]: ...")
    state.tambah_notice("[SYSTEM - AUTOMATIC RESEARCH]: ...")
    assert len(state.pending_system_notices) == 2
    state.pending_system_notices.clear()  # the per-tool-batch reset in the loop
    assert state.pending_system_notices == []


def test_reset_per_turn_membersihkan_cakupan_turn():
    state = SessionState()
    state.catat_file_berubah("- `x.py` (write_file)")
    state.tambah_notice("n")
    state.current_iter = 7
    state.analyzed_files.add("README.md")
    state.nudge_count = 2
    state.verifikasi_count = 1

    state.reset_per_turn()

    assert state.files_changed_this_turn == []
    assert state.pending_system_notices == []
    assert state.current_iter == 0
    # run-scoped fields are deliberately untouched:
    assert state.analyzed_files == {"README.md"}
    assert state.nudge_count == 2
    assert state.verifikasi_count == 1


def test_pending_image_bisa_diikat_ulang():
    """The old code needed a 1-element list holder (ruff F823); a dataclass
    field can be rebound safely, including from inside the generator."""
    state = SessionState(pending_image=[{"type": "text", "text": "hi"}])
    assert state.pending_image
    state.pending_image = None
    assert state.pending_image is None


def test_sisa_todo_memfilter_status_dan_none_safe():
    todos = [
        {"id": "1", "text": "a", "status": "completed"},
        {"id": "2", "text": "b", "status": "pending"},
        {"id": "3", "text": "c", "status": "in_progress"},
    ]
    state = SessionState()
    assert state.sisa_todo(None) == []

    tm = _TodoPalsu(todos)
    sisa = state.sisa_todo(tm)
    assert [t["id"] for t in sisa] == ["2", "3"]

    # parity with the old inline guard it replaces
    lama = (
        [t for t in tm.get_todos() if t.get("status") in ("pending", "in_progress")]
        if (tm and tm.has_pending_or_in_progress())
        else []
    )
    assert sisa == lama

    kosong = _TodoPalsu([{"id": "1", "text": "a", "status": "completed"}])
    assert state.sisa_todo(kosong) == []
