# Orion MCP Foundation (UPDATE-014)

Status: implemented in backend 0.6.0 · extension unchanged (0.0.10) ·
no public release yet (the MCP phase closes with a later update; v0.1.0
is the milestone candidate).

Orion can now extend its 22 built-in tools with tools served by **MCP
(Model Context Protocol) servers** running on your machine. This document
describes what shipped, how to configure it, and how it behaves when
things go wrong.

---

## 1. What shipped

| Piece | Location | Role |
|---|---|---|
| MCP manager | `backend/app/services/mcp_manager.py` | The ONLY module touching the `mcp` SDK: config parsing, subprocess lifecycle, tool registry, call routing, failure formatting |
| Agent-loop branch | `backend/app/services/agent_loop.py` | One dynamic dispatch branch for `mcp__*` tools + schema merge into the provider request |
| Wiring | `backend/app/main.py` | `/set-project` starts the project's servers (additive `mcp` response field), `/health` gains `mcp_servers`/`mcp_tools`, lifespan shuts servers down |
| Demo server | `backend/examples/mcp_demo_server.py` | Dependency-free test server (`echo`, `add`, `fail`) for end-to-end verification |
| Tests | `backend/app/services/test_mcp_manager.py` | 31 unit tests (fakes only — no subprocess in pytest) |
| SSE harness | sandbox `baseline/` | Scenarios `mcp_ok`, `mcp_error`, `mcp_timeout` added; the nine pre-014 scenarios stayed **byte-identical** (`u014base` vs `u014final`) |

The seven frozen SSE event names are untouched: MCP activity is reported
through the existing `status` event (`🔌 MCP <server>/<tool> ...`).

**No configuration, no change.** Without `.orion/mcp.json` the backend
behaves exactly like 0.5.1 — the provider request is byte-identical and
the harness proves it.

## 2. Quick start (5 minutes, any OS)

1. Open the project you want Orion to work on.
2. Create the file `<project>/.orion/mcp.json` with this content, replacing
   the two `<...>` placeholders:

```json
{
  "mcpServers": {
    "demo": {
      "command": "<full path to the backend venv python>",
      "args": ["<full path to repo>/backend/examples/mcp_demo_server.py"]
    }
  }
}
```

   On a standard Windows install the python path is
   `<repo>\backend\venv\Scripts\python.exe` (macOS/Linux:
   `<repo>/backend/venv/bin/python`).
3. In VS Code: run **Orion: Set Project** again (or restart the backend).
   The `/set-project` response and the backend log now report:
   `[ORION MCP] server 'demo' ready with 3 tool(s)`.
4. Ask Orion in chat: *“Use the MCP echo tool to say hello.”* The activity
   log shows `🔌 MCP demo/echo ...`.
5. Optional error drill: ask it to call `mcp__demo__fail` — Orion receives
   `[MCP TOOL ERROR] ...` as the tool result, reports the failure, and the
   stream ends normally with `done`.

Community servers (e.g. `npx -y @modelcontextprotocol/server-fetch`) work
the same way. **Windows note:** `npx` is a `.cmd` shim that the MCP stdio
launcher cannot spawn directly; wrap it:

```json
{ "mcpServers": { "fetch": {
    "command": "cmd",
    "args": ["/c", "npx", "-y", "@modelcontextprotocol/server-fetch"] } } }
```

## 3. Configuration reference (`.orion/mcp.json`)

```json
{
  "mcpServers": {
    "<server name>": {
      "command": "python",              // REQUIRED, non-empty string
      "args": ["server.py"],            // optional list of strings (default [])
      "env":  { "KEY": "value" },       // optional; merged OVER a safe default
                                        // environment (PATH, HOME, ...)
      "timeoutSeconds": 30,             // optional, 1..600 (default 30)
      "disabled": false                 // optional; true = keep it configured
                                        // but do not spawn it
    }
  }
}
```

Parsing is deliberately forgiving: broken JSON, a non-object root, an entry
without `command`, a non-list `args`, a non-numeric `timeoutSeconds`,
unknown keys — none of these are ever fatal. Each problem produces a
warning in the `/set-project` response (`mcp.warnings`) and in the backend
log (`[ORION MCP WARNING] ...`); the affected entry is skipped or defaulted,
everything else keeps working.

`ORION_MCP_CONFIG=<path>` overrides the config location (tests, advanced
setups). Config is re-read on every `/set-project`; servers of the previous
project are stopped first.

**Master switch (backend 0.8.1+):** `"mcp_enabled": false` in the project's
`.orion/orion_config.json` turns the whole MCP layer off for that project
without deleting `mcp.json` — no servers spawn, `/mcp/status` reports
`"enabled": false` with an explanatory warning, and flipping the flag back
on (or removing it) restores everything on the next `/set-project` or
backend restart. Per-server `disabled` flags remain the fine-grained knob.
Since UPDATE-020 (backend 0.9.2) the flag no longer needs hand-editing or a
restart: the MCP panel's master-switch button posts one `/mcp/config` op
(`master_switch`) that writes `orion_config.json` atomically and applies
the change LIVE (off = stop every server now, on = start_all), and
§11's wizard/panel surface reflects the state from `/mcp/status`.

Security note: `mcp.json` grants command execution on your machine, exactly
like `backend/.env`. Only configure servers you trust; the file lives in the
project's `.orion/` folder, which Orion never packages or publishes.

## 4. How tools reach the model

* Each tool of each healthy server is exposed to the LLM under a
  namespaced name: **`mcp__<server>__<tool>`** (characters outside
  `[a-zA-Z0-9_-]` become `_`; total length capped at 64).
* Collisions — with the 22 built-in tools or between two servers — are
  **skipped with a warning**, never silently overwritten.
* The tool's JSON Schema (`inputSchema`) is passed through to the provider
  as the function `parameters`; the description is kept (capped at 1024
  chars). Schemas that are missing or not object-shaped fall back to an
  empty-object schema so the tool stays callable.
* The merged schema is built once per chat turn; the built-in 22 always
  come first.

## 5. Failure contract (what the model sees)

`MCPManager.call()` never raises and the SSE stream never crashes because
of MCP. Every failure becomes an ordinary tool-result string:

| Prefix | Trigger | Advice embedded for the model |
|---|---|---|
| `[MCP TOOL ERROR]` | the tool itself reported an error result (`isError`) | treat like any failed tool |
| `[MCP TIMEOUT]` | no answer within `timeoutSeconds` (SDK read-timeout `-32001` **or** the outer hard guard) | do not blindly retry; shrink the operation or continue without it |
| `[MCP SERVER DOWN]` | connection lost and the **one** transparent reconnect failed, or the server never started | tell the user, continue without the tool |
| `[MCP ERROR]` | unknown tool name or any other SDK failure | use another approach |

The existing anti-loop guard (3 consecutive failures of the same tool =
emergency stop + auto-research) protects MCP tools exactly like built-ins.

## 6. Machine-readable surface (additive)

* `POST /set-project` response: `"mcp": {"servers": [{"name", "status",
  "tools", "error"?}], "tools": <int>, "warnings": [<str>]}` with status
  `ok | error | disabled` (`"mcp": null` when the manager could not be
  imported).
* `GET /health`: `"mcp_servers": [<names of healthy servers>]`,
  `"mcp_tools": <registered tool count>`.
* Older extensions ignore unknown fields — same policy as the UPDATE-013
  interpreter fields. Backend version: **0.6.0**.

## 7. SDK compatibility (mcp 1.x and 2.x)

`requirements.txt` declares `mcp>=1.2.0`. The SDK renamed its model fields
between majors (1.x `inputSchema`/`isError`/`structuredContent` camelCase →
2.x `input_schema`/`is_error`/`structured_content` snake_case; the read
timeout is a `timedelta` on 1.x and a float on 2.x; the high-level server
class `FastMCP` became `MCPServer`). `mcp_manager.py` absorbs both shapes
(`_gf`, `_call_tool_compat`, `_looks_like_timeout`), and the demo server
imports whichever class exists. Both stacks are exercised by the unit
tests; the sandbox gate runs on mcp 2.3.0. Check yours with
`pip show mcp` inside `backend\venv` — any 1.2+ works.

## 8. Lifecycle & platform notes

* Servers are spawned when a project is set (or auto-restored at startup)
  and stopped when the project changes or the backend shuts down; the
  manager never leaves a session open across projects.
* Startup is bounded (15 s per server: spawn + initialize + list_tools);
  a server that fails to start is reported and skipped — the backend and
  every non-MCP feature stay fully operational.
* Shutdown is best-effort with a 10 s bound per server (Windows: the SDK
  terminates the subprocess; a hung child can never block backend exit).
* Only the **stdio** transport ships in 014. Streamable-HTTP/SSE and their
  OAuth flows are a later candidate.

## 9. Not in this update (by design)

VS Code UI for MCP configuration (Settings/Setup Wizard) → UPDATE-015;
sub-agent orchestration → 015/016; MCP resources & prompts (only **tools**
are consumed); remote transports; a server marketplace/registry.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `mcp` field missing in `/set-project` response | the manager failed to import — check the backend log for `[ORION STARTUP WARNING] mcp_manager.py ...` and run `pip install -r backend\requirements.txt` |
| Server status `error`, “did not become ready within 15s” | wrong `command` path (use ABSOLUTE paths; on Windows the venv python is `...\backend\venv\Scripts\python.exe`); test the command manually in a terminal |
| `npx` server fails on Windows | wrap it: `"command": "cmd", "args": ["/c", "npx", ...]` (see §2) |
| Tool never offered to the model | check warnings for a name collision (`already taken`) or `"disabled": true`; tool names appear as `mcp__<server>__<tool>` |
| `[MCP SERVER DOWN]` mid-session | the server process died; Orion already tried one reconnect — restart via **Set Project** and inspect the server's own log output in `<project>\.orion\logs\orion_stdout.log` |

## 11. The VS Code configuration surface (UPDATE-015)

Since backend 0.7.0 / extension 0.0.11 you no longer need to hand-edit
`mcp.json`:

* **MCP panel** — activity bar item "MCP Servers" (plug icon) or command
  *Orion: MCP Servers (tool servers)*: live server table (status badge,
  tool count, command, error text), add/edit form, enable-disable, remove,
  warnings, "Open mcp.json" escape hatch, and **Install demo server**
  (composes command/args automatically from `/health`, cross-OS).
* **Setup Wizard** — optional sixth card: open the panel or install the
  demo with one click; skipping changes nothing.
* Every mutation goes through `POST /mcp/config`: validate → atomic write
  → reload in ONE call, so the panel and the file can never disagree.
* **Privacy rule:** stored `env` VALUES are never displayed anywhere in the
  UI (only key names). Editing a server keeps its existing env unless you
  re-type every `KEY=VALUE` pair — deliberate, so a screen share can never
  leak a secret and a command edit can never silently wipe one.
* Management endpoints (`GET /mcp/status`, `POST /mcp/config`) are
  token-guarded like every route except `GET /`; the SSE contract and the
  agent loop are untouched by this surface.
