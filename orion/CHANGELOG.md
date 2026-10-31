# Change Log

All notable changes to the Orion extension are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.5] - 2026-10-31

Companion backend: **0.10.22** (UPDATE-025 commits 16–18).

### Added
- **One-click "Install Playwright MCP" button in the MCP tool-servers
  panel** (UPDATE-025 commit 18): writes the official `@playwright/mcp`
  server into `<project>/.orion/mcp.json` through the same atomic
  `apply_config_op` transaction as the demo install — no hand-written
  JSON, no memorized config. Platform-aware (`cmd /c npx …` on Windows,
  plain `npx` elsewhere); re-clicking is a safe reinstall. Plugging the
  server arms the backend 0.10.21 computed-style verification tooth:
  UI-changing turns then owe rendered-style evidence (getComputedStyle
  via browser_evaluate) or an honest UNVERIFIED disclosure.
- **Router section in the Setup Wizard (step 4)**: an explicit
  "Disable Gemini tier" checkbox (writes `GEMINI_DISABLED=true`; no more
  gutting the primary-model field to silence the Gemini keys) and a
  "Last-resort fallback" dropdown (`none` / `cloudflare` / any configured
  `provider_N`, writes `ROUTER_FALLBACK`). Both mirror the live `.env`
  state when the wizard opens, and unknown values degrade to `none`.

### Changed
- Model router try-order (backend 0.10.22): **Cloudflare now runs BEFORE
  the legacy FALLBACK tier**, so configured Cloudflare accounts actually
  receive traffic instead of sitting behind the glm fallback;
  `ROUTER_FALLBACK` can still demote one group to the very end, labelled
  "(Last Resort)" in logs.

## [0.4.1] - 2026-10-26

### Added
- **Per-bubble Copy button in the chat panel** (companion backend 0.9.4):
  every message bubble now carries a small `Copy` button that copies the
  RAW pre-markdown text of that message — no more screenshots as a
  substitute for copying. Streaming re-renders only the bubble body, so
  the button never flickers away mid-answer; `navigator.clipboard` is
  used when available with a `document.execCommand('copy')` fallback, and
  message text is explicitly selectable (`user-select: text`). The button
  is a secondary action with an explicit border fallback, per the UI rule
  that themes may render secondary backgrounds transparent.

## [0.4.0] - 2026-10-25

### Added
- **Documents through the 📎 button** (companion backend 0.9.3): pdf, docx
  and xlsx can now be attached to a message; the backend extracts them
  with the same engine as the inbox drop-zone (pypdf / python-docx /
  openpyxl, 20 MB cap, announced page/table/row truncations) and injects
  the text for that turn only. Images and plain text unchanged.
- **Setup Wizard: "Connect existing backend…"** — the fix for the
  multi-folder daily driver: opening VS Code outside the orion repo no
  longer forces a re-download; one dialog validates an existing backend
  folder (repo root or backend folder), writes the GLOBAL
  `orion.backendPath`, restarts the backend and reports readiness.

## [0.3.3] - 2026-10-25

### Changed
- **The chat webview runtime is no longer an inline script.** VS Code
  delivers webview HTML through `document.write()`; on VS Code 1.143 the
  giant inline chat script died on that path with a parse error (owner
  machine, incident 2026-10-25) while every local engine accepted it. The
  runtime now ships as a separate bundled file (`dist/chatwebview.js`)
  loaded through `asWebviewUri` behind a nonce CSP (the pattern the MCP
  panel and wizard already follow in spirit); an external script parses
  from its own file URL and is immune to write-chunking and re-encoding.
  The ASCII-only parse-error beacon from 0.3.2 stays as the permanent
  tripwire.

## [0.3.2] - 2026-10-25

### Added
- **Parse-error beacon in the chat webview**: a separate ASCII-only script
  tag (first thing in `<head>`) reports ANY load/parse error of the webview
  document to the Extension Host log as `[ORION] chat webview load error:
  … @line L:C`. Webview page errors are invisible in the workbench DevTools
  console — this closes the blind spot that made the 2026-10-25 silent
  death take three rounds to diagnose.
- Hardening: the chat inline script is now ASCII-only (raw astral emoji
  replaced with explicit escapes), removing a whole class of
  encoding/transport oddities from the parse path.

## [0.3.1] - 2026-10-25

### Fixed
- **Chat panel could die silently when a CDN request hung.** The webview
  loaded marked.js / highlight.js (and a stylesheet) from CDNs in `<head>`,
  BEFORE its inline script: a hanging request (offline, proxy, blackholed
  DNS) blocked the HTML parser, so Send, 📎 and every binding never
  existed — with no console error at all (real incident, 2026-10-25). The
  CDN assets now load AFTER the inline script and every use is guarded
  (escaped-pre markdown fallback, no-op highlighting), so the panel always
  boots and upgrades itself when the CDN arrives.
- New boot beacon: the inline script posts `webviewBoot` at load and the
  extension host logs `[ORION] chat webview inline script booted` — the
  missing diagnostic signal from the incident above.

## [0.3.0] - 2026-10-23

### Added
- **Attachments are real now**: a 📎 button in the chat panel opens a file
  dialog (images png/jpg/webp + text/code), renders removable chips above
  the input, and sends them with the NEXT message only — the backend
  pipeline existed since the attachment feature but had no UI (companion
  backend applies the same caps: 7 MB/image, 512 KB/text, 4 images).
- **MCP master-switch button** in the MCP Servers panel ("MCP ON/OFF —
  master switch"): one `/mcp/config` transaction writes `mcp_enabled` into
  `orion_config.json` and applies it live (off stops every server,
  `mcp.json` preserved; on restarts them). Companion backend 0.9.2.
- **Stale-backend watchdog toast**: a backend that goes stale MID-session
  (git pull, extension update) now toasts once per backend boot with
  Restart/Keep actions — previously only detected at VS Code start.

### Changed
- Setup Wizard step 4 states explicitly that empty fields keep stored keys
  and that Gemini may be skipped in favour of provider groups; saving with
  zero credentials warns loudly instead of a cheerful "Saved 0 key(s)".
- `install.ps1`: skipping Gemini is a first-class path (offers one
  OpenAI-compatible provider group right there); all user-facing output is
  now English (Indonesian walkthrough stays in `docs/PANDUAN_SETUP.md`).

## [0.2.0] - 2026-10-23

### Added
- **Setup Wizard: optional “Sub-agent models” card** (companion backend
  0.9.0). Pin one model per internal sub-agent role — Explorer, Verifier,
  Workers — with dropdowns fed by the new token-guarded
  `GET /models/options` (credential groups and model names derived from the
  backend environment; key values never cross the wire), a per-role **Test**
  button that runs a real one-shot probe (`POST /models/test`), and
  **Save profiles** which writes `subagent_models` atomically through
  `POST /models/config`. *Main router (default)* keeps the pre-018
  behaviour; a saved profile whose group vanished from the environment
  stays visible (marked *not in current env*) and the backend falls back to
  the main router with a warning — a broken profile can never break a
  sub-run.
- Companion backend 0.9.0 also ships the **inbox drop-zone**: files dropped
  into `<project>/.orion/inbox/` (text/code, png/jpg/webp, pdf, docx, xlsx)
  are ingested on the next chat turn, extracted under hard caps, archived
  (never deleted) and announced with a `📥 Inbox: …` activity line — no
  extension UI required. See `docs/INBOX_AND_PROFILES.md`.

## [0.1.1] - 2026-10-23

### Changed
- No extension changes (backend-only release, backend 0.8.1): delegation
  policy in the system prompt, claim-free wording in sub-agent role
  prompts, and the per-project `mcp_enabled` master switch. The
  Marketplace listing stays at 0.1.0.

## [0.1.0] - 2026-10-22

### Added
- **Sub-agents** (companion backend 0.8.0): Orion delegates bounded work to
  internal sub-agents — Explorer (read-only survey), Verifier (real-tool
  post-edit critic) and parallel Workers with disjoint write scopes — with
  per-turn budgets in `.orion/orion_config.json` (`subagents_*`) and
  `🤖 Sub-agent …` activity lines around every delegation. First public
  Marketplace release.

## [0.0.10] - 2026-10-21

### Added
- **Backend identity check**: the extension no longer treats *any* process
  answering port 8000 as a ready backend. The companion backend (0.5.0)
  exposes a token-guarded `GET /health` (app name, version, PID, boot time,
  backend folder, active project) and writes a PID file
  (`.orion/orion_backend.pid`); at startup Orion verifies the listener is
  **this installation's, fresh** backend before trusting it.
- **Restart offer for stale/orphaned backends**: when the backend on port
  8000 belongs to a different installation, does not match the PID file, or
  was started *before* the backend code was last modified (typical right
  after an upgrade), a dialog offers **Restart backend now** or
  **Keep using it**.
- **Port-conflict triage**: a foreign (non-Orion) application on port 8000
  now gets a clear error asking you to close it, instead of a silent 20-second
  timeout; when a freshly spawned backend exits early, the message tells the
  port-grab case apart from a startup crash (and points at
  `.orion/logs/orion_stdout.log`).
- A **relative `orion.backendPath`** setting warns once per session with an
  **Open Settings** button — relative paths resolve against an unpredictable
  working directory and usually break auto-start.

### Changed
- Companion backend: `orion_stdout.log` **rotates at 5 MB** (2 backups kept)
  instead of growing unbounded; an oversized legacy log is reclaimed on the
  first start. Backend version bumped 0.4.1 → 0.5.0, then → 0.5.1 with the
  hardening below.
- **Hardening (backend 0.5.1)**: the project scanner now ignores dependency
  folders by PATTERN (any name starting with `venv`/`.venv`, or containing
  `node_modules`/`__pycache__`) instead of exact names only — a renamed
  virtualenv (`venv-lama-…`) used to be scanned in full — and hard-caps the
  dependency map at 20,000 files, with a loud truncation notice inside the
  project map and in the `/set-project` warnings. Real-world trigger: a
  45 MB map injecting ~560k tokens into every chat.

### Fixed
- Chatting **without a project set** no longer layers a false "Connection to
  the backend was lost" alarm on top of the *Please set a project first.*
  error — that stream now ends with the proper completion signal like every
  other path.
- An LLM answer with an **empty `choices` list** no longer crashes the stream
  with a bare `IndexError`; the chat shows a clear, actionable error instead.
- Disconnecting (closing the chat / VS Code) while an LLM call is in flight
  no longer leaves the call running **orphaned in the background** — it is
  cancelled, so no quota is burned on an answer nobody will read.
- **Runaway project map (backend 0.5.1)**: a large folder whose name is not
  exactly on the ignore list (e.g. a renamed virtualenv) could inflate the
  dependency map to tens of megabytes and every chat to hundreds of
  thousands of tokens. The scanner now ignores dependency folders by
  pattern and caps the map at 20,000 files with an explicit truncation
  notice; `GET /health` additionally reports the running Python
  executable/prefix so a wrong-stack backend is diagnosable.

## [0.0.9] - 2026-10-21

### Added
- **Setup Wizard** (`Ctrl+Shift+P` → *Orion: Setup Wizard (Install Backend)*):
  a five-step installer inside VS Code, without a terminal — Python
  detection (one-click winget install on Windows), backend download from
  GitHub (zip, no git needed, an existing `.env` is always preserved,
  `orion.backendPath` set automatically), `.venv` creation + dependency
  install with live progress and an import smoke test, an API-key form
  (multiple Gemini keys + optional provider groups for
  OpenRouter/OpenAI/Anthropic/DeepSeek/Qwen/Groq; stored keys are shown
  masked and kept when a field is left empty), backend restart and
  one-click chat. Every backend setup-error dialog now offers
  **Run Setup Wizard** as its first button.

### Changed
- The companion backend writes its stdout log to
  `<project>/.orion/logs/orion_stdout.log` instead of `~/orion_stdout.log`,
  so both logs live side by side inside the project and several projects
  no longer share one file (falls back to the old home location when the
  project folder is not writable; `ORION_STDOUT_LOG` overrides).
- Walkthrough step 1 now points at the Setup Wizard first and the
  one-line PowerShell installer second.

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
