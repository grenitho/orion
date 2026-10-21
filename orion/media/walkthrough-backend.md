# Orion = extension + backend

The VS Code extension you just installed is the **interface**. The agent
itself (planning, code editing, verification, LLM calls) is a small
**Python backend** that runs locally on `127.0.0.1:8000`.

Installing the backend is a **one-time** job — pick ONE route:

## Setup Wizard (easiest, any OS)

Press `Ctrl+Shift+P` → **Orion: Setup Wizard (Install Backend)**.

The wizard walks you through five steps inside VS Code — no terminal:
detect/install Python, download the backend, install dependencies,
paste your **free** Gemini API keys (get them at
<https://aistudio.google.com/apikey> — you can add several; Orion
rotates them automatically), then start the backend and open the chat.

## Windows terminal (one line)

Open PowerShell (no administrator needed) and run one line:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/grenitho/orion/main/install.ps1 | iex"
```

The installer checks/installs Python, downloads Orion, creates the virtual
environment, installs dependencies, and asks you to paste your **free**
Gemini API keys (get them at <https://aistudio.google.com/apikey> — you can
add several; Orion rotates them automatically).

## Any OS (manual)

See the full guide:
[docs/PANDUAN_SETUP.md](https://github.com/grenitho/orion/blob/main/docs/PANDUAN_SETUP.md)

After installing, tell VS Code where the backend lives:
**Settings → search `orion.backendPath`** → paste the path to the `backend`
folder → reload the window.
