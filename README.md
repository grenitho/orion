# Orion — AI Software Engineer Assistant for VS Code

**A local-first, verification-driven coding agent.** Orion reads your project, plans
multi-step work, edits code, runs commands, and — most importantly — **checks its own
claims with real parsers and compilers before reporting success.**

Orion is composed of two parts that talk over localhost:

- a **FastAPI backend** (Python) that owns the agent loop, tool execution, context
  management and LLM routing, and
- a **VS Code extension** (TypeScript) that provides the chat panel, approval modals,
  activity log and editor intelligence.

---

## Why Orion exists

Most coding assistants fail in two ways: they **stop halfway** through multi-part
requests, and they **report success for work they never did**. Orion is built around
closing exactly those two failure modes, with mechanisms that are enforced in code and
covered by tests — not with prompt wording alone.

- **Multi-task completion.** Requests are tracked as an explicit todo list. If the model
  claims completion while items remain pending, the loop injects a continuation message
  and keeps working instead of stopping.
- **Claim verification.** A completion claim with zero files changed is rejected and
  challenged: the model must cite file + line evidence, or admit the work is not done.
- **Real eyes on the code.** `verify_code` runs actual parsers/compilers
  (`node --check`, `tsc`, Python `compile`, JSON/XML/YAML/CSS parsers, HTML structural
  checks) across 16 file types. Syntax errors are reported with line numbers, because a
  language model cannot reliably see a missing parenthesis — a parser can.
- **Editor awareness.** Every request carries the active file, the current selection and
  the diagnostics from VS Code's *Problems* panel, so the agent knows when its own edit
  broke something.
- **Honest failure.** Every agent run ends with exactly one terminal event
  (`completed` / `needs_confirmation` / `failed` / `rate_limited`). A stream that ends
  without one is completed by a wrapper, and a genuine crash is still distinguishable
  from a normal stop.

---

## Feature overview

| Area | Capability |
|---|---|
| **Tools (22)** | file read/write/edit (SEARCH-REPLACE), ranged read, JSON path read, code search, terminal execution with timeout + process-tree kill, todo management, user questions, web search, web page fetch (SSRF-guarded), `verify_code`, `run_tests`, git status/diff/commit, backups list/restore, delete/move with backup |
| **LLM routing** | primary Gemini with N-key rotation → OpenRouter fallback → Cloudflare fallback; per-tier temperature; retry with exponential backoff + jitter; `reasoning_effort` mapped to Gemini thinking levels |
| **Context** | persistent session history with orphaned tool-call validation, atomic writes and backups; automatic summarization when approaching token budget; safe history splitting that never separates an assistant call from its tool response |
| **Safety** | path-traversal guard on every file operation; confirmation modals for dangerous commands; destructive/remote git operations blocked at code level; automatic `.bak` before overwrite; reversible restores; SSRF protection on fetched URLs |
| **UX** | streaming status events (including a "still thinking… Ns" heartbeat), markdown + syntax highlighting in the chat panel, per-bubble Copy button for the raw message text (0.4.1+), activity view, performance dashboard, cancel button with real abort |
| **Assistant behaviour** | three-layer system prompt (conversation contract · non-negotiable floor · working style) with test-enforced anti-bloat ratchets: answer the question that was asked, name absent things plainly with options, facts over victory words, per-item audits — UPDATE-023 |
| **Claim integrity** | completion claims are gated on PER-FILE evidence: an edit ledger records every successful and failed write/edit attempt (guard rejections and worker sub-agent edits included); a claim that contradicts the ledger is challenged naming the failed files (hard challenge, full quota); a completion claim with zero changed files gets ONE soft answer-first correction instead of the old double evidence ritual (UPDATE-023); every successful write/edit carries `(mtime_ns, size)` disk evidence, a reported-success write that left the file unchanged returns an explicit error, and `.py` edits get an instant automatic syntax verdict (UPDATE-022) |
| **Quality** | 390 automated tests, including regression guards for every safety mechanism; startup consistency check between declared tools and dispatched tools |

---

## Architecture

```
┌─────────────────────────── VS Code ───────────────────────────┐
│  Chat panel (webview)  ◄──postMessage──►  Extension host (TS) │
│        ▲                                        │              │
│        │ status/token/ask_user/done events      │ fetch (SSE)  │
└────────┼────────────────────────────────────────┼──────────────┘
         │                                        ▼
         │                         ┌──────────────────────────┐
         │                         │  FastAPI backend (Python)│
         │                         │  /chat  /set-project     │
         │                         │  agent loop + 22 tools   │
         │                         │  LLM router (3 tiers)    │
         │                         └──────────┬───────────────┘
         │                                    ▼
         │                     Gemini / OpenRouter / Cloudflare
         └── diagnostics, active file, selection (sent with every request)
```

The backend binds to `127.0.0.1` only and CORS is closed; see
[SECURITY.md](SECURITY.md) for the full threat model, including what Orion
deliberately does **not** protect against.

---

## Requirements

- Python **3.11+** (the one-line installer checks/installs it for you)
- Node.js **18+** — optional; only needed for `verify_code` on JS/TS projects
  and to build the extension from source
- VS Code **1.125+**
- At least one LLM provider key (free Gemini keys are enough; OpenRouter /
  Cloudflare / any OpenAI-compatible endpoint optional as fallback tiers)

---

## Quick start

### Fastest route (Windows, no experience needed)

Install the **Orion — AI Software Engineer** extension (`grenitho.orion-ai`)
from the VS Code Marketplace, then run one line in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
```

It installs the backend, dependencies and your free Gemini keys, then tells
you the last two clicks (`orion.backendPath` setting + reload). Full
step-by-step guide, troubleshooting and multi-provider LLM recipes:
[docs/PANDUAN_SETUP.md](docs/PANDUAN_SETUP.md).

### Manual route (any OS)

#### 1. Backend

```bash
cd backend
python -m venv .venv                # both .venv and venv are auto-detected
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # then fill in your keys
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 2. Extension — only when building from source

(Otherwise just install `grenitho.orion-ai` from the VS Code Marketplace.)

```bash
cd ../orion
npm install
npm run package                     # type-check + lint + production build
npx vsce package                    # produces orion-ai-<version>.vsix
code --install-extension orion-ai-<version>.vsix --force
```

For development, open the `orion/` folder in VS Code and press **F5** to launch an
Extension Development Host that loads straight from source.

### Your first task (both routes)

Open the **Orion** activity bar icon, set your project folder, and talk to it:

> "Add a dark-mode toggle, write a test for it, and update the README."

Orion will create a todo list, work through it item by item, verify its edits with
`verify_code` / `run_tests`, ask for approval before dangerous commands, and checkpoint
with `git_commit` when the work passes.

---

## Configuration

All secrets and knobs live in `backend/.env` (never committed). The most relevant:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY_1`, `_2`, … | any number of Gemini keys; rotated per request |
| `DEFAULT_MODEL` | e.g. `gemini/gemini-3.5-flash-lite` |
| `FALLBACK_API_KEY`, `FALLBACK_BASE_URL`, `FALLBACK_MODELS` | any OpenAI-compatible endpoint: OpenRouter (one key → OpenAI/Anthropic/DeepSeek/Qwen/…), OpenAI, DeepSeek, Qwen/DashScope, Groq — recipes in [docs/PANDUAN_SETUP.md](docs/PANDUAN_SETUP.md) |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODELS` | Cloudflare tier |
| `LLM_REASONING_EFFORT` | `minimal` / `low` / `medium` / `high` (default `high`) |
| `LLM_TIMEOUT`, `LLM_MAX_RETRIES_PER_MODEL`, `LLM_BACKOFF_BASE` | resilience knobs |
| `ORION_MAX_TODO_NUDGE`, `ORION_MAX_VERIFIKASI` | limits for the continuation and claim-challenge loops |

Runtime behaviour per project lives in `.orion/orion_config.json`
(`max_iterations`, token budget thresholds, `subagents_*`, `mcp_enabled`,
`inbox_*`, `subagent_models`).

**MCP tool servers (backend 0.7.0+):** extend Orion's 22 built-in tools
with any local [Model Context Protocol](https://modelcontextprotocol.io)
server via `<project>/.orion/mcp.json` — full reference, failure contract
and a 5-minute demo-server walkthrough in
[docs/MCP_FOUNDATION.md](docs/MCP_FOUNDATION.md). No
config file = no behavioural change. Since extension 0.0.11 the same
configuration is manageable without touching JSON: the **MCP Servers**
item in the activity bar opens a panel (server table, add/edit form,
enable-disable, one-click demo install), and the Setup Wizard gained an
optional sixth card for it — see `docs/MCP_FOUNDATION.md` §11.

**Sub-agents (backend 0.8.0+):** for exploration-heavy investigation,
independent post-edit verification and mechanical multi-file work, Orion
can delegate to bounded internal sub-agents (Explorer / Verifier / parallel
Workers with disjoint write scopes). Reports return as ordinary tool
results; budgets live in `.orion/orion_config.json` (`subagents_*`); every
delegation shows in the activity log. See [docs/SUBAGENTS.md](docs/SUBAGENTS.md).

**Inbox & model profiles (backend 0.9.0+, extension 0.2.0):** drop files
into `<project>/.orion/inbox/` (text/code, png/jpg/webp, pdf, docx, xlsx)
and the next chat turn ingests them under hard caps, archives them (never
deletes) and announces `📥 Inbox: …` — attachments without an attachment
UI. And each sub-agent role can be pinned to one model from the credential
groups you already configured (`subagent_models` in
`.orion/orion_config.json`), managed from the Setup Wizard's optional
**Sub-agent models** card with a per-role Test probe. Empty state = exactly
the old behaviour. Full reference:
[docs/INBOX_AND_PROFILES.md](docs/INBOX_AND_PROFILES.md).

**Attach & quality-of-life (extension 0.3.0+, documents since 0.4.0):** the
chat panel's 📎 button attaches images, text/code AND pdf/docx/xlsx (extracted
server-side by the inbox engine) to ONE message (same pipeline and caps as
the inbox); since 0.4.1 every chat bubble carries a **Copy** button that
copies that message's raw pre-markdown text; the MCP panel carries the **master-switch button** (mcp_enabled,
applied live, mcp.json preserved); and a watchdog toasts once when the
backend goes stale mid-session (git pull / extension update) with a
Restart action.

---

## Testing

```bash
python -m pytest backend -q         # from the repo root
```

393 tests cover the attachment pipeline, the inbox drop-zone, the model-profile
router and endpoints, the sub-agent plumbing, the claim-verification gate and
the failure-aware edit ledger (per-file claim evidence, UPDATE-022), the
prompt-architecture ratchets and the one-shot soft challenge (UPDATE-023), the
per-write disk evidence and automatic syntax check, the loop-counter guard,
the git-operation blocklist, the stream-completion wrapper, the
research-query builder and the security posture (CORS, loopback bind, token
guard, absence of personal paths and private artifacts). Several of these tests
exist because a real production incident proved the bug first; their docstrings
say so.

---

## Project layout

```
backend/
  app/
    main.py              FastAPI app, agent loop, SSE contract
    logging_setup.py     rotating file logging (stdout stays quiet on purpose)
    services/
      llm_router.py      3-tier provider router with backoff
      tool_executor.py   22 tools, path guard, git guard, backups
      code_verifier.py   multi-language static verification
      html_checker.py    HTML structural checks
      context_manager.py persistent, self-healing session history
      todo_manager.py    explicit task list
      guard_rules.py     claim detection & challenge policy (pure, tested)
      research_rules.py  auto-research query building (pure, tested)
      stream_utils.py    terminal-event guarantee (tested)
      mcp_manager.py     MCP client: config, lifecycle, tool registry (UPDATE-014)
      subagent_manager.py  Explorer/Verifier/Workers delegation (UPDATE-016)
      inbox_manager.py   .orion/inbox drop-zone: extract, cap, archive (UPDATE-018)
      permission_manager.py, dependency_scanner.py, post_edit_verifier.py
      test_*.py          393 tests
  examples/
    mcp_demo_server.py   dependency-free MCP server for E2E testing
orion/                   VS Code extension (TypeScript, esbuild)
docs/                    status, roadmap and feature notes
SECURITY.md              threat model and honest limitations
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backend silent / chat reports a lost connection | Read the logs in the **backend install's** `.orion\logs\` folder — the folder that CONTAINS `backend/` (your repo root on a dev install), *not* the active chat project when the two differ: `orion_stdout.log` (startup + routing) and `backend.log` (structured, fallback `backend\logs\backend.log`); pre-0.0.9: `~\orion_stdout.log`. Make sure port 8000 is free |
| Suspect two backends / a stale process | Token-guarded `GET /runtime/forensics` (backend 0.9.1+) compares the PID-file claim, the answering process and the code mtime, and answers with plain-English verdicts (`TWIN SUSPECTED`, `STALE CODE`, …); the same evidence is logged at every boot as `[ORION RUNTIME WARNING] TWIN SUSPECTED` |
| `No module named 'mcp'` on the first chat | `pip install mcp` inside the backend venv (newer litellm needs it for tool calls) |
| Extension shows a stale UI after updating | Fully restart VS Code and close old chat tabs |
| `401 unauthorized` from chat or dashboard | Shared-token mismatch: restart the backend once, then reload the VS Code window; both re-read `~/.orion_shared_secret` |
| Two Orion extensions installed at once | Keep exactly one (`code --list-extensions`); duplicate copies split the activity log and the chat panel |
| MCP server never starts / `[MCP SERVER DOWN]` | See the MCP troubleshooting table in [docs/MCP_FOUNDATION.md](docs/MCP_FOUNDATION.md) §10 — usually a wrong absolute `command` path; on Windows wrap `npx` servers in `cmd /c` |
| Dropped a file into `.orion/inbox/` but no `📥` line appears | Ingest runs once per chat turn and the manifest (`.orion/inbox_state.json`) remembers each processed file *version* — modify the file or drop a new name; check `inbox_enabled`; see [docs/INBOX_AND_PROFILES.md](docs/INBOX_AND_PROFILES.md) |

The extension auto-starts the backend when the opened workspace contains (or
sits near) this repository's `backend/` folder, or when the setting
`orion.backendPath` points at it; otherwise start it manually as in Quick
start §1. Everything Orion remembers about a project lives in that project's
`.orion/` folder — gitignored, never packaged, and safe to delete to reset
its memory.

---

## Roadmap

Done and guarded by tests: multi-task continuation, claim verification, editor
diagnostics, multi-language verification, test runner, auto-research on repeated
failure, safe file operations with reversible restores, git checkpoints, heartbeat
progress, closed CORS + loopback bind.

Done since: the `main.py` split is complete (Phase C: `sse.py`, `session_state.py`,
`loop_guards.py`, `agent_loop.py` — 2,769 → 569 lines) and the whole code surface
is English (Phase D: UI, logs, comments, system prompt), while chat replies keep
following the user's language.

Planned next (renumbered in UPDATE-018): **019** defect-hunting & forensics
(`validate_project_structure` ignoring `node_modules`/`__pycache__`, the
twin-process forensics from incident 013), **020** UX polish (a webview
attach button forwarding into the same pipeline the inbox uses, onboarding
fixes), **021** a borrowed-stack trial for the client's ERP
(PocketBase/SQLite → Supabase, n8n-mcp, playwright analytics), **022** the
standalone non-VS Code client (vision, post-1.0). Everything else the old
roadmap promised — setup wizard, multi-provider tiers, MCP tool servers,
parallel sub-agents — has shipped.

---

## Limitations (read this before trusting it)

- Orion executes shell commands **with your user privileges**. There is no process
  sandbox. Run it on machines you control, never as administrator/root, and keep the
  approval modals enabled.
- The model can be misled by hostile content in files or web pages (prompt injection).
  Verification tools and approval modals are the mitigation, not a guarantee.
- Output quality depends on the model tier you configure; lightweight models are fast
  but need the verification scaffolding above to be trustworthy.

## License

MIT — see [LICENSE](LICENSE).
