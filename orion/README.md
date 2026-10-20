# Orion — AI Software Engineer for VS Code

Give Orion a task in your own language — "add a dark-mode toggle, test it, and
update the README" — and it plans the work into a todo list, reads your
project, edits code, runs tests and terminal commands **with your approval**,
verifies its own work with real parsers/compilers, and reports honestly what
changed and what did not. Orion replies in the language you write in.

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
- **Resilient by design** — 3-tier LLM router (rotated Gemini keys →
  OpenRouter → Cloudflare), heartbeat status while the model thinks, and every
  run ends with exactly one terminal event so the UI never hangs or fakes
  success.
- **Attachments** — paste screenshots or drop image/text files into the chat.
- **Performance dashboard** — requests, tokens, estimated cost and latency.
- **Project memory** — per-project `.orion/` folder with context docs,
  dependency map, todo list, backups and logs. Local only, gitignored, never
  packaged.

## Requirements

- Python **3.11+** and Node.js **18+**
- VS Code **1.125+**
- At least one LLM provider key (Gemini recommended; OpenRouter/Cloudflare
  optional as fallback tiers)

## Install

### 1. Extension

From a release build (recommended once published):

```bash
code --install-extension orion-ai-<version>.vsix --force
```

From source:

```bash
cd orion
npm install
npm run package                    # type-check + lint + production bundle
npx vsce package                   # produces orion-ai-<version>.vsix
code --install-extension orion-ai-<version>.vsix --force
```

### 2. Backend

```bash
cd backend
python -m venv .venv               # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then add at least GEMINI_API_KEY_1
```

The extension **auto-starts the backend** when VS Code opens a workspace that
contains (or sits near) this repository's `backend/` folder. To run it
manually instead:

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 3. First run

1. Open **your project's folder** in VS Code.
2. Click the **Orion** icon in the activity bar → *Click to open the Chat
   Panel* (or `Ctrl+Shift+P` → *Orion: Open Chat Panel*).
3. Send a task. Watch the **Activity view** — it is Orion's live log
   (*Thinking…*, *Reading folder structure…*, *Running test suite…*).
4. Approve or reject terminal commands when the modal appears; multi-item
   tasks show a todo checklist box in the chat.

## Where your data lives

Everything Orion remembers about a project lives in that project's `.orion/`
folder (session history, backups, logs, todos, dependency map). It is
gitignored by design and never shipped; deleting it resets Orion's memory for
that project without touching your code.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Chat reports a lost connection / no reply | Is the backend running? Check port 8000 is free; read `<project>\.orion\logs\backend.log` and `%USERPROFILE%\orion_stdout.log` |
| `No module named 'mcp'` on first chat | Newer `litellm` needs it for tool calls: `pip install mcp` inside the backend venv |
| Port 8000 already in use | Kill the stale backend (close other VS Code windows running Orion, or stop the old uvicorn process) |
| Extension shows old UI after an update | Fully restart VS Code and close old chat tabs |
| `401 unauthorized` from chat or dashboard | Restart the backend once and reload the VS Code window; both re-read `~/.orion_shared_secret` |
| Backend never auto-starts | Ensure the opened workspace contains or is near this repo's `backend/` folder, or start it manually (see Install §2) |
| `StarletteDeprecationWarning: ... install httpx2 instead` during pytest | starlette ≥ 1.9 serves `starlette.testclient` with httpx2; without it the suite still passes but prints one deprecation warning. Fix: `pip install -r backend/requirements-dev.txt`. Test-only — backend runtime is unaffected |
| `npm warn allow-scripts esbuild ...` during `npm install` | Benign: esbuild's official postinstall only selects a prebuilt platform binary. Safe to ignore; optionally silence via `npm approve-scripts esbuild` |

## Security & privacy

The backend binds to loopback only, CORS is closed, risky terminals need your
approval, and destructive git is blocked in code. The approval modal is a
deliberate defense against prompt injection — **do not disable it**. Threat
model and honest limitations: see `SECURITY.md`.

## Development

Open `orion/` in VS Code and press **F5** for an Extension Development Host.
Backend tests: `pip install -r backend/requirements-dev.txt` once, then
`python -m pytest backend -q` (116 tests, zero warnings).
Architecture and roadmap: `docs/DEVELOPMENT_ROADMAP.md`; refactor history and
rules: `docs/FASE_C_PLAN.md`.

## License

MIT — see [LICENSE](LICENSE).
