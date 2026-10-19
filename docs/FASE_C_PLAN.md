# FASE C — Execution Plan: extracting the agent loop out of main.py

**Status:** STEP 1 DONE (commit `d8af462`, `sse.py` extracted, baseline
byte-identical, tag `pre-fase-c` points at `da208b2`). Steps 2-4 NOT STARTED.
Phase D (i18n) was executed after step 1 and is complete — see the Phase D
section at the bottom. This document is the single source of truth for the
remaining steps. Read all of it before touching code. Written to be executable
by an agent or engineer with **no prior context** beyond this repository.

Companion documents: `docs/README.md` (what is canonical), `STATUS_ORION.md`
(agent-context, Indonesian), `DEVELOPMENT_ROADMAP.md` (what comes after).

---

## 0. Current state snapshot (verify these before starting)

| Fact | Value | How to check |
|---|---|---|
| `main.py` size | ~2,119 lines | `wc -l backend/app/main.py` (or PowerShell `(Get-Content …).Count`) |
| Agent loop | `async def _aliran_inti()` inside `/chat` | search `_aliran_inti` |
| Tools | 22 declared & dispatched | startup log line `[TOOLS] Consistent: 22 tools declared & dispatched.` |
| Tests | 82 passing (77 + 5 in `test_sse.py`) | `python -m pytest backend/app/services/ -q` |
| Already extracted | `tools_schema.py`, `prompts.py`, `tool_dispatch.py`, `guard_rules.py`, `research_rules.py`, `stream_utils.py`, `code_verifier.py`, `html_checker.py`, `sse.py` (step 1) | `ls backend/app/services/` |
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

- **`edit_file` success is never recorded in `files_changed_this_turn`.** main.py
  gates the recording on `result.lower().startswith("success")` (pre-i18n:
  `"sukses"`), but `ToolExecutor.edit_file` returns a message starting with
  "Edited file ..." (pre-i18n: "Berhasil mengedit ..."), so only `write_file`
  ever trips the gate. Pre-existing behaviour; Phase D preserved it exactly
  (the translated edit result deliberately does NOT start with "Success").
  Fixing it is a behaviour change (the claim-verification gate would see
  evidence after pure-edit turns) and belongs in its own commit with its own
  test, after Phase C.
- **System prompt tells the model to `git push` via run_terminal**, while the
  code-level guard blocks `git push` there (by design; the prompt's own git
  section also says pushes are the user's manual action). The instructions
  contradict each other; reconciling them is a prompt-behaviour change, not
  i18n. Left verbatim during Phase D.

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
