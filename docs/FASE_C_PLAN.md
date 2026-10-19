# FASE C — Execution Plan: extracting the agent loop out of main.py

**Status:** NOT STARTED. This document is the single source of truth for doing it.
Read all of it before touching code. Written to be executable by an agent or
engineer with **no prior context** beyond this repository.

Companion documents: `docs/README.md` (what is canonical), `STATUS_ORION.md`
(agent-context, Indonesian), `DEVELOPMENT_ROADMAP.md` (what comes after).

---

## 0. Current state snapshot (verify these before starting)

| Fact | Value | How to check |
|---|---|---|
| `main.py` size | ~2,115 lines | `wc -l backend/app/main.py` (or PowerShell `(Get-Content …).Count`) |
| Agent loop | `async def _aliran_inti()` inside `/chat` | search `_aliran_inti` |
| Tools | 22 declared & dispatched | startup log line `[TOOLS] Konsisten: 22 tool` |
| Tests | 77 passing | `python -m pytest backend/app/services/ -q` |
| Already extracted | `tools_schema.py`, `prompts.py`, `tool_dispatch.py`, `guard_rules.py`, `research_rules.py`, `stream_utils.py`, `code_verifier.py`, `html_checker.py` | `ls backend/app/services/` |
| Static checks | `ruff --select=F,E9,B` clean | run it |
| Extension type-check | `tsc --noEmit` clean | `cd orion && npx tsc --noEmit -p tsconfig.json` |

If any row disagrees, stop and reconcile first — this plan assumes exactly this state.

## 1. The SSE contract (DO NOT CHANGE THESE NAMES)

The backend emits exactly seven event types; the extension handles exactly these
seven (verified with the integration-contract checker). Phase C moves code around
but **must not rename, add or remove any of these** without a matching change in
`orion/src/panels/ChatPanel.ts` (`_processStreamData`) — and such a change is a
separate, deliberate commit, never a side effect of refactoring.

| Event | Payload | Extension behaviour |
|---|---|---|
| `status` | `text` | activity log line / progress |
| `token` | `text` | append to assistant bubble (markdown rendered) |
| `ask_user` | `question` | stop spinner, render question, re-enable input |
| `confirm_terminal` | `command` | show approval modal, call `/execute-terminal`, feed result back |
| `todo_update` | `markdown`, `todos` | render the todo checklist box |
| `error` | `text` | show error bubble |
| `done` | — | stop spinner, re-enable input |

Guarantee to preserve: **every run ends with exactly one `done`** (wrapper
`stream_utils.jamin_done`), and a genuine crash must NOT produce `done`
(test `test_stream_utils.py::test_crash_tidak_disamarkan_sebagai_selesai`).

## 2. Closure inventory of `_aliran_inti`

Names the generator closes over or mutates. When extracting, every one of these
must become either a field of `SessionState` (step 2) or an explicit dependency
(step 4). Search by name; do not trust line numbers.

Closed over (read): `context_manager`, `todo_manager`, `tool_executor`,
`orion_config`, `static_system_prompt` (now `SYSTEM_PROMPT`), `TOOLS`.

Mutated per turn: `files_changed_this_turn`, `analyzed_files`,
`pending_system_notices`, `_nudge_count`, `_verifikasi_count`,
`_pending_image_parts` (dormant attachment support), `current_iter`,
`total_input_tokens` / `total_output_tokens` (performance logging).

Loop counter: **`_`** — guarded by `test_loop_counter_guard.py`; never reuse `_`
as a throwaway inside the loop.

## 3. The four steps (one commit each)

### Step 1 — `sse.py`: event builders (risk: low)
Replace every `yield f"data: {json.dumps({...})}\n\n"` with `yield sse.ev(...)`.
Pure call-shape change; no behaviour change.
*Accept:* 77 tests green; `git diff` touches only `yield` lines; the seven event
names above unchanged; a recorded baseline transcript (section 5) is byte-identical
in event sequence.

### Step 2 — `session_state.py`: the per-turn state (risk: medium)
Move the mutated names above into a `SessionState` dataclass with methods
(`catat_file_berubah`, `tambah_notice`, `reset_per_turn`, `sisa_todo`, …).
The loop stays in `main.py` for now, but stops carrying 10 loose variables.
*Accept:* 77 tests green; new `test_session_state.py` covering `reset_per_turn`
and file-change accounting; no behaviour change observable in baseline transcript.

### Step 3 — `loop_guards.py`: the four gates as one decision (risk: medium)
Collapse the four decision points (todo-continuation nudge, claim-challenge gate,
checkpoint/pause, normal answer) into
`evaluasi_akhir_iterasi(state, konten, ...) -> Keputusan` with enum
`LANJUT_TODO | TANTANG_KLAIM | CHECKPOINT | JAWAB_NORMAL | PAUSE_ITERASI`.
The loop becomes a `match` on that decision. Pure logic moves to functions that
can be unit-tested with synthetic inputs.
*Accept:* previous tests green; new tests feed five synthetic situations and assert
five distinct decisions, including "claim with zero changed files" and
"todos still pending".

### Step 4 — `agent_loop.py`: the generator itself (risk: high, last)
Move `_aliran_inti` to `app/services/agent_loop.py` as
`async def aliran_agent(state: SessionState, deps: Deps) -> AsyncIterator[str]`
where `Deps` holds: `context_manager`, `todo_manager`, `tool_executor`,
`llm_call` (the heartbeat-wrapped `get_ai_response`), `kirim_status` (sse),
`config`, `logger`. `main.py` keeps HTTP routing, builds `SessionState`/`Deps`,
and wraps with `stream_utils.jamin_done`.
*Accept:* all tests green; consistency check KONSEN; and the four manual scenarios
in section 6 pass in VS Code.

## 4. Hard rules

1. One step = one commit. If a step fails verification, `git checkout -- <files>`
   and redo that step; never stack fixes across steps.
2. **No behaviour changes while moving.** Behaviour fixes are separate commits
   after Phase C completes. Record temptations in the Backlog section below.
3. Run `python -m pytest backend/app/services/ -q` after every pattern
   replacement, not only at step end.
4. Never reuse `_` inside the loop (see guard test).
5. The seven SSE event names are frozen (section 1).

## 5. Baseline transcript procedure (do this BEFORE step 1)

Record one real conversation's event stream so steps can be compared against it:

```powershell
$body = @{ message = "list the files in this project and summarize them" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/set-project" -ContentType "application/json" -Body (@{ path = "D:\APP Grenitho\orion-project" } | ConvertTo-Json)
Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:8000/chat" -ContentType "application/json" -Body $body -OutFile baseline_sse.txt
```

Keep `baseline_sse.txt` outside the repo. After each step, re-record and diff the
**event-type sequence** (ignore timings and token text): it must be identical.

## 6. Manual acceptance scenarios (after step 4)

a) multi-item task ("add X, test X, update README") → todo list appears, all items done
b) a task that triggers a dangerous command → approval modal, then continuation
c) a deliberately broken file + "fix it" → `verify_code` cited with line number
d) press Cancel mid-generation → clean cancel, input re-enabled, no phantom error

## 7. Rollback

Every step is one commit: `git revert <sha>` restores the previous state without
touching later work. The pre-Phase-C tag is recommended before step 1:
`git tag pre-fase-c && git push --tags`.

## 8. Definition of done

- `main.py` under ~700 lines, containing only HTTP routing + composition
- 77+ tests green, including new `session_state` / `loop_guards` tests
- baseline event sequence unchanged
- four manual scenarios pass
- `STATUS_ORION.md` updated with new module map

## Backlog (temptations deferred during Phase C)

- (empty — add here anything you notice but must not fix while moving code)

---

## After Phase C: Phase D — internationalization (portfolio polish)

See `DEVELOPMENT_ROADMAP.md`. Summary: translate user-facing status strings and
tool result messages to English (updating the tests that assert them), translate
code comments, and translate the system prompt last, as its own commit, with a
before/after behaviour comparison. **Never** translate chat replies — those follow
the user's language by design. `.orion/*` and `session_history.json` are gitignored
user data and never ship; they need no translation.
