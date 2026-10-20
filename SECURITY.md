# ORION SECURITY

For those who run Orion on their own machine, and for contributors reading
the code.

## The threat model we acknowledge openly

Orion is an agent that **runs shell commands and writes files** on the machine
it is installed on. That is the source of its power and of its risk. These are
the boundaries:

| Layer | State |
|---|---|
| Network bind | Backend binds **explicitly** to `127.0.0.1:8000`. Unreachable from other machines |
| CORS | **Closed** (`allow_origins=[]`, only the `vscode-webview://` regex). A malicious web page cannot call the backend from a browser |
| Extension ↔ backend auth | **Shared-secret token** (`X-Orion-Token` header). Minted once into `~/.orion_shared_secret` (or `ORION_SHARED_TOKEN` env override on both sides); every route except the health probe `GET /` returns 401 without it. The extension attaches it automatically |
| Shell commands | `run_terminal` filters dangerous patterns + a VS Code confirmation modal; `git init/push/pull/clone/reset/rebase` are **blocked at code level** |
| Paths | All file operations are guarded to stay inside the project folder (path traversal rejected) |
| Web fetch | SSRF protection: localhost, private IPs, loopback, link-local and metadata addresses are rejected |
| Backups | Every overwrite/delete leaves a copy in `.orion/backups/` |

## What we do NOT claim

- **No process sandbox.** Approved commands run with the rights of the user
  running Orion. Never run Orion as administrator/root.
- **The shared token is a same-user boundary, not a fortress.** A malicious
  process running under the same user account can read the token file. The
  token closes the "any local process can call the endpoints" hole; the
  confirmation modal remains the primary defense against a manipulated model.
- **The LLM can be persuaded** (prompt injection) through file or web content
  it reads. The confirmation modal is the main defense: **do not disable it**.

## Data that must never enter the repo / VSIX

`.orion/` (conversation history, performance logs, todos), old `*.vsix`
files, `.env`, `__pycache__/`, `node_modules/`, `dist/`. All are in
`.gitignore` and `.vscodeignore`, and `test_security_posture.py` rejects
configuration regressions. The shared secret itself lives outside the repo,
in the user's home directory.

## If you find a hole

Report it via an issue without including an active payload, or contact the
maintainer privately when the hole is directly exploitable.
