"""
test_loop_guards.py — tests for the end-of-iteration decision (Phase C step 3)
================================================================================
The FASE_C_PLAN.md acceptance requires five synthetic situations asserting
five distinct decisions, including "claim with zero changed files" and
"todos still pending". Those five are below, plus the sixth member
(TERUSKAN) and the two quota-degradation edges, which are exactly the
behaviours the old inline cascade had.
"""

from __future__ import annotations

from app.services.loop_guards import Keputusan, evaluasi_akhir_iterasi
from app.services.session_state import SessionState

KLAIM_PANJANG = "The feature is finished; every file has been written and validated."
JAWAB_PANJANG = "Here is an explanation of how the module works, step by step, in detail."
PENDEK = "ok"

TODO_PENDING = [{"id": "2", "text": "still to do", "status": "pending"}]


def _evaluasi(state, konten, *, iterasi=1, max_iter=20, sisa_todo=None,
              maks_nudge=10, maks_verifikasi=2):
    return evaluasi_akhir_iterasi(
        state, konten,
        iterasi=iterasi, max_iter=max_iter,
        sisa_todo=sisa_todo if sisa_todo is not None else [],
        maks_nudge=maks_nudge, maks_verifikasi=maks_verifikasi,
    )


def test_lanjut_todo_selama_masih_ada_yang_pending():
    """Situation 1 (acceptance: 'todos still pending'): claim + evidence +
    pending todo + nudge quota left -> keep the loop going with a nudge."""
    state = SessionState()
    state.catat_file_berubah("- `a.py` (write_file)")
    assert _evaluasi(state, KLAIM_PANJANG, sisa_todo=TODO_PENDING) is Keputusan.LANJUT_TODO


def test_checkpoint_setelah_bukti_ada_dan_todo_habis():
    """Situation 2: claim + evidence + no pending todos -> close with a
    checkpoint report and ask_user."""
    state = SessionState()
    state.catat_file_berubah("- `a.py` (write_file)")
    assert _evaluasi(state, KLAIM_PANJANG) is Keputusan.CHECKPOINT


def test_tantang_klaim_tanpa_bukti():
    """Situation 3 (acceptance: 'claim with zero changed files'): the claim
    gate rejects it and forces verification."""
    state = SessionState()  # files_changed_this_turn empty
    assert _evaluasi(state, KLAIM_PANJANG) is Keputusan.TANTANG_KLAIM


def test_jawab_normal_untuk_jawaban_panjang_tanpa_klaim():
    """Situation 4: an ordinary long answer streams out and ends the run."""
    assert _evaluasi(SessionState(), JAWAB_PANJANG) is Keputusan.JAWAB_NORMAL


def test_pause_iterasi_saat_reply_pendek_menabrak_batas():
    """Situation 5: a short reply at the iteration cap pauses for the user."""
    state = SessionState()
    assert _evaluasi(state, PENDEK, iterasi=19, max_iter=20) is Keputusan.PAUSE_ITERASI


def test_teruskan_reply_pendek_di_bawah_batas():
    """The sixth member: a short reply below the cap is stored and the loop
    continues silently (previously the bare `continue` in the short-reply
    branch)."""
    state = SessionState()
    assert _evaluasi(state, PENDEK, iterasi=3, max_iter=20) is Keputusan.TERUSKAN


def test_klaim_di_iterasi_pertama_tanpa_file_jadi_tantangan():
    """Gate 2 requires iterasi > 0; a claim on the very first iteration with
    no evidence falls to the challenge gate (mirrors the recorded SSE
    scenario E)."""
    state = SessionState()
    assert _evaluasi(state, KLAIM_PANJANG, iterasi=0) is Keputusan.TANTANG_KLAIM


def test_nudge_habis_degradasi_ke_checkpoint():
    """Nudge quota spent -> graceful degradation to the old behaviour:
    checkpoint (with the pending-items warning), not an endless loop."""
    state = SessionState()
    state.catat_file_berubah("- `a.py` (write_file)")
    state.nudge_count = 2
    assert _evaluasi(state, KLAIM_PANJANG, sisa_todo=TODO_PENDING,
                     maks_nudge=2) is Keputusan.CHECKPOINT


def test_kuota_verifikasi_habis_klaim_jadi_jawaban_normal():
    """Challenge quota spent -> the claim is no longer challenged and leaves
    as a normal answer (exactly the pre-existing fall-through)."""
    state = SessionState()
    state.verifikasi_count = 2
    assert _evaluasi(state, KLAIM_PANJANG,
                     maks_verifikasi=2) is Keputusan.JAWAB_NORMAL


def test_tantang_klaim_soft_hanya_sekali_update_023():
    """UPDATE-023: a zero-evidence claim is challenged SOFT, once per turn.

    The old ORION-17b behaviour fired the numbered evidence ritual up to
    2x per turn; two consecutive user questions both got the same ceremony
    and never an answer (owner transcript, InvoiceFlow). One answer-first
    correction is the whole budget now — the hard, full-quota challenge is
    reserved for the failed-edit ledger (TANTANG_EDIT_GAGAL).
    """
    state = SessionState()
    state.verifikasi_count = 1  # already soft-challenged once this turn
    assert _evaluasi(state, KLAIM_PANJANG) is Keputusan.JAWAB_NORMAL
