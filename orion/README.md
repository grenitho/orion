# Orion — AI Software Engineer for VS Code

Give Orion a task in your own language — "add a dark-mode toggle, test it,
and update the README" — and it plans the work into a todo list, reads your
project, edits code, runs tests and terminal commands **with your approval**,
verifies its own work with real parsers/compilers, and reports honestly what
changed and what did not. Orion replies in the language you write in.

Orion has two parts: **this extension** (the interface) and a small **local
Python backend** (the agent's brain) that talks to it over `127.0.0.1`.
Your code and API keys never leave your machine.

## Quick start — free, ~10 minutes, 3 steps

**1. Install this extension** (you probably just did).

**2. Install the backend — one line.** Open PowerShell (no admin needed)
and run:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
```

It checks/installs Python, downloads Orion, installs dependencies, and asks
you to paste your **free** Gemini API keys — get them at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey). You can
enter several keys (multiple Google accounts work); Orion rotates them and
hops to the next key when one hits its rate limit.

**3. Point VS Code at the backend.** Settings (`Ctrl+,`) → search
`orion.backendPath` → fill in `C:\Users\<you>\orion\backend` → reload the
window. Click the **Orion** icon in the activity bar, follow the
*Get Started with Orion* walkthrough, and send your first task.

macOS/Linux, manual install, no-Python machines, and every common error:
**[docs/PANDUAN_SETUP.md](https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md)**
(a step-by-step guide, written in Indonesian; the repository
[README.md](https://github.com/grenitho/orion/blob/main/README.md) covers
the same ground in English).

## Requirements

- **VS Code 1.125+**
- **Python 3.11+** — the one-line installer finds or installs it for you
- **Node.js is optional** — only needed to verify JS/TS code
  (`verify_code` uses `node --check`) or to build this extension from source
- At least one LLM API key — free Gemini keys are enough to start

## Smarter models: OpenAI, Anthropic, DeepSeek, Qwen, …

Orion routes through [LiteLLM](https://docs.litellm.ai/), so you are not
locked to Gemini. When you outgrow the free tier, plug in stronger models
by editing `backend/.env` — **no code changes, no rebuild**:

```ini
# One OpenRouter key unlocks OpenAI / Anthropic / DeepSeek / Qwen / etc.
FALLBACK_API_KEY=sk-or-...
FALLBACK_MODELS=openai/gpt-5, anthropic/claude-sonnet-4-5, deepseek/deepseek-reasoner
```

Direct endpoints (OpenAI, DeepSeek, Qwen/DashScope, Groq) work the same
way via `FALLBACK_BASE_URL`; you can even run a non-Gemini model as your
primary by leaving the Gemini keys empty. Full recipes and honest caveats:
the **LLM** section of
[PANDUAN_SETUP.md](https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md).
Restart the backend after editing `.env`.

## Features

- **Agentic loop with an explicit todo list** — multi-item requests become a
  visible checklist; the loop keeps going until every item is done or asks you
  before stopping.
- **Real verification, not vibes** — `verify_code` checks 16 file kinds with
  actual parsers/compilers (py, js/ts, html, json, yaml, css, …), `run_tests`
  returns parsed pass/fail reports, and VS Code's own diagnostics, your active
  file and your selection are sent with every request.
- **Safety by code, not by promise** — dangerous terminal commands require an
  approval modal; destructive/networked git (`init/push/pull/clone/reset`) is
  blocked at code level; path-traversal guard; sensitive files (.env, keys)
  are never auto-overwritten; every overwrite keeps a `.bak` backup.
- **Resilient by design** — multi-tier LLM router (rotated Gemini keys →
  first-class `PROVIDER_<N>` groups → legacy OpenAI-compatible fallback →
  Cloudflare), heartbeat status while the model thinks, and every run ends
  with exactly one terminal event so the UI never hangs or fakes success.
- **Inbox drop-zone & attachments** — put files (text/code, png/jpg/webp
  screenshots, PDF, DOCX, XLSX) into `<project>/.orion/inbox/` and the next
  chat turn picks them up, or use the 📎 button in the chat panel to attach
  images, text/code or PDF/DOCX/XLSX (extracted server-side) to ONE message; documents are extracted under announced caps, and
  every processed file is archived — never deleted.
- **MCP tool servers** — lend Orion tools from any local Model Context
  Protocol server, managed from a dedicated panel (add/edit/disable,
  one-click demo) with a per-project master switch button.
- **Sub-agents with model profiles** — bounded internal Explorer /
  Verifier / parallel Workers take exploration-heavy and verification
  work off the main context; an optional Setup-Wizard card pins one model
  per role from the credentials you already configured, with a real Test
  probe and automatic fallback to the main router.
- **Performance dashboard** — requests, tokens, estimated cost and latency.
- **Project memory** — per-project `.orion/` folder with context docs,
  dependency map, todo list, backups and logs. Local only, gitignored, never
  packaged.

## Manual install (developers)

```bash
git clone https://github.com/grenitho/orion.git
cd orion/backend
python -m venv .venv               # both .venv and venv are detected
pip install -r requirements.txt
cp .env.example .env               # then add at least GEMINI_API_KEY_1
```

The extension **auto-starts the backend** when VS Code opens a workspace
that contains (or sits near) the repository's `backend/` folder, or when
`orion.backendPath` points at it. To run the backend by hand:

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

To build the extension from source: `cd orion && npm install &&
npx @vscode/vsce package` → `code --install-extension orion-ai-<version>.vsix --force`.

## First run

1. Open **your project's folder** in VS Code.
2. Click the **Orion** icon in the activity bar (or `Ctrl+Shift+P` →
   *Orion: Open Chat Panel*).
3. Send a task. Watch the **Activity view** — it is Orion's live log
   (*Thinking…*, *Reading folder structure…*, *Running test suite…*).
4. Approve or reject terminal commands when the modal appears; multi-item
   tasks show a todo checklist box in the chat.

Useful commands: **Orion: Start Backend** (retry the auto-start),
**Orion: Open Setup Guide**, **Orion: Show Performance Dashboard**.

## Where your data lives

Everything Orion remembers about a project lives in that project's `.orion/`
folder (session history, backups, logs, todos, dependency map). It is
gitignored by design and never shipped; deleting it resets Orion's memory for
that project without touching your code.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Chat says *Cannot reach the Orion backend* | `Ctrl+Shift+P` → **Orion: Start Backend**; if that reports *backend folder not found*, set `orion.backendPath` in Settings |
| Backend never auto-starts | Set `orion.backendPath` to your `backend` folder, or open a workspace near it; see the setup guide |
| `No module named 'mcp'` on first chat | Your venv predates 0.0.8: `pip install -r requirements.txt` inside the backend venv (mcp is now declared) |
| Port 8000 already in use | Kill the stale backend (close other VS Code windows running Orion, or stop the old uvicorn process) |
| Extension shows old UI after an update | Fully restart VS Code and close old chat tabs |
| `401 unauthorized` from chat or dashboard | Restart the backend once and reload the VS Code window; both re-read `~/.orion_shared_secret` |
| Chat reports a lost connection mid-task | Read `<project>\.orion\logs\backend.log` and `<project>\.orion\logs\orion_stdout.log` (pre-0.0.9: `%USERPROFILE%\orion_stdout.log`) |
| `StarletteDeprecationWarning: ... install httpx2 instead` during pytest | Test-only: `pip install -r backend/requirements-dev.txt`; backend runtime is unaffected |
| `npm warn allow-scripts esbuild ...` during `npm install` | Benign: esbuild's postinstall only selects a prebuilt platform binary |

## Security & privacy

The backend binds to loopback only, CORS is closed, risky terminals need your
approval, and destructive git is blocked in code. The approval modal is a
deliberate defense against prompt injection — **do not disable it**. Threat
model and honest limitations: see
[SECURITY.md](https://github.com/grenitho/orion/blob/main/SECURITY.md).

## Development

Open `orion/` in VS Code and press **F5** for an Extension Development Host.
Backend tests: `pip install -r backend/requirements-dev.txt` once, then
`python -m pytest backend -q` (116 tests, zero warnings).
Architecture and roadmap:
[docs/DEVELOPMENT_ROADMAP.md](https://github.com/grenitho/orion/blob/main/docs/DEVELOPMENT_ROADMAP.md).

## License

MIT — see [LICENSE](https://github.com/grenitho/orion/blob/main/LICENSE).
