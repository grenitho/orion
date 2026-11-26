# Orion — AI Software Engineer Assistant for VS Code

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![VS Code 1.125+](https://img.shields.io/badge/VS%20Code-1.125%2B-007ACC?logo=visualstudiocode&logoColor=white)](https://code.visualstudio.com/)
[![Tests](https://img.shields.io/badge/Tests-684%20passed-2ea44f)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A local-first, verification-driven coding agent.** Orion reads your project, plans multi-step work, edits code, runs commands, and — most importantly — **verifies its own claims with real parsers, compilers, and disk evidence before reporting success.**

Orion consists of two local components communicating strictly over `127.0.0.1`:

- **FastAPI Backend (`backend/`)** — Runs the agentic loop, 22 built-in tools, MCP client, semantic search index, sub-agent delegation, context pruning, and multi-tier LLM routing.
- **VS Code Extension (`orion/`)** — Provides the streaming chat panel, approval modals, Setup Wizard, MCP server manager, activity log, performance dashboard, and live editor diagnostics.

---

## Why Orion Exists

Most AI coding assistants fail in two predictable ways: they **stop halfway** through multi-part requests, or they **claim success for work they never actually completed**. Orion addresses both failure modes in code and enforces them with automated regression tests:

- **Multi-Task Continuation** — Requests are tracked as an explicit todo list. If the model tries to stop while tasks remain pending, the agent loop injects a continuation nudge until the list is finished.
- **Per-File Claim Verification** — Every write/edit records disk evidence `(mtime_ns, size)` in an edit ledger. Completion claims with zero modified files or unverified failed edits are automatically challenged.
- **Real Parser & Compiler Checks** — `verify_code` runs actual syntax/type checkers (`python compile`, `node --check`, `tsc`, JSON/YAML/XML/CSS/HTML validators) across 16 file types, reporting exact line numbers.
- **Live Editor Awareness** — Every turn includes your active file, cursor selection, and diagnostics from VS Code's *Problems* panel so Orion immediately sees if an edit introduces errors.
- **Deterministic Stream Termination** — Every agent run ends with a single terminal state (`completed`, `needs_confirmation`, `failed`, or `rate_limited`), distinguishing normal stops from genuine crashes.

---

## Key Capabilities

| Area | What Orion Does |
| :--- | :--- |
| **22 Built-in Tools** | File read/write/edit (`SEARCH`/`REPLACE`), ranged read, JSON path query, regex & semantic code search, terminal execution with timeout + process-tree kill, todo manager, user prompts, web search & SSRF-guarded fetch, `verify_code`, `run_tests`, git (`status`/`diff`/`commit`), backup snapshots/restore, and safe delete/move. |
| **Multi-Provider LLM Routing** | Primary Gemini with multi-key rotation, falling back across OpenAI-compatible endpoints (OpenRouter, Anthropic Claude, DeepSeek, Qwen, Groq) and Cloudflare Workers AI with exponential backoff, jitter, and configurable `reasoning_effort`. |
| **Claude Prompt Caching** | Automatic ephemeral cache-control breakpoints (`prompt_cache.py`) on system prompts, tool schemas, and stable conversation prefixes for Claude models, tracked via `cached_tokens` in performance telemetry. |
| **Semantic Code Search** | Incremental local codebase indexing (`semantic_search.py`) with automatic re-indexing on startup and file edits for fast symbol and concept retrieval. |
| **Bounded Sub-Agents** | Delegates heavy investigation, independent verification, or parallel disjoint file edits to specialized **Explorer**, **Verifier**, and **Worker** sub-agents, preserving key findings across context pruning via a per-project Findings Ledger. |
| **MCP Extensibility** | Extend Orion's 22 built-in tools with any local [Model Context Protocol](https://modelcontextprotocol.io) server via `.orion/mcp.json` or the built-in **MCP Servers** UI panel. |
| **Multimodal Inbox & Attachments** | Attach files directly in chat (`📎`) or drop text/code, images (`png`/`jpg`/`webp`), and documents (`pdf`/`docx`/`xlsx`) into `.orion/inbox/` for automatic extraction, size capping, and archiving. |
| **Least-Privilege DB Loans** | Temporary, revocable external database borrowing (such as read-only or disposable Supabase environments) with startup warnings for over-privileged service keys ([docs/SUPABASE_LOAN.md](docs/SUPABASE_LOAN.md)). |
| **Defense-in-Depth Safety** | Loopback-only (`127.0.0.1`) binding, shared-secret authentication, closed CORS, path-traversal guards, destructive git command blocklist, automatic `.bak` snapshots before overwrites, and confirmation modals for shell commands. |

---

## Architecture

```text
┌─────────────────────────── VS Code ───────────────────────────┐
│  Chat Webview  ◄──── postMessage ────►  Extension Host (TS)   │
│       ▲                                         │             │
│       │ status / token / ask_user / done        │ fetch (SSE) │
└───────┼─────────────────────────────────────────┼─────────────┘
        │                                         ▼
        │                          ┌────────────────────────────┐
        │                          │  FastAPI Backend (Python)  │
        │                          │  • /chat & /set-project    │
        │                          │  • Agent Loop & 22 Tools   │
        │                          │  • Semantic Index & Cache  │
        │                          │  • Sub-Agents & MCP Client │
        │                          └──────────────┬─────────────┘
        │                                         ▼
        │                     Gemini / Claude / OpenRouter / Cloudflare
        └──── active file, selection & Problems diagnostics ────┘
```

See [SECURITY.md](SECURITY.md) for the complete threat model and explicit security boundaries.

---

## Requirements

- **Python 3.11+** (automatically checked/installed by the Windows quick installer)
- **VS Code 1.125+**
- **Node.js 18+** *(optional — required only for JS/TS `verify_code` checks or building the extension from source)*
- **At least one LLM API key** (free Gemini keys work out of the box; OpenRouter, Anthropic, DeepSeek, Qwen, Groq, or Cloudflare optional)

---

## Quick Start

### Option 1: One-Line Installer (Windows)

1. Install **Orion — AI Software Engineer** (`grenitho.orion-ai`) from the VS Code Marketplace.
2. Run the setup script in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm [https://raw.githubusercontent.com/grenitho/orion/main/install.ps1](https://raw.githubusercontent.com/grenitho/orion/main/install.ps1) | iex"
```

The script sets up the Python backend, installs dependencies, configures your API keys, and displays the final `orion.backendPath` setting to paste into VS Code. For detailed walkthroughs and multi-provider setups, see [docs/PANDUAN_SETUP.md](docs/PANDUAN_SETUP.md).

### Option 2: Manual Setup (Windows / macOS / Linux)

#### 1. Start the Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Add your API keys
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 2. Install or Build the Extension

Install `grenitho.orion-ai` from the VS Code Marketplace, or build from source:

```bash
cd ../orion
npm install
npm run package        # Type-check, lint, and bundle
npx vsce package       # Generates orion-ai-<version>.vsix
code --install-extension orion-ai-<version>.vsix --force
```

*(For extension development, open `orion/` in VS Code and press **F5** to launch an Extension Development Host.)*

### Your First Task

Click the **Orion** icon in the VS Code Activity Bar, select your project folder, and try a multi-step prompt:

> *"Add a dark-mode toggle, write unit tests for it, and update the documentation."*

Orion will create a structured todo list, implement the changes, verify syntax and tests, request confirmation before any shell commands, and create a clean git checkpoint once verified.

---

## Configuration

### Environment Variables (`backend/.env`)

| Variable | Description |
| :--- | :--- |
| `GEMINI_API_KEY_1`, `_2`, ... | One or more Gemini API keys (automatically rotated per request). |
| `DEFAULT_MODEL` | Primary model identifier (e.g., `gemini/gemini-2.5-flash` or `gemini/gemini-3.5-flash-lite`). |
| `FALLBACK_API_KEY`, `FALLBACK_BASE_URL`, `FALLBACK_MODELS` | OpenAI-compatible fallback tier (OpenRouter, Anthropic/Claude, DeepSeek, Qwen, Groq). |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_MODELS` | Optional third-tier Cloudflare Workers AI fallback. |
| `LLM_REASONING_EFFORT` | Thinking/reasoning depth: `minimal`, `low`, `medium`, or `high` (default: `high`). |
| `LLM_TIMEOUT`, `LLM_MAX_RETRIES_PER_MODEL`, `LLM_BACKOFF_BASE` | Request timeout and exponential retry controls. |
| `ORION_MAX_TODO_NUDGE`, `ORION_MAX_VERIFIKASI` | Maximum continuation and claim-verification challenge loops. |

### Per-Project Settings (`.orion/`)

All project-specific state lives in `<your-project>/.orion/` (gitignored by default and safe to delete to reset project memory):

- **`.orion/orion_config.json`** — Iteration limits, token budget thresholds, sub-agent budgets (`subagents_*`), per-role model overrides (`subagent_models`), `mcp_enabled`, and inbox toggles.
- **`.orion/mcp.json`** — Local MCP server definitions (manageable visually via the **MCP Servers** panel or **Setup Wizard**; see [docs/MCP_FOUNDATION.md](docs/MCP_FOUNDATION.md)).
- **`.orion/inbox/`** — Drop-zone folder for files to be ingested on the next chat turn ([docs/INBOX_AND_PROFILES.md](docs/INBOX_AND_PROFILES.md)).

---

## Project Layout

```text
orion-project/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI endpoints, SSE contract & runtime setup
│   │   ├── logging_setup.py         # Rotating structured file logs
│   │   └── services/
│   │       ├── agent_loop.py        # Core agentic loop, todo nudges & claim gating
│   │       ├── llm_router.py        # Multi-tier provider routing, backoff & fallbacks
│   │       ├── prompt_cache.py      # Claude ephemeral prompt caching & token extraction
│   │       ├── tool_executor.py     # 22 built-in tools, path/git guards & backups
│   │       ├── code_verifier.py     # Multi-language static syntax & compiler checks
│   │       ├── semantic_search.py   # Incremental local semantic code search & auto-index
│   │       ├── context_manager.py   # Persistent session history & safe token pruning
│   │       ├── findings_ledger.py   # Cross-pruning sub-agent & investigation ledger
│   │       ├── mcp_manager.py       # Model Context Protocol client & tool registry
│   │       ├── subagent_manager.py  # Explorer, Verifier & parallel Worker orchestration
│   │       ├── inbox_manager.py     # Multimodal attachment & inbox extraction pipeline
│   │       └── ...                  # Guard rules, SSE helpers, telemetry & permissions
│   ├── tests/                       # 684 automated pytest tests & regression guards
│   └── examples/                    # Reference MCP server & config templates
├── orion/                           # VS Code extension (TypeScript + esbuild)
│   └── src/
│       ├── extension.ts             # Extension entry point & lifecycle management
│       ├── panels/                  # Chat, Activity, Setup Wizard, MCP, Config & Perf UI
│       └── services/                # Backend bridge, auth token & process manager
├── docs/                            # Setup guides, architecture docs & roadmap
├── scripts/                         # Standalone indexing & semantic search utilities
├── install.ps1                      # One-line Windows setup script
├── BLUEPRINT.md                     # System architecture specification
├── PROGRESS.md                      # Milestone & release history
└── SECURITY.md                      # Threat model & security policy
```

---

## Testing

Run the full backend test suite from the `backend/` directory:

```bash
cd backend
pytest
```

The suite includes **684 automated tests** covering the agentic loop, per-file edit ledger, static code verification, prompt caching, semantic search auto-indexing, sub-agent isolation, MCP hard blocks, SSE stream guarantees, and security posture checks.

---

## Troubleshooting

| Symptom | Resolution |
| :--- | :--- |
| **Backend silent / connection lost** | Inspect `.orion/logs/orion_stdout.log` and `backend.log` inside the backend installation root, and ensure port `8000` is available. |
| **Suspected duplicate or stale backend** | Query `GET /runtime/forensics` (token-protected) or check boot logs for `[ORION RUNTIME WARNING] TWIN SUSPECTED` / `STALE CODE`. |
| **`401 Unauthorized` in chat or dashboard** | Shared secret mismatch — restart the backend once, then run **Developer: Reload Window** in VS Code so both re-read `~/.orion_shared_secret`. |
| **`No module named 'mcp'`** | Activate the backend virtual environment and run `pip install -r requirements.txt`. |
| **MCP server fails to start on Windows** | Verify the executable path in `.orion/mcp.json`, and wrap `npx` commands with `cmd /c` (see [docs/MCP_FOUNDATION.md](docs/MCP_FOUNDATION.md)). |
| **File in `.orion/inbox/` not ingested** | Each file version is tracked in `.orion/inbox_state.json` and ingested once per turn; modify the file or add a new file to trigger ingestion. |

---

## Limitations

- **User-Privilege Execution** — Orion executes shell commands with your current OS user permissions (there is no OS-level container sandbox). Run it only on trusted projects, never as Administrator/`root`, and keep approval modals enabled.
- **Prompt Injection Risk** — Untrusted files or web pages may contain adversarial instructions. Orion's verification tools, git blocklists, and approval prompts mitigate—but do not eliminate—this risk.
- **Model Capability Dependency** — Smaller/faster models benefit heavily from Orion's verification scaffolding, while complex multi-file refactoring works best with stronger reasoning models.

---

## License

MIT — see [LICENSE](LICENSE).