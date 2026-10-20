# FASE C — Execution Plan: extracting the agent loop out of main.py

**Status:** COMPLETE — all four steps are committed: step 1 `d8af462`
(`sse.py`), step 2 `14e0b6e` (`session_state.py`), step 3 `4534987`
(`loop_guards.py`), step 4 `4ee5f46` (`agent_loop.py`). Tag `pre-fase-c`
points at `da208b2`. Phase D (i18n) was executed between step 1 and step 2
and is complete — see the Phase D section at the bottom. This document
remains the reference for what was done and for re-verifying it. Read all of
it before touching the loop again.

Companion documents: `docs/README.md` (what is canonical), `STATUS_ORION.md`
(agent-context, Indonesian), `DEVELOPMENT_ROADMAP.md` (what comes after).

---

## 0. Current state snapshot (verify these before starting)

| Fact | Value | How to check |
|---|---|---|
| `main.py` size | ~569 lines — HTTP routing + composition only | `wc -l backend/app/main.py` (or PowerShell `(Get-Content …).Count`) |
| Agent loop | `async def aliran_agent(state, deps)` in `app/services/agent_loop.py` | search `aliran_agent` |
| Tools | 22 declared & dispatched | startup log line `[TOOLS] Consistent: 22 tools declared & dispatched.` |
| Tests | 97 passing | `python -m pytest backend/app/services/ -q` |
| Already extracted | `tools_schema.py`, `prompts.py`, `tool_dispatch.py`, `guard_rules.py`, `research_rules.py`, `stream_utils.py`, `code_verifier.py`, `html_checker.py`, `sse.py`, `session_state.py`, `loop_guards.py`, `agent_loop.py` | `ls backend/app/services/` |
| Static checks | `ruff --select=F,E9,B` clean | run it |
| Extension type-check | `tsc --noEmit` clean | `cd orion && npx tsc --noEmit -p tsconfig.json` |
| Code language | English strings/comments/prompt (Phase D done); identifiers deliberately stay Indonesian | read any module |

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

### Step 1 — `sse.py`: event builders (risk: low) — ✅ DONE (`d8af462`)
Replace every `yield f"data: {json.dumps({...})}\n\n"` with `yield sse.ev(...)`.
Pure call-shape change; no behaviour change.
*Accept:* 77 tests green; `git diff` touches only `yield` lines; the seven event
names above unchanged; a recorded baseline transcript (section 5) is byte-identical
in event sequence.
*Result:* all 47 sites replaced by an AST transform; `test_sse.py` added (byte
identity with the legacy inline form, `ev('done') == stream_utils.buat_event_done()`,
AST guard against manual frames and non-frozen names); 82 tests green; baseline
transcripts re-recorded and **byte-identical**, not just sequence-identical.

### Step 2 — `session_state.py`: the per-turn state (risk: medium) — ✅ DONE (`14e0b6e`)
Move the mutated names above into a `SessionState` dataclass with methods
(`catat_file_berubah`, `tambah_notice`, `reset_per_turn`, `sisa_todo`, …).
The loop stays in `main.py` for now, but stops carrying 10 loose variables.
*Accept:* 77 tests green; new `test_session_state.py` covering `reset_per_turn`
and file-change accounting; no behaviour change observable in baseline transcript.
*Result:* 88 tests green (6 new); five baseline transcripts byte-identical.
Inventory correction recorded in the module docstring: `total_input_tokens`/
`total_output_tokens` are locals of the `/performance` route, not loop state.

### Step 3 — `loop_guards.py`: the four gates as one decision (risk: medium) — ✅ DONE (`4534987`)
Collapse the four decision points (todo-continuation nudge, claim-challenge gate,
checkpoint/pause, normal answer) into
`evaluasi_akhir_iterasi(state, konten, ...) -> Keputusan` with enum
`LANJUT_TODO | TANTANG_KLAIM | CHECKPOINT | JAWAB_NORMAL | PAUSE_ITERASI`.
The loop becomes a `match` on that decision. Pure logic moves to functions that
can be unit-tested with synthetic inputs.
*Accept:* previous tests green; new tests feed five synthetic situations and assert
five distinct decisions, including "claim with zero changed files" and
"todos still pending".
*Result:* 97 tests green (9 new: the five required situations plus TERUSKAN,
first-iteration claim, and both quota-degradation edges); five baseline
transcripts byte-identical. Documented deviation: the enum has a sixth member
(`TERUSKAN`) because the max-iteration pause lives inside the short-reply
branch — splitting that branch between loop and evaluator would have been
less faithful.

### Step 4 — `agent_loop.py`: the generator itself (risk: high, last) — ✅ DONE (`4ee5f46`)
Move `_aliran_inti` to `app/services/agent_loop.py` as
`async def aliran_agent(state: SessionState, deps: Deps) -> AsyncIterator[str]`
where `Deps` holds: `context_manager`, `todo_manager`, `tool_executor`,
`llm_call` (the heartbeat-wrapped `get_ai_response`), `kirim_status` (sse),
`config`, `logger`. `main.py` keeps HTTP routing, builds `SessionState`/`Deps`,
and wraps with `stream_utils.jamin_done`.
*Accept:* all tests green; consistency check KONSEN; and the four manual scenarios
in section 6 pass in VS Code.
*Result:* main.py 569 lines; 97 tests green; `[TOOLS] Consistent: 22 tools`;
five baseline transcripts byte-identical; guard tests repointed to
agent_loop.py. Implementation notes: the heartbeat while-loop stays inside
the generator and `Deps.llm_call` carries the raw `get_ai_response` (a
heartbeat that yields SSE frames cannot be a plain async function);
`Deps` also carries the two per-request inputs `context_message` and
`lampiran_desc`; `Deps.tool_executor` is a per-request snapshot (documented
in the module docstring). Manual scenarios a/b are covered by the mock
recordings; c/d await a VS Code pass.

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

> **Without API keys (CI / sandbox):** the same procedure works against a
> deterministic mock: leave `GEMINI_API_KEY_*` unset and point Tier 2 at a local
> OpenAI-compatible mock (`FALLBACK_BASE_URL=http://127.0.0.1:9100/v1`,
> `FALLBACK_API_KEY=mock`, `FALLBACK_MODELS=mock-model`) that replays a scripted
> tool call followed by a final text answer. Step 1 was verified this way with
> two scenarios (tool call → answer, and `update_todos` → `ask_user`, the latter
> also exercising the `jamin_done` wrapper); both transcripts came out
> byte-identical before/after. A mock proves plumbing and event ordering, not
> model behaviour.

## 6. Manual acceptance scenarios (after step 4)

Scenarios c and d have a checked-in runbook with server-side evidence:
`docs/RELEASE_GATES_MANUAL.md`. Scenarios a and b are covered end-to-end
by the mock harness recordings.

a) multi-item task ("add X, test X, update README") → todo list appears, all items done
b) a task that triggers a dangerous command → approval modal, then continuation
c) a deliberately broken file + "fix it" → `verify_code` cited with line number
d) press Cancel mid-generation → clean cancel, input re-enabled, no phantom error

## 7. Rollback

Every step is one commit: `git revert <sha>` restores the previous state without
touching later work. The pre-Phase-C tag is recommended before step 1:
`git tag pre-fase-c && git push --tags`.

## 8. Definition of done

- `main.py` under ~700 lines, containing only HTTP routing + composition — **done: 569 lines**
- 77+ tests green, including new `session_state` / `loop_guards` tests — **done: 97 green (6 + 9 new)**
- baseline event sequence unchanged — **done: five mock scenarios, transcripts byte-identical across all four steps**
- four manual scenarios pass — **a/b covered end-to-end by mock scenarios D (todo nudge → checkpoint) and C (dangerous command → `confirm_terminal`); c (broken file → `verify_code` cited) and d (Cancel mid-generation) still need a manual pass in VS Code before release**
- `STATUS_ORION.md` updated with new module map — **done**

## Backlog (temptations deferred during Phase C)

Both items below were fixed in UPDATE-004, after Phase C completed, each in
its own commit with its own test — exactly as this section prescribed.

- **FIXED (`c0fd24b`): `edit_file` success is never recorded in
  `files_changed_this_turn`.** The loop gates evidence and post-edit
  verification on results starting with "success"; edit_file now returns
  "Success: edited file ...", so pure-edit turns produce evidence and the
  post-edit verifier runs. Covered by a contract test in test_tool_dispatch
  and by SSE baseline scenarios f (write+edit+claim) and g (edit-only+claim,
  which now reaches the checkpoint instead of being challenged twice).
- **FIXED (`4f3908c`): system prompt told the model to `git push` via
  run_terminal** while the code guard blocks it there. The prompt's git
  section now routes checkpoints through the `git_commit` tool, names push
  as the user's manual action, and falls back to `.orion/backups` for
  non-repo projects — consistent with GIT_DIBLOKIR and the anti-hallucination
  rules.

---

## After Phase C: Phase D — internationalization (portfolio polish) — ✅ DONE (after step 1)

See `DEVELOPMENT_ROADMAP.md`. Summary: translate user-facing status strings and
tool result messages to English (updating the tests that assert them), translate
code comments, and translate the system prompt last, as its own commit, with a
before/after behaviour comparison. **Never** translate chat replies — those follow
the user's language by design. `.orion/*` and `session_history.json` are gitignored
user data and never ship; they need no translation.

**Executed** (out of the plan's order, right after step 1, on explicit instruction):

| Commit | Package |
|---|---|
| `a82c370` (+ fixups `486a4a0`, `c979c13`) | user-facing status strings + tool result messages + the tests asserting them |
| `00c19b5` | extension UI strings (+ backendManager console logs) |
| `c23cd39` | developer-facing backend log strings |
| `f9cee02` | all code comments & docstrings (AST-fingerprint-verified comments-only) |
| `24e3cf4` | test assertion messages + webview-embedded comments |
| `0da6394` | system prompt + tool descriptions + every `[SYSTEM ...]` injection, with before/after comparison (21/21 sections, 56/56 rules, identical tool-name set, 7003→5275 cl100k tokens, SSE baseline byte-identical) |

Deliberately **not** translated: chat replies (by design), identifiers
(`jamin_done`, `_aliran_inti`, ... — steps 2-4 and the guard tests are written
against them), `KATA_KLAIM` and the anti-amnesia keyword list (they match model
output, which follows the user's language — kept bilingual and documented),
production-transcript fixtures in `test_guard_rules.py`, historical Indonesian
docs (labelled by `docs/README.md`).

Steps 2-4 of Phase C remain open; the snapshot table in section 0 reflects the
post-Phase-D state.
