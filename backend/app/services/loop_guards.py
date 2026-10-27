"""
loop_guards.py — the end-of-iteration decision as one pure function (Phase C step 3)
=====================================================================================
Before this module existed, four decision gates lived inline in the agent
loop's non-tool-call branch (main.py): the todo-continuation nudge, the
claim-challenge gate, the checkpoint/pause, and the normal answer — each
wrapped around long side-effect blocks, which made the DECISION logic
impossible to unit-test without running the whole stream.

`evaluasi_akhir_iterasi` collapses those gates into one pure function:
(state + scalars) in, a `Keputusan` enum member out. The loop in main.py
becomes a `match` on the decision; every side effect (storing messages,
SSE yields, counter bumps) stays in the match arms, byte-for-byte the same
code as before.

Decision table (identical to the previous if/elif/else cascade, plus the
UPDATE-022 failed-edit gate inserted before the evidence gate):

  short reply (<50 chars, no claim keyword):
      at the iteration cap        -> PAUSE_ITERASI
      below the cap               -> TERUSKAN
  claim + ledger holds failed edits that never recovered + quota left
      (UPDATE-022 — fires even when OTHER files really changed):
                                  -> TANTANG_EDIT_GAGAL
  claim + iterasi > 0 + files actually changed this turn:
      todos still pending & nudge quota left -> LANJUT_TODO
      otherwise                              -> CHECKPOINT
  claim, zero changed files, not yet challenged this turn
      (SOFT one-shot since UPDATE-023)       -> TANTANG_KLAIM
  everything else                                  -> JAWAB_NORMAL

Deviation from FASE_C_PLAN.md, documented deliberately: the plan lists five
enum members, but the max-iteration pause lives INSIDE the short-reply
branch, so the below-cap "store it and keep looping silently" outcome needs
a sixth member (TERUSKAN). Splitting the short-reply check between the loop
and the evaluator would have been less faithful, not more.

Why the nudge/checkpoint/challenge rules look the way they do (history):
  - CHECKPOINT requires hard evidence (files_changed_this_turn): keywords
    like "selesai"/"berhasil" appear in ordinary audit text even when no
    edit_file/write_file ever ran (the "claims completion while doing
    nothing" incident).
  - LANJUT_TODO exists because the old loop broke on the first "done" even
    with todos pending (the "only does the first task" complaint,
    PATCH ORION-1); the nudge quota degrades gracefully to CHECKPOINT.
  - TANTANG_KLAIM (PATCH ORION-17b) rejects a completion claim with zero
    changed files, bounded by MAX_VERIFIKASI so the gate cannot become a
    new loop. Once the quota is spent, the claim falls through to
    JAWAB_NORMAL — exactly the pre-existing behaviour.
"""

from __future__ import annotations

from enum import Enum, auto

from app.services import guard_rules


class Keputusan(Enum):
    """The end-of-iteration decision (see the module docstring's table)."""

    LANJUT_TODO = auto()    # todos still pending: nudge the model, keep looping
    TANTANG_KLAIM = auto()  # completion claim with zero evidence: challenge, keep looping
    # >>> UPDATE-022: completion claim while the edit ledger holds failures
    # that never recovered — challenged by NAMING the failed files, even
    # when other files really changed (InvoiceFlow M1: partial evidence
    # must not launder a failed edit). Shares the TANTANG_KLAIM quota and
    # the challenge mechanism; only the message/status text differs.
    TANTANG_EDIT_GAGAL = auto()
    CHECKPOINT = auto()     # real work, nothing pending: report + ask_user, stop
    JAWAB_NORMAL = auto()   # ordinary answer: stream it, stop
    PAUSE_ITERASI = auto()  # short reply at the iteration cap: pause message, stop
    TERUSKAN = auto()       # short reply below the cap: store it, keep looping silently


def evaluasi_akhir_iterasi(
    state,
    konten: str,
    *,
    iterasi: int,
    max_iter: int,
    sisa_todo: list,
    maks_nudge: int,
    maks_verifikasi: int,
) -> Keputusan:
    """Decide how the agent loop ends this iteration. PURE: no side effects.

    `state` is a SessionState (reads files_changed_this_turn, nudge_count,
    verifikasi_count, and — since UPDATE-022 — the edit ledger via
    edit_gagal_belum_pulih()). `iterasi` is the loop counter `_` (0-based);
    the pause check mirrors the old `current_iter = _ + 1; current_iter >=
    max_iter`. `sisa_todo` is precomputed by the caller via
    `state.sisa_todo(todo_manager)` so this function stays dependency-free.
    """
    is_task_complete = guard_rules.kata_klaim(konten)

    # Gate 1: a short reply without a tool call is not a real answer.
    if len(konten.strip()) < 50 and not is_task_complete:
        if iterasi + 1 >= max_iter:
            return Keputusan.PAUSE_ITERASI
        return Keputusan.TERUSKAN

    # Gate 2 (UPDATE-022): a completion claim while the ledger still holds
    # failed edits that NEVER succeeded afterwards gets challenged by name —
    # BEFORE the evidence gate below, because genuinely-changed OTHER files
    # must not excuse a failed edit of the file the claim is about.
    if guard_rules.klaim_edit_gagal_perlu_ditantang(
        is_task_complete,
        state.edit_gagal_belum_pulih(),
        state.verifikasi_count,
        maks_verifikasi,
    ):
        return Keputusan.TANTANG_EDIT_GAGAL

    # Gate 3: a completion claim WITH hard evidence may close the task —
    # but only after the remaining todos are dealt with.
    if is_task_complete and iterasi > 0 and state.files_changed_this_turn:
        if sisa_todo and state.nudge_count < maks_nudge:
            return Keputusan.LANJUT_TODO
        return Keputusan.CHECKPOINT

    # Gate 4: a completion claim WITHOUT evidence gets challenged — but since
    # UPDATE-023 only ONCE per turn (soft one-shot), with an answer-first
    # message (see the loop's TANTANG_KLAIM arm). History: ORION-17b fired
    # this up to 2× per turn with a numbered evidence ritual; the ritual
    # hijacked the conversation (owner transcript, InvoiceFlow: two user
    # questions answered with the same "1. file+line 2. bukti 3. sudah
    # dikerjakan sebelumnya" ceremony, never the actual answer). Word
    # discipline in the prompt (layer 1) now prevents most false triggers;
    # this gate is the backstop for genuine claim-without-work, and one
    # answer-first correction is enough — a second identical ritual only
    # taught the model to perform compliance instead of answering.
    # The HARD challenge (Gate 2, failed edits in the ledger) keeps the full
    # maks_verifikasi quota: that one guards real integrity, not wording.
    if guard_rules.klaim_perlu_ditantang(
        is_task_complete,
        bool(state.files_changed_this_turn),
        state.verifikasi_count,
        min(1, maks_verifikasi),
    ):
        return Keputusan.TANTANG_KLAIM

    # Gate 5: an ordinary, sufficiently long answer.
    return Keputusan.JAWAB_NORMAL
