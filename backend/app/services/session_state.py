"""
session_state.py — the per-turn agent state (Phase C step 2)
=============================================================
Before this module existed, the agent loop in main.py carried ~9 loose mutable
locals initialized at the top of the ~1,200-line generator (analyzed_files,
files_changed_this_turn, pending_system_notices, the nudge/verification
counters, the single-use attachment image parts, current_iter). That made the
loop's bookkeeping impossible to unit-test and easy to break.

SessionState bundles those names into one dataclass with small explicit
methods. The loop (still in main.py at this step) builds one instance per
/chat request and reads/writes through it — no behaviour change: every field
starts at the same default the old locals had.

Deliberately NOT moved here:
  - the `_` loop counter (guarded by test_loop_counter_guard.py — it must
    stay the for-loop target inside the generator);
  - `last_tool_calls`, `consecutive_400_errors`, `last_400_error_msg`
    (loop-internal recovery bookkeeping, not listed in the Phase C plan
    inventory);
  - `total_input_tokens`/`total_output_tokens` — the plan inventory lists
    them as per-turn loop state, but they are actually locals of the
    /performance route (computed from performance_log.jsonl), not of the
    agent loop. Left untouched.

Identifiers stay Indonesian, consistent with the rest of the codebase and
with the method names prescribed by FASE_C_PLAN.md (catat_file_berubah,
tambah_notice, reset_per_turn, sisa_todo).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    """Mutable state of one /chat turn (one agent-loop run)."""

    # Files/line-ranges already read during this run — the redundancy guard
    # for read_file / read_file_range (the warning says "in this session").
    analyzed_files: set = field(default_factory=set)

    # Evidence of real work: appended ONLY when a write_file/edit_file result
    # genuinely starts with the success marker. Mandatory precondition for the
    # completion checkpoint (PATCH ORION-17b / the claim gate).
    files_changed_this_turn: list = field(default_factory=list)

    # System notices deferred until after the assistant + tool messages are
    # stored, so history order stays valid. Re-initialized per tool-call batch
    # inside the loop (state.pending_system_notices.clear()).
    pending_system_notices: list = field(default_factory=list)

    # Attachment image parts, injected into api_messages ONLY on the first
    # iteration and then set back to None (single use). Replaces the old
    # 1-element-list holder workaround (ruff F823): assigning through
    # `state.` cannot create a generator-local, so rebinding is safe now.
    pending_image: object = None

    # Quotas/counters (PATCH ORION-1b and ORION-17a). Scoped to the whole
    # run, exactly like the old per-request locals.
    nudge_count: int = 0
    verifikasi_count: int = 0

    # >>> UPDATE-016: sub-agent delegations used on THIS turn (budget gate
    # in subagent_manager.dispatch; cap = subagent_max_per_turn config).
    subagent_calls: int = 0

    # >>> UPDATE-022 (InvoiceFlow M1 incident, HANDOFF_UPDATE_022 §2C):
    # the failure-aware EDIT LEDGER. files_changed_this_turn only ever saw
    # SUCCESSES, so a turn that mixed one real edit with a FAILED edit of
    # another file passed the claim gate — Orion reported "fixed" for a
    # file whose edit_file call had returned an error (or was rejected by
    # a guard), and the checkpoint happily listed the unrelated success.
    # The ledger records BOTH outcomes per attempt, in order, so the gate
    # can demand evidence per claimed file instead of per turn.
    #   edit_gagal  : [{"urutan", "path", "alasan"}] every failed attempt
    #   edit_sukses : [{"urutan", "path"}]           every successful one
    # `urutan` is a turn-scoped sequence number (never wall-clock time:
    # recordings/tests must stay deterministic).
    edit_gagal: list = field(default_factory=list)
    edit_sukses: list = field(default_factory=list)
    _edit_urutan: int = 0

    # Iteration progress marker used by the short-reply pause path.
    current_iter: int = 0

    def catat_file_berubah(self, entri: str) -> None:
        """Record one piece of real-work evidence (a successfully changed file)."""
        self.files_changed_this_turn.append(entri)

    def catat_edit_gagal(self, path: str, alasan: str) -> None:
        """Record ONE failed write_file/edit_file attempt (UPDATE-022 ledger)."""
        self._edit_urutan += 1
        self.edit_gagal.append(
            {"urutan": self._edit_urutan, "path": path, "alasan": alasan}
        )

    def catat_edit_sukses(self, path: str) -> None:
        """Record ONE successful write_file/edit_file (UPDATE-022 ledger)."""
        self._edit_urutan += 1
        self.edit_sukses.append({"urutan": self._edit_urutan, "path": path})

    def edit_gagal_belum_pulih(self) -> list:
        """Failed edits that NEVER succeeded afterwards, oldest first.

        One entry per path (the LATEST unrecovered failure — its `alasan` is
        the most relevant). A success for the same path with a higher
        `urutan` than the failure means the file recovered: the model retried
        and the retry worked, so the claim gate has nothing to challenge.
        """
        sukses_terakhir: dict = {}
        for entri in self.edit_sukses:
            path = entri["path"]
            if entri["urutan"] > sukses_terakhir.get(path, -1):
                sukses_terakhir[path] = entri["urutan"]
        terbaru: dict = {}
        for entri in sorted(self.edit_gagal, key=lambda e: e["urutan"]):
            if sukses_terakhir.get(entri["path"], -1) > entri["urutan"]:
                continue  # recovered by a later success
            terbaru[entri["path"]] = entri
        return sorted(terbaru.values(), key=lambda e: e["urutan"])

    def ringkasan_ledger(self) -> str:
        """Checkpoint-report summary of the ledger (UPDATE-022).

        Returns '' when NO failure was ever recorded this turn, so reports
        of all-failure-free turns stay byte-identical to the pre-022 shape
        (the SSE harness contract). When failures exist, lists every failed
        attempt with its reason and whether a later attempt recovered it.
        """
        if not self.edit_gagal:
            return ""
        sukses_terakhir: dict = {}
        for entri in self.edit_sukses:
            path = entri["path"]
            if entri["urutan"] > sukses_terakhir.get(path, -1):
                sukses_terakhir[path] = entri["urutan"]
        baris = []
        for entri in sorted(self.edit_gagal, key=lambda e: e["urutan"]):
            pulih = sukses_terakhir.get(entri["path"], -1) > entri["urutan"]
            tanda = "RECOVERED (a later attempt succeeded)" if pulih else "NEVER SUCCEEDED"
            baris.append(f"- `{entri['path']}` — {entri['alasan']} [{tanda}]")
        return (
            "\n\n**⚠️ EDIT LEDGER — failed edit attempts this turn:**\n"
            + "\n".join(baris)
        )

    def tambah_notice(self, notice: str) -> None:
        """Queue a system notice to be flushed after the tool results are stored."""
        self.pending_system_notices.append(notice)

    def reset_per_turn(self) -> None:
        """Clear the turn-scoped records when the state is reused for a new turn.

        analyzed_files and the two quota counters are NOT cleared: the
        read-redundancy guard and the nudge/claim quotas span the whole run,
        matching the pre-SessionState behaviour where they were initialized
        once per request. The UPDATE-022 edit ledger IS turn-scoped: like
        files_changed_this_turn, it is evidence about THIS turn's claims.
        """
        self.files_changed_this_turn = []
        self.pending_system_notices = []
        self.edit_gagal = []
        self.edit_sukses = []
        self._edit_urutan = 0
        self.current_iter = 0

    def sisa_todo(self, todo_manager) -> list:
        """Todo items still pending/in_progress ([] when todo_manager is absent).

        Equivalent to the old inline guard
        `todo_manager and todo_manager.has_pending_or_in_progress()` followed
        by the same comprehension — the comprehension itself is the source of
        truth, has_pending_or_in_progress() is exactly `bool()` of it.
        """
        if todo_manager is None:
            return []
        return [
            t for t in todo_manager.get_todos()
            if t.get("status") in ("pending", "in_progress")
        ]
