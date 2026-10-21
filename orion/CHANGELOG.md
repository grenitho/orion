# Change Log

All notable changes to the Orion extension are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.0.8] - 2026-10-20

### Added
- **"Get Started with Orion" walkthrough** (Help → Welcome → walkthroughs):
  four tracked steps from installing the backend to the first chat task,
  with an optional step for extra API keys and smarter models.
- New commands: **Orion: Start Backend** (manual retry of the auto-start)
  and **Orion: Open Setup Guide**.
- New setting: **`orion.backendPath`** — point the extension at a backend
  folder anywhere on disk; no more editing extension source.
- One-line Windows installer **`install.ps1`** (repo root) and the full
  setup guide **`docs/PANDUAN_SETUP.md`**: Python check/install, backend
  download without git, venv + dependencies, interactive multi-key
  `.env` creation, smoke test, and the final VS Code steps.
- `.env.example` now documents the zero-code multi-provider options:
  OpenRouter (one key → OpenAI/Anthropic/DeepSeek/Qwen/…) or any direct
  OpenAI-compatible endpoint, plus how to run a non-Gemini model as the
  effective primary.

### Changed
- Marketplace readme rewritten for first-time users: free 3-step quick
  start first, developer builds last, Node.js clarified as optional.
- Setup errors are actionable: dialogs explain what to do and offer
  **Open Setup Guide / Open Settings / Retry** buttons instead of naming
  source files.
- The chat panel translates backend-unreachable failures into concrete
  next steps instead of showing the raw `fetch failed` driver error.

### Fixed
- Auto-start required a venv folder named exactly `venv` while the docs
  said `.venv` — every doc-following install failed. Both names now work
  (`backendManager` and `start-backend.bat`).
- `mcp` (imported by newer litellm for tool calls) is now declared in
  `backend/requirements.txt`; the first-chat `No module named 'mcp'`
  trap is gone for new installs.
- A failing pre-flight `/set-project` call no longer aborts the chat
  request before `/chat` runs.

## [0.0.7] - 2026-10-19

### Changed
- Extension renamed from `orion` to `orion-ai` (ID `grenitho.orion-ai`):
  VS Code Marketplace extension names are globally unique and `orion` was
  already taken by another publisher. Display name, icon and behaviour are
  unchanged. Uninstall the old `grenitho.orion` before installing this
  version so two copies never run backends side by side.

## [0.0.6] - 2026-10-19

### Added
- Shared-secret token between extension and backend: minted once into
  `~/.orion_shared_secret` (override: `ORION_SHARED_TOKEN` on both sides),
  attached by the extension as `X-Orion-Token`; every backend route except
  the health probe now answers 401 without it. Closes the last outstanding
  SECURITY.md hardening promise.

## [0.0.5] - 2026-10-19

### Added
- Full installation & first-run guide in this readme, plus a troubleshooting
  table.
- Marketplace metadata: publisher, repository, license, keywords, categories
  and gallery banner; `LICENSE` now ships inside the VSIX.

### Changed
- The entire user interface is English: chat webview, activity view,
  performance dashboard, notifications, backend status stream and tool
  result messages. **Chat replies still follow the language you write in** —
  that is deliberate and now guaranteed by an explicit rule in the system
  prompt.
- System prompt and all 22 tool descriptions translated to English; claim
  detection keywords intentionally stay bilingual (Indonesian + English) so
  honesty guards keep working for Indonesian conversations.

### Internal (no behaviour change)
- Phase C refactor completed: SSE frame builders (`sse.py`), per-turn state
  (`session_state.py`), end-of-iteration decision (`loop_guards.py`) and the
  agent loop itself (`agent_loop.py`) extracted from `main.py`
  (2,769 → 569 lines). Verified by byte-identical SSE baseline recordings
  and 97 automated tests.

## [0.0.1 – 0.0.4]

- Agentic loop with todo tracking, terminal approval modal, post-edit
  verification, 3-tier LLM router with heartbeat, project intelligence
  (`.orion/` context + dependency map), performance dashboard, cancel
  button, image & text attachments. Build history lives in `docs/`.
