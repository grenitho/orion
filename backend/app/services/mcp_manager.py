"""
mcp_manager.py — MCP (Model Context Protocol) client foundation (UPDATE-014)
=============================================================================
Orion can extend its 22 built-in tools with tools served by local MCP
servers (filesystem, fetch, database, ... — anything speaking the protocol).
This module is the ONLY place in the backend that touches the ``mcp`` SDK.

Design contract (see docs/24_Orion_MCP_Foundation.md):

  * Configuration lives in ``<project>/.orion/mcp.json`` (Claude-Desktop
    style ``{"mcpServers": {...}}``); ``ORION_MCP_CONFIG`` overrides the
    location. A missing/broken file is NEVER fatal: it yields warnings and
    the backend simply runs without MCP tools.
  * Transport in 014: stdio only (the manager spawns each server as a
    subprocess and keeps the session alive for the whole project lifetime).
  * Tools are exposed to the LLM under a namespaced public name
    ``mcp__<server>__<tool>`` (sanitized to ``[a-zA-Z0-9_-]``, max 64 chars).
    Collisions with the built-in tools or between servers are skipped with
    a warning — never silently overwritten.
  * FAILURE CONTRACT: nothing in this module raises out of ``call()`` or
    ``start_all()``. Every failure (bad config, dead subprocess, timeout,
    tool-level error) becomes an informative ``[MCP ...]`` string that the
    agent loop hands back to the LLM as an ordinary tool result. The SSE
    stream contract (7 frozen events, exactly-one-done) is untouched: this
    module never yields frames itself.
  * SDK compatibility: ``mcp`` 1.x uses camelCase model fields
    (``inputSchema``/``isError``/``structuredContent``), 2.x renamed them to
    snake_case (``input_schema``/``is_error``/``structured_content``) and
    ``call_tool(read_timeout_seconds=...)`` takes a timedelta on 1.x but a
    float on 2.x. The small helpers below (``_gf``, ``_call_tool_compat``)
    absorb both shapes; the core client API (ClientSession, stdio_client,
    initialize/list_tools/call_tool) is stable across versions. Verified by
    the UPDATE-014 spike (sandbox: mcp 2.3.0) and unit tests simulating
    both field styles.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Soft import of the MCP SDK. ``mcp`` is declared in requirements.txt since
# UPDATE-010, but a broken/partial install must degrade to "no MCP tools",
# never to a dead backend (same policy as todo_manager in main.py).
# --------------------------------------------------------------------------
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_IMPORT_ERROR: str | None = None
except Exception as _mcp_import_err:  # pragma: no cover - depends on env
    ClientSession = None              # type: ignore[assignment]
    StdioServerParameters = None      # type: ignore[assignment]
    stdio_client = None               # type: ignore[assignment]
    MCP_IMPORT_ERROR = f"{type(_mcp_import_err).__name__}: {_mcp_import_err}"

try:
    from mcp.client.stdio import get_default_environment
except Exception:  # pragma: no cover - very old/new SDK layouts
    def get_default_environment() -> dict:
        """Minimal fallback: enough for a spawned server to find interpreters."""
        keep = {}
        for name in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "PATHEXT",
                     "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "LANG", "LC_ALL"):
            value = os.environ.get(name)
            if value is not None:
                keep[name] = value
        return keep


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
CONFIG_RELPATH = Path(".orion") / "mcp.json"
ENV_CONFIG_OVERRIDE = "ORION_MCP_CONFIG"

DEFAULT_CALL_TIMEOUT = 30.0     # seconds, per call_tool (per-server override)
DEFAULT_START_TIMEOUT = 15.0    # seconds, spawn+initialize+list_tools
HARD_GUARD_MARGIN = 5.0         # outer wait_for = call timeout + margin
MAX_TOOL_NAME_LEN = 64          # provider limit for function names
MAX_SERVER_NAME_LEN = 40
DESCRIPTION_MAX_LEN = 1024
ERROR_TEXT_MAX_LEN = 300

_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_-]")

# Result-string prefixes (the agent loop's is_terminal_failure / anti-loop
# guards look at plain text; these markers make MCP failures recognizable
# to the LLM without colliding with built-in tool output shapes).
PREFIX_TOOL_ERROR = "[MCP TOOL ERROR]"
PREFIX_TIMEOUT = "[MCP TIMEOUT]"
PREFIX_SERVER_DOWN = "[MCP SERVER DOWN]"
PREFIX_ERROR = "[MCP ERROR]"

# Connection-loss signatures (spike E7: a dead server surfaces as
# MCPError -32000 "Connection closed"; broken pipes vary by platform).
_CONN_LOSS_MARKERS = (
    "connection closed",
    "connection lost",
    "broken pipe",
    "brokenresource",
    "closedresource",
    "end of file",
    "eof occurred",
)


# --------------------------------------------------------------------------
# SDK compatibility helpers (mcp 1.x camelCase <-> 2.x snake_case)
# --------------------------------------------------------------------------
def _gf(obj: Any, snake: str, camel: str, default: Any = None) -> Any:
    """Read a model field that is snake_case on mcp 2.x, camelCase on 1.x."""
    value = getattr(obj, snake, None)
    if value is None:
        value = getattr(obj, camel, None)
    return default if value is None else value


def _looks_like_connection_loss(err: BaseException) -> bool:
    text = f"{type(err).__name__}: {err}".lower()
    return any(marker in text for marker in _CONN_LOSS_MARKERS)


_TIMEOUT_MARKERS = ("timed out", "timeout", "timedout")


def _looks_like_timeout(err: BaseException) -> bool:
    """True for OUR hard guard (asyncio.TimeoutError) AND for the SDK's own
    read-timeout, which surfaces as McpError code -32001 with a 'timed out'
    message (spike finding E4) — not an asyncio.TimeoutError subclass."""
    if isinstance(err, TimeoutError):  # == asyncio.TimeoutError on py3.11+
        return True
    text = f"{type(err).__name__}: {err}".lower()
    if any(marker in text for marker in _TIMEOUT_MARKERS):
        return True
    code = getattr(getattr(err, "exception", None), "code", None)
    if code is None:
        code = getattr(err, "code", None)
    return str(code) == "-32001"


async def _call_tool_compat(session, name: str, arguments: dict, timeout: float):
    """call_tool with read_timeout_seconds as float (2.x) or timedelta (1.x).

    1.x performs ``timedelta`` arithmetic on the value, so a float raises
    TypeError/AttributeError inside the SDK — detected and retried once with
    the correct type. The outer ``asyncio.wait_for`` in ``_call_once`` is the
    hard guard either way.
    """
    try:
        return await session.call_tool(
            name, arguments, read_timeout_seconds=timeout
        )
    except (TypeError, AttributeError):
        from datetime import timedelta

        return await session.call_tool(
            name, arguments, read_timeout_seconds=timedelta(seconds=timeout)
        )


# --------------------------------------------------------------------------
# Pure helpers (unit-tested without any subprocess)
# --------------------------------------------------------------------------
def sanitize_component(text: str) -> str:
    """Replace every character outside [a-zA-Z0-9_-] with an underscore."""
    return _NAME_INVALID.sub("_", str(text))


def public_tool_name(server: str, tool: str) -> str:
    """The namespaced name the LLM sees: mcp__<server>__<tool>, <=64 chars.

    Truncation keeps the TOOL side (the server side is short and bounded by
    MAX_SERVER_NAME_LEN), so two tools on one server never collide through
    truncation unless their own names are absurdly long — in which case the
    registry's collision check still protects us.
    """
    prefix = f"mcp__{sanitize_component(server)[:MAX_SERVER_NAME_LEN]}__"
    tool_part = sanitize_component(tool)
    budget = MAX_TOOL_NAME_LEN - len(prefix)
    if budget < 1:  # server name ate the whole budget (cannot normally happen)
        return prefix[:MAX_TOOL_NAME_LEN]
    return prefix + tool_part[:budget]


def builtin_tool_names() -> set:
    """The 22 dispatched built-in tools (import is cheap and cycle-free)."""
    from app.services import tool_dispatch

    return set(tool_dispatch.NAMA_TOOL_AKTIF)


def build_openai_tool(public_name: str, description: str, input_schema: Any) -> dict:
    """Convert one MCP Tool into an OpenAI-style function entry.

    ``input_schema`` is passed through verbatim when it is a JSON-Schema-ish
    dict (providers accept standard keywords like title/properties/
    required); anything missing or malformed falls back to an empty object
    schema so the tool stays callable without arguments.
    """
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        parameters = {"type": "object", "properties": {}}
    else:
        parameters = input_schema
    text = (description or "").strip() or f"MCP tool {public_name}."
    return {
        "type": "function",
        "function": {
            "name": public_name,
            "description": text[:DESCRIPTION_MAX_LEN],
            "parameters": parameters,
        },
    }


def format_tool_result(result: Any) -> str:
    """Render a CallToolResult (1.x or 2.x shape) as one tool-result string.

    * text content parts are joined with newlines;
    * non-text parts (image/audio/resource) become a typed placeholder;
    * with no content at all, structuredContent (2.x) is JSON-rendered;
    * ``is_error=True`` prefixes ``[MCP TOOL ERROR]`` so the LLM treats the
      text as a failed attempt, exactly like a failed built-in tool.
    """
    parts: list[str] = []
    content = getattr(result, "content", None) or []
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                ctype = getattr(item, "type", None) or type(item).__name__
                parts.append(f"[non-text content: {ctype}]")
    if not parts:
        structured = _gf(result, "structured_content", "structuredContent")
        if structured is not None:
            try:
                parts.append(json.dumps(structured, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                parts.append(str(structured))
    body = "\n".join(part for part in parts if part and part.strip()).strip()
    if not body:
        body = "(the tool returned no content)"
    if _gf(result, "is_error", "isError"):
        return f"{PREFIX_TOOL_ERROR} {body}"
    return body


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class ServerSpec:
    """One validated ``mcpServers`` entry."""

    name: str
    command: str
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    timeout: float = DEFAULT_CALL_TIMEOUT
    disabled: bool = False


_KNOWN_SPEC_KEYS = {"command", "args", "env", "timeoutSeconds", "disabled"}


def build_server_spec(name: str, raw: Any) -> tuple[ServerSpec | None, list[str]]:
    """Validate one server entry; return (spec-or-None, warnings).

    Deliberately forgiving for beginners: fixable problems (odd args type,
    unknown keys, out-of-range timeout) produce a warning and a sane
    default; only a missing/empty ``command`` rejects the entry.
    """
    warnings: list[str] = []
    clean_name = str(name).strip()
    if not clean_name:
        return None, ["an MCP server entry has an empty name; entry skipped"]
    if len(clean_name) > MAX_SERVER_NAME_LEN:
        warnings.append(
            f"MCP server name '{clean_name[:20]}...' is longer than "
            f"{MAX_SERVER_NAME_LEN} chars; tool names will be truncated"
        )
    if sanitize_component(clean_name) != clean_name:
        warnings.append(
            f"MCP server name '{clean_name}' contains characters outside "
            "[a-zA-Z0-9_-]; they are replaced with '_' in tool names"
        )
    if not isinstance(raw, dict):
        return None, [f"MCP server '{clean_name}' must be a JSON object; entry skipped"]

    unknown = sorted(set(raw) - _KNOWN_SPEC_KEYS)
    if unknown:
        warnings.append(
            f"MCP server '{clean_name}': unknown config key(s) {unknown} "
            "(known: command, args, env, timeoutSeconds, disabled)"
        )

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        return None, [
            f"MCP server '{clean_name}' has no non-empty string \"command\"; entry skipped"
        ]

    raw_args = raw.get("args", [])
    if isinstance(raw_args, str):
        warnings.append(
            f"MCP server '{clean_name}': \"args\" should be a list of strings; "
            "the single string was wrapped into a one-element list"
        )
        args = [raw_args]
    elif isinstance(raw_args, list):
        args = [str(a) for a in raw_args]
    else:
        warnings.append(
            f"MCP server '{clean_name}': \"args\" is not a list; using [] instead"
        )
        args = []

    raw_env = raw.get("env", {})
    if isinstance(raw_env, dict):
        env = {str(k): str(v) for k, v in raw_env.items()}
    else:
        warnings.append(
            f"MCP server '{clean_name}': \"env\" is not an object; using {{}} instead"
        )
        env = {}

    timeout = DEFAULT_CALL_TIMEOUT
    raw_timeout = raw.get("timeoutSeconds")
    if raw_timeout is not None:
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
            warnings.append(
                f"MCP server '{clean_name}': \"timeoutSeconds\" must be a number; "
                f"using the default {DEFAULT_CALL_TIMEOUT:g}s"
            )
        elif not (1.0 <= float(raw_timeout) <= 600.0):
            warnings.append(
                f"MCP server '{clean_name}': \"timeoutSeconds\" {raw_timeout} is "
                f"outside 1..600; using the default {DEFAULT_CALL_TIMEOUT:g}s"
            )
        else:
            timeout = float(raw_timeout)

    disabled = bool(raw.get("disabled", False))

    return (
        ServerSpec(name=clean_name, command=command.strip(), args=args,
                   env=env, timeout=timeout, disabled=disabled),
        warnings,
    )


def parse_config(raw_text: str) -> tuple[dict, list[str]]:
    """Parse the mcp.json text into {name: ServerSpec} + warnings."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {}, [
            f"mcp.json is not valid JSON ({exc.msg} at line {exc.lineno} "
            f"column {exc.colno}); MCP disabled for this project"
        ]
    if not isinstance(data, dict):
        return {}, ["mcp.json must be a JSON object with an \"mcpServers\" key; MCP disabled"]
    servers = data.get("mcpServers", {})
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        return {}, ['"mcpServers" must be an object mapping names to server configs; MCP disabled']
    if not servers:
        return {}, []

    specs: dict[str, ServerSpec] = {}
    warnings: list[str] = []
    for name in servers:  # config order is preserved for the summary
        spec, entry_warnings = build_server_spec(str(name), servers[name])
        warnings.extend(entry_warnings)
        if spec is not None and spec.name in specs:
            warnings.append(f"duplicate MCP server name '{spec.name}'; keeping the first entry")
        elif spec is not None:
            specs[spec.name] = spec
    return specs, warnings


def mutate_servers(raw: dict, op: str, payload: dict) -> tuple[dict | None, str | None]:
    """Pure mcp.json mutation for POST /mcp/config (UPDATE-015).

    Returns (new raw dict, None) or (None, error message). Deliberately
    pure: writing + reloading lives in MCPManager.apply_config_op so this
    function is unit-testable without any filesystem or subprocess.

    Ops:
      set          {name, spec[, keep_env]}  add/update one server entry;
                   keep_env=true + an empty env in the new spec preserves
                   the stored env (the panel never shows secret values, so
                   editing a command must not silently wipe the secrets).
      remove       {name}
      toggle       {name[, disabled]}   flip (or set) the disabled flag
      replace_all  {servers}             whole mcpServers map, all validated
    Unknown top-level keys of the file are preserved verbatim.
    """
    if not isinstance(raw, dict):
        raw = {}
    servers = raw.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        return None, (
            '"mcpServers" in the existing file is not an object; '
            "edit the file manually"
        )
    servers = dict(servers)

    def _entry_from_spec(built: ServerSpec) -> dict:
        entry: dict = {"command": built.command, "args": list(built.args)}
        if built.env:
            entry["env"] = dict(built.env)
        if built.timeout != DEFAULT_CALL_TIMEOUT:
            entry["timeoutSeconds"] = built.timeout
        if built.disabled:
            entry["disabled"] = True
        return entry

    if op == "set":
        name = str(payload.get("name") or "").strip()
        spec = payload.get("spec")
        if not name or not isinstance(spec, dict):
            return None, "op 'set' needs a non-empty 'name' and a 'spec' object"
        built, warnings = build_server_spec(name, spec)
        if built is None:
            return None, f"invalid server spec: {warnings[0] if warnings else 'missing command'}"
        if payload.get("keep_env") and not built.env:
            old = servers.get(name)
            if isinstance(old, dict) and isinstance(old.get("env"), dict):
                built.env = {str(k): str(v) for k, v in old["env"].items()}
        servers[name] = _entry_from_spec(built)
    elif op == "remove":
        name = str(payload.get("name") or "").strip()
        if name not in servers:
            return None, f"server '{name}' is not in the config"
        del servers[name]
    elif op == "toggle":
        name = str(payload.get("name") or "").strip()
        entry = servers.get(name)
        if not isinstance(entry, dict):
            return None, f"server '{name}' is not in the config"
        disabled = payload.get("disabled")
        if disabled is None:
            disabled = not bool(entry.get("disabled", False))
        servers[name] = {**entry, "disabled": bool(disabled)}
    elif op == "replace_all":
        new_servers = payload.get("servers")
        if not isinstance(new_servers, dict):
            return None, "op 'replace_all' needs a 'servers' object"
        for key in new_servers:
            built, warnings = build_server_spec(str(key), new_servers[key])
            if built is None:
                return None, (
                    f"invalid server '{key}': "
                    f"{warnings[0] if warnings else 'missing command'}"
                )
        servers = {str(k): v for k, v in new_servers.items()}
    else:
        return None, f"unknown op '{op}' (expected: set, remove, toggle, replace_all)"

    out = dict(raw)
    out["mcpServers"] = servers
    return out, None


# --------------------------------------------------------------------------
# Runtime state
# --------------------------------------------------------------------------
@dataclass
class _ServerRuntime:
    """Live state of one configured server."""

    spec: ServerSpec
    status: str = "error"          # ok | error | disabled
    error: str | None = None
    session: Any = None
    stack: AsyncExitStack | None = None
    tools: dict = field(default_factory=dict)      # public name -> original name
    openai: list = field(default_factory=list)     # converted function schemas


# --------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------
class MCPManager:
    """Owns every MCP subprocess/session of the ACTIVE project.

    Lifecycle: ``start_all(project_path)`` on /set-project (and on startup
    auto-restore), ``stop_all()`` when the project changes or the backend
    shuts down. One manager instance serves one project at a time; calling
    ``start_all`` again stops the previous servers first.
    """

    def __init__(self, start_timeout: float = DEFAULT_START_TIMEOUT):
        self._servers: dict[str, _ServerRuntime] = {}
        self._registry: dict[str, tuple[str, str]] = {}  # public -> (server, original)
        self._warnings: list[str] = []
        self.project_path: str | None = None
        self.config_path: Path | None = None
        # >>> UPDATE-017: master switch read from the project's
        # orion_config.json ("mcp_enabled", default True). False = the whole
        # MCP layer stays off for this project without deleting mcp.json
        # (the "stop kontak" the owner asked for; per-server Disable/Enable
        # already exists since UPDATE-015).
        self.enabled: bool = True
        self._start_timeout = start_timeout

    # ------------------------------------------------------------- config

    def resolve_config_path(self, project_path: str | None) -> Path | None:
        override = os.getenv(ENV_CONFIG_OVERRIDE, "").strip()
        if override:
            return Path(override).expanduser()
        if project_path:
            return Path(project_path) / CONFIG_RELPATH
        return None

    def _read_enabled_flag(self, project_path: str | None) -> bool:
        """The mcp_enabled master switch from <project>/.orion/orion_config.json.

        Missing file, unreadable file, broken JSON or missing key all mean
        ENABLED (the safe historical behaviour); only an explicit false-ish
        value turns the layer off.
        """
        if not project_path:
            return True
        cfg_path = Path(project_path) / ".orion" / "orion_config.json"
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return True
        if isinstance(data, dict):
            return bool(data.get("mcp_enabled", True))
        return True

    def load_config(self, project_path: str | None) -> dict:
        """Read mcp.json for the project; never raises."""
        self._warnings = []
        if MCP_IMPORT_ERROR is not None:
            self._warnings.append(
                f"the mcp package is unavailable ({MCP_IMPORT_ERROR}); "
                "run: pip install -r backend/requirements.txt"
            )
            return {}
        self.enabled = self._read_enabled_flag(project_path)
        if not self.enabled:
            self._warnings.append(
                "MCP is disabled for this project (mcp_enabled=false in "
                ".orion/orion_config.json); no servers were started. The "
                "config file stays untouched so re-enabling is one flag away."
            )
            return {}
        path = self.resolve_config_path(project_path)
        self.config_path = path
        if path is None or not path.exists():
            return {}
        try:
            # utf-8-sig: Windows writing tools love BOMs (PowerShell 5.1
            # Set-Content -Encoding UTF8, some Notepad modes) and json.loads
            # chokes on the leading \ufeff. Reading as utf-8-sig accepts both
            # BOM and no-BOM files — observed in the field during the
            # UPDATE-014 owner E2E.
            raw = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self._warnings.append(f"cannot read {path.name}: {exc}")
            return {}
        specs, warnings = parse_config(raw)
        self._warnings.extend(warnings)
        return specs

    # ---------------------------------------------------------- lifecycle

    async def start_all(self, project_path: str | None) -> dict:
        """Stop old servers, then spawn every enabled server of the new
        project. Returns the summary dict embedded in the /set-project
        response. Never raises."""
        await self.stop_all()
        self.project_path = str(project_path) if project_path else None
        specs = self.load_config(project_path)

        for name in sorted(specs):  # sorted = deterministic startup order
            spec = specs[name]
            if spec.disabled:
                self._servers[name] = _ServerRuntime(spec=spec, status="disabled")
                continue
            runtime = await self._start_server(spec)
            self._servers[name] = runtime
            if runtime.status == "ok":
                self._register_tools(runtime)
        return self.summary()

    async def _start_server(self, spec: ServerSpec) -> _ServerRuntime:
        """Spawn + initialize + list_tools for one server, bounded in time."""
        runtime = _ServerRuntime(spec=spec)
        if ClientSession is None or stdio_client is None or StdioServerParameters is None:
            runtime.error = f"mcp package unavailable: {MCP_IMPORT_ERROR}"
            return runtime
        stack = AsyncExitStack()
        try:
            env = None
            if spec.env:
                try:
                    env = {**get_default_environment(), **spec.env}
                except Exception:  # pragma: no cover - defensive
                    env = dict(spec.env)
            params = StdioServerParameters(
                command=spec.command, args=list(spec.args), env=env
            )
            read, write = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(params)),
                timeout=self._start_timeout,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self._start_timeout,
            )
            await asyncio.wait_for(session.initialize(), timeout=self._start_timeout)
            listed = await asyncio.wait_for(session.list_tools(), timeout=self._start_timeout)

            runtime.session = session
            runtime.stack = stack
            runtime.status = "ok"
            for tool in getattr(listed, "tools", None) or []:
                original = str(getattr(tool, "name", "") or "")
                if not original:
                    continue
                public = public_tool_name(spec.name, original)
                schema = _gf(tool, "input_schema", "inputSchema")
                description = getattr(tool, "description", "") or ""
                runtime.tools[public] = original
                runtime.openai.append(build_openai_tool(public, description, schema))
            return runtime
        except asyncio.TimeoutError:
            runtime.status = "error"
            runtime.error = (
                f"server did not become ready within {self._start_timeout:g}s "
                f"(command: {spec.command})"
            )
            await self._close_stack(stack)
            return runtime
        except Exception as exc:
            runtime.status = "error"
            runtime.error = f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_MAX_LEN]
            await self._close_stack(stack)
            return runtime

    async def _close_stack(self, stack: AsyncExitStack | None) -> None:
        if stack is None:
            return
        try:
            await asyncio.wait_for(stack.aclose(), timeout=10)
        except Exception:
            pass  # subprocess cleanup is best-effort; the OS reaps orphans

    def _register_tools(self, runtime: _ServerRuntime) -> None:
        taken = builtin_tool_names() | set(self._registry)
        for public in list(runtime.tools):
            if public in taken:
                server, original = runtime.spec.name, runtime.tools[public]
                self._warnings.append(
                    f"tool '{original}' of MCP server '{server}' was skipped: "
                    f"the name '{public}' is already taken"
                )
                del runtime.tools[public]
                runtime.openai = [t for t in runtime.openai
                                  if t["function"]["name"] != public]
                continue
            self._registry[public] = (runtime.spec.name, runtime.tools[public])
            taken.add(public)

    def _unregister_tools(self, runtime: _ServerRuntime) -> None:
        for public in list(runtime.tools):
            if self._registry.get(public, (None,))[0] == runtime.spec.name:
                del self._registry[public]

    async def stop_all(self) -> None:
        """Close every session/subprocess. Safe to call repeatedly."""
        for runtime in self._servers.values():
            await self._stop_server(runtime)
        self._servers.clear()
        self._registry.clear()

    async def _stop_server(self, runtime: _ServerRuntime) -> None:
        stack, runtime.stack = runtime.stack, None
        runtime.session = None
        if runtime.status == "ok":
            runtime.status = "error"
            runtime.error = "stopped"
        await self._close_stack(stack)

    # --------------------------------------------------------- inspection

    def has_tool(self, public_name: str) -> bool:
        return public_name in self._registry

    def tool_names(self) -> set:
        return set(self._registry)

    def status_label(self, public_name: str) -> str:
        """'server/tool' for the 🔌 status frame; falls back to the raw name."""
        hit = self._registry.get(public_name)
        return f"{hit[0]}/{hit[1]}" if hit else public_name

    def openai_tools(self) -> list:
        """Function schemas of every healthy server's tools, in start order."""
        tools: list = []
        for runtime in self._servers.values():
            if runtime.status == "ok":
                tools.extend(runtime.openai)
        return tools

    def summary(self) -> dict:
        """The additive ``mcp`` field of the /set-project response."""
        servers = []
        for name, runtime in self._servers.items():
            entry = {"name": name, "status": runtime.status, "tools": len(runtime.tools)}
            if runtime.error:
                entry["error"] = runtime.error
            servers.append(entry)
        return {
            "servers": servers,
            "tools": len(self._registry),
            "warnings": list(self._warnings),
            "enabled": self.enabled,  # UPDATE-017 master switch visibility
        }

    # ------------------------------------------------- management (UPDATE-015)

    def status_detail(self) -> dict:
        """The full picture behind GET /mcp/status.

        SECURITY: env VALUES never leave this process — only the key names
        (``env_keys``), so the VS Code panel can say "this server has 2 env
        vars" during a screen share without ever showing a secret. Editing
        env means re-submitting every KEY=VALUE pair (panel explains this).
        """
        servers = []
        for name, runtime in self._servers.items():
            servers.append({
                "name": name,
                "status": runtime.status,
                "tools": len(runtime.tools),
                "error": runtime.error,
                "disabled": runtime.spec.disabled,
                "command": runtime.spec.command,
                "args": list(runtime.spec.args),
                "timeout": runtime.spec.timeout,
                "env_keys": sorted(runtime.spec.env),
            })
        return {
            "project_path": self.project_path,
            "config_path": str(self.config_path) if self.config_path else None,
            "warnings": list(self._warnings),
            "servers": servers,
            "enabled": self.enabled,  # UPDATE-017 master switch visibility
        }

    async def apply_config_op(self, op: str, payload: dict) -> dict:
        """Validate -> atomically write mcp.json -> reload servers.

        One call = one transaction: a rejected op leaves the existing file
        byte-for-byte untouched; a accepted op always ends with the servers
        reloaded from what was just written (no "file saved but servers
        still old" state). Never raises. Returns
        {"success", "error"|None, "mcp": summary}.
        """
        result: dict = {"success": False, "error": None, "mcp": self.summary()}
        if not self.project_path:
            result["error"] = "no active project; run Set Project first"
            return result
        path = self.resolve_config_path(self.project_path)
        if path is None:  # pragma: no cover - resolve only fails w/o project
            result["error"] = "cannot resolve the mcp.json location"
            return result

        current: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                result["error"] = (
                    "the existing mcp.json is not valid JSON; fix or delete "
                    "it manually (this operation was refused, file untouched)"
                )
                return result
            except OSError as exc:
                result["error"] = f"cannot read {path.name}: {exc}"
                return result
            if not isinstance(loaded, dict):
                result["error"] = (
                    "the existing mcp.json is not a JSON object; fix or "
                    "delete it manually (this operation was refused)"
                )
                return result
            current = loaded

        new_raw, err = mutate_servers(current, op, payload)
        if err is not None:
            result["error"] = err
            return result
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(new_raw, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError as exc:
            result["error"] = f"cannot write {path.name}: {exc}"
            return result

        try:
            summary = await self.start_all(self.project_path)
        except Exception as exc:  # pragma: no cover - start_all never raises
            summary = {"servers": [], "tools": 0, "warnings": [str(exc)]}
        result["success"] = True
        result["mcp"] = summary
        return result

    def health_fields(self) -> dict:
        """Additive GET /health fields (old extensions ignore unknown keys)."""
        return {
            "mcp_servers": [
                name for name, runtime in self._servers.items()
                if runtime.status == "ok"
            ],
            "mcp_tools": len(self._registry),
        }

    # --------------------------------------------------------------- call

    async def call(self, public_name: str, arguments: dict | None = None) -> str:
        """Invoke one MCP tool; ALWAYS returns a string, never raises.

        Failure shapes (all informative for the LLM):
          [MCP ERROR]        unknown tool / unexpected SDK failure
          [MCP TIMEOUT]      the server did not answer in time
          [MCP SERVER DOWN]  connection lost and the one transparent
                             reconnect attempt failed
          [MCP TOOL ERROR]   the tool itself reported an error result
        """
        try:
            hit = self._registry.get(public_name)
            if hit is None:
                return (
                    f"{PREFIX_ERROR} tool '{public_name}' is not registered; "
                    f"available MCP tools: {sorted(self._registry) or '(none)'}"
                )
            server_name, original = hit
            runtime = self._servers.get(server_name)
            if runtime is None or runtime.status != "ok" or runtime.session is None:
                if runtime is not None and await self._restart(runtime):
                    pass
                else:
                    reason = runtime.error if runtime else "server vanished"
                    return (
                        f"{PREFIX_SERVER_DOWN} '{server_name}' is not connected "
                        f"({reason}). The tool '{original}' cannot be used right now; "
                        "tell the user the MCP server is down and continue without it."
                    )
            return await self._call_guarded(runtime, original, arguments)
        except Exception as exc:  # pragma: no cover - last-resort guard
            return f"{PREFIX_ERROR} unexpected failure calling '{public_name}': {exc}"[:ERROR_TEXT_MAX_LEN]

    def _timeout_message(self, runtime: _ServerRuntime, original: str,
                         suffix: str = "") -> str:
        return (
            f"{PREFIX_TIMEOUT} tool '{original}' on MCP server "
            f"'{runtime.spec.name}' did not answer within "
            f"{runtime.spec.timeout:g} seconds{suffix}. "
            "It was NOT executed to completion; do not blindly retry — try a "
            "smaller/different operation or continue without this tool."
        )

    async def _call_guarded(self, runtime: _ServerRuntime, original: str,
                            arguments: dict | None) -> str:
        """One call with timeout, connection-loss detection and ONE retry
        after a transparent reconnect (spike finding: a dead server keeps
        answering 'Connection closed' — retrying without reconnect is useless,
        and more than one reconnect risks a new loop)."""
        try:
            return await self._call_once(runtime, original, arguments, runtime.spec.timeout)
        except Exception as exc:
            # The SDK's own read timeout is an McpError ('timed out', -32001),
            # NOT asyncio.TimeoutError — _looks_like_timeout covers both, plus
            # the outer wait_for hard guard.
            if _looks_like_timeout(exc):
                return self._timeout_message(runtime, original)
            if not _looks_like_connection_loss(exc):
                return (
                    f"{PREFIX_ERROR} call to '{original}' failed: "
                    f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_MAX_LEN]
                )
            if not await self._restart(runtime):
                return (
                    f"{PREFIX_SERVER_DOWN} '{runtime.spec.name}' lost its connection "
                    f"({type(exc).__name__}: {str(exc)[:120]}) and could not be "
                    f"restarted ({runtime.error}). Continue without this tool and "
                    "tell the user the MCP server died."
                )
            try:
                return await self._call_once(runtime, original, arguments, runtime.spec.timeout)
            except Exception as exc2:
                if _looks_like_timeout(exc2):
                    return self._timeout_message(runtime, original, suffix=" (after a reconnect)")
                return (
                    f"{PREFIX_SERVER_DOWN} '{runtime.spec.name}' failed again right "
                    f"after reconnecting: {type(exc2).__name__}: {str(exc2)[:120]}"
                )

    async def _call_once(self, runtime: _ServerRuntime, original: str,
                         arguments: dict | None, timeout: float) -> str:
        async def _inner():
            return await _call_tool_compat(
                runtime.session, original, arguments or {}, timeout
            )

        result = await asyncio.wait_for(_inner(), timeout=timeout + HARD_GUARD_MARGIN)
        return format_tool_result(result)

    async def _restart(self, runtime: _ServerRuntime) -> bool:
        """Tear down and respawn one server; re-register its tools on success."""
        self._unregister_tools(runtime)
        old_stack, runtime.stack = runtime.stack, None
        runtime.session = None
        runtime.status = "error"
        runtime.error = None
        await self._close_stack(old_stack)

        fresh = await self._start_server(runtime.spec)
        if fresh.status != "ok":
            runtime.error = fresh.error or "restart failed"
            return False
        runtime.session = fresh.session
        runtime.stack = fresh.stack
        runtime.status = "ok"
        runtime.error = None
        runtime.tools = fresh.tools
        runtime.openai = fresh.openai
        self._register_tools(runtime)
        return True
