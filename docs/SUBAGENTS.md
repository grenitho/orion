# Orion Sub-Agents (UPDATE-016)

Status: shipped in backend 0.8.0 · extension unchanged by this update ·
closes the MCP phase together with UPDATE-014 (foundation) and
UPDATE-015 (configuration UI) → milestone **v0.1.0**.

Orion's main agent loop can delegate bounded work to **internal
sub-agents**. They are not MCP servers and not separate processes: they are
capped mini agent-runs inside the backend, each with its own in-memory
conversation, a fixed tool allowance, and a single report that returns to
the main loop as an ordinary tool result.

---

## 1. The three roles

| Role | Tool | Tools it may use | Returns |
|---|---|---|---|
| **Explorer** | `delegate_explorer` | read-only: list/read/search | one condensed findings report |
| **Verifier** | `delegate_verifier` | read-only + verify_code, verify_html, run_tests, git_diff, git_status | verdict (PASS / PASS WITH NOTES / FAIL) + defect list |
| **Workers** | `delegate_workers` | read-only + write_file/edit_file **inside assigned scopes only** | one report per worker, in submission order |

When to expect them: since UPDATE-022 the main model is instructed to
delegate PROACTIVELY, on its own judgement, without waiting to be asked —
exploration of unfamiliar code (roughly three files or more), an
independent Verifier check before any non-trivial completion claim, and
mechanical work across two or more disjoint files. Trivial single-file
actions it still does itself, and the per-turn budget caps (§3) keep the
initiative from burning quota. You can also ask for it explicitly in chat
("kirim Verifier untuk cek ulang hasil edit"); an explicit user
instruction — to delegate or not to — always wins.

## 2. Hard rules (v1)

* **Depth 1** — sub-agents have no delegation tools; they cannot spawn.
* **No user contact** — no `ask_user`, no `confirm_terminal`, and no
  `run_terminal` in any sub-agent toolset: dangerous commands remain
  exclusively in the main loop, behind your approval modal.
* **No shared memory** — a sub-run's conversation lives in memory only;
  `session_history.json` never grows from sub-agent chatter (quota +
  clarity). Only the final report enters the main history, as a tool result.
* **Disjoint write scopes** — `delegate_workers` rejects overlapping file
  lists BEFORE any LLM call or write happens; each worker's writes are
  re-checked per call at execution time.
* **Bounded cost** — see §3.
* **Honest failure** — nothing raises into the main loop: budget, overlap,
  dead model or tool crashes all arrive as `[SUBAGENT ...]` strings the
  main model can read and react to.
* **Worker edits feed the main ledger (UPDATE-022)** — every worker
  `write_file`/`edit_file` outcome, INCLUDING scope rejections, is recorded
  in the main turn's edit ledger (`session_state`). A completion claim
  while a worker edit is still unrecovered gets challenged by name, exactly
  like a failed edit by the main agent itself.

## 3. Budgets (per project, `.orion/orion_config.json`)

Since UPDATE-017 the MAIN system prompt carries an explicit delegation
policy, and since UPDATE-022 that policy is PROACTIVE (delegate without
being asked; triggers for Explorer/Verifier/Workers; budget caps as the
quota guard rather than a reason to hesitate; an explicit user instruction
always wins) — alongside the standing rules: how to scope workers,
reports-as-evidence, budget honouring, built-ins-over-MCP-guests and
claim-word discipline. Every role prompt ends with a claim-discipline
sentence so sub-agent reports do not trip the main loop's completion-claim
gate. Delegation is therefore taught, not just available.

| Key | Default | Meaning |
|---|---|---|
| `subagents_enabled` | `true` | master switch |
| `subagent_max_per_turn` | `3` | delegations per single chat turn |
| `subagent_max_iterations` | `6` | LLM rounds inside one sub-run |
| `subagent_workers_max` | `3` | parallel workers per `delegate_workers` call |

All values are clamped to sane ranges; missing keys fall back to defaults,
so existing projects need no migration. Every delegation is visible in the
activity log as two frozen `status` frames:
`🤖 Sub-agent <role>: <task snippet or worker count> ...` and
`🤖 Sub-agent <role> finished.` — the report itself arrives in the tool
result, prefixed `[SUBAGENT REPORT • <role> • N iterations, M tool calls,
status <ok|capped|error>]`.

## 3b. Per-role model profiles (UPDATE-018)

Each role can be pinned to ONE model from a credential group that already
exists in the backend environment — the classic money-savers being a cheap
model for `explorer` and a strong model for `verifier`. Config key
`subagent_models` in `.orion/orion_config.json`:

```json
"subagent_models": {
  "explorer": { "group": "provider_2", "model": "deepseek/deepseek-chat" },
  "verifier": { "group": "gemini",     "model": "gemini/gemini-3.5-flash" },
  "workers":  null
}
```

* `null` / absent / `{}` = the **main router** (the pre-018 behaviour — the
  empty state changes nothing);
* `group` ∈ `gemini` | `provider_<N>` | `fallback`; `model` follows the
  router's usual prefix rules;
* **no new secrets**: a profile is a single-target queue built from the
  group's existing credentials (gemini profiles rotate the group's N keys);
* **a broken profile can never break a sub-run**: malformed entries are
  dropped at config-read time, and a group without credentials falls back
  to the main router with a `[LLM_ROUTER] [WARNING]` line;
* managed without touching JSON by the Setup Wizard's optional
  **Sub-agent models** card (dropdowns from `GET /models/options`, a real
  probe via `POST /models/test`, atomic save via `POST /models/config`).

Full reference: `docs/INBOX_AND_PROFILES.md` Part B.

## 4. Seeing the cost

Each sub-run is real LLM traffic (its own tool rounds). The defaults cap a
worst-case turn at 3 delegations × 6 iterations; the report header states
exactly how many iterations and tool calls happened, and
`.orion/performance_log.jsonl` keeps recording every model call as before.
If a project should never delegate: set `subagents_enabled` to `false` —
the tools then answer `[SUBAGENTS DISABLED]` and the main agent continues
with its built-ins.

## 5. Not in v1 (deliberate)

* sub-agents that call MCP tools (their toolsets are built-in-only);
* nested delegation (depth > 1);
* sub-agents talking to the user or requesting terminal approvals;
* filesystem-level write locks (scope enforcement is logical, at the
  dispatch and execution guards — disjointness is checked upfront);
* streaming sub-agent tokens into the chat (summaries only, by design).

## 6. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `[SUBAGENT BUDGET] ...` in a tool result | the turn hit `subagent_max_per_turn`; expected behaviour — continue the chat to reset the budget |
| `[SUBAGENT SCOPE] worker scopes overlap ...` | the model proposed overlapping file lists; nothing ran — ask again or split the task |
| `[SUBAGENT ERROR] ...` | a model/tool failure inside a sub-run; the main agent sees it and can retry or report honestly |
| reports feel too short/long | tune `subagent_max_iterations`; the explorer/verifier prompts already demand condensed output |
| a pinned profile seems ignored | check the backend log for `[LLM_ROUTER] [WARNING] Sub-agent profile ... falling back to the main router` — the group has no credentials in this environment (or the entry is malformed); `GET /models/options` shows what the backend actually sees |
