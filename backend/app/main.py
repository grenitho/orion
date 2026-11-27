import os
import sys
from pathlib import Path


def _resolve_stdout_log() -> Path:
    """Decide where every print()/stdout line of the backend goes."""
    env = os.getenv("ORION_STDOUT_LOG")
    if env:
        return Path(env).expanduser()
    try:
        target = (
            Path(__file__).resolve().parents[2] / ".orion" / "logs" / "orion_stdout.log"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        return Path.home() / "orion_stdout.log"


_STDOUT_LOG_PATH = _resolve_stdout_log()


def _open_stdout_sink(path: Path):
    """Open the stdout sink for ``path`` — rotating since UPDATE-013."""
    try:
        from app.stdout_rotation import RotatingFileWriter
    except ImportError:  # pragma: no cover
        try:
            from stdout_rotation import RotatingFileWriter  # type: ignore[no-redef]
        except ImportError:
            return open(path, "a", encoding="utf-8", buffering=1)
    return RotatingFileWriter(path)


try:
    sys.stdout = _open_stdout_sink(_STDOUT_LOG_PATH)
except OSError:  # pragma: no cover - last resort: historical home location
    _STDOUT_LOG_PATH = Path.home() / "orion_stdout.log"
    sys.stdout = _open_stdout_sink(_STDOUT_LOG_PATH)
sys.stderr = sys.stdout

try:
    from app.stdout_rotation import repoint_handlers as _repoint_handlers

    _repoint_handlers(sys.stderr)
except Exception:  # noqa: BLE001 - logging hygiene must never block boot
    pass

# >>> CRITICAL FIX: load_dotenv() MUST be called HERE, FIRST, BEFORE any
# module below is imported.
from dotenv import load_dotenv

load_dotenv()
# >>> END OF CRITICAL FIX <<<

import asyncio
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

VALIDATE_MAX_DIRS = 20_000

_VALIDATE_PRUNE_EXACT = {
    ".git",
    ".orion",
    ".vscode",
    ".idea",
    ".pytest_cache",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    "target",
}


def _validate_dir_pruned(name: str) -> bool:
    """True for folders the duplicate-assets walk must never enter."""
    if name in _VALIDATE_PRUNE_EXACT:
        return True
    low = name.lower()
    if low.startswith(("venv", ".venv")):
        return True
    return ("node_modules" in low) or ("__pycache__" in low)


def validate_project_structure(project_path: str) -> list:
    """Check and report suspicious folder structures (duplicates, junk, etc.)."""
    warnings = []
    visited = 0
    capped = False

    for root, dirs, _files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not _validate_dir_pruned(d)]
        visited += 1
        if visited > VALIDATE_MAX_DIRS:
            capped = True
            dirs[:] = []
            break

        if "assets" in dirs:
            assets_path = os.path.join(root, "assets")
            expected_assets = os.path.join(project_path, "assets")
            if os.path.normpath(assets_path) != os.path.normpath(expected_assets):
                warnings.append(f"⚠️ Duplicate folder detected: {assets_path}")

    if capped:
        warnings.append(
            f"⚠️ STRUCTURE SCAN CAPPED: checked {VALIDATE_MAX_DIRS} folders; "
            "deeper folders were NOT checked for duplicate 'assets' folders. "
            "Inspect them manually if the project really is this large."
        )
    return warnings


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.services import auth, findings_ledger, sse, stream_utils
from app.services import tool_dispatch
from app.services.agent_loop import Deps, aliran_agent, blok_jangkar_tugas
from app.services.session_state import SessionState, normalisasi_session_id
from app.services.tools_schema import TOOLS
from app.services.context_manager import ContextManager
from app.services.dependency_scanner import DependencyScanner
from app.services.llm_router import (
    build_profile_queue,
    get_ai_response,
    model_options,
)
from app.services.tool_executor import ToolExecutor

try:
    from app.services.todo_manager import TodoManager
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] todo_manager.py failed to import ({e}). Todo-list feature disabled."
    )
    TodoManager = None

try:
    from app.services.mcp_manager import MCPManager
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] mcp_manager.py failed to import ({e}). MCP tool-server support disabled."
    )
    MCPManager = None

try:
    from app.services import inbox_manager
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] inbox_manager.py failed to import ({e}). Inbox feature disabled."
    )
    inbox_manager = None


import logging

logging.basicConfig(
    filename=os.path.join(os.path.expanduser("~"), "orion_crash.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

try:
    from app.logging_setup import get_logger, setup_logging
except ImportError:  # pragma: no cover
    from logging_setup import get_logger, setup_logging  # type: ignore[no-redef]

try:
    from app import runtime_id
except ImportError:  # pragma: no cover
    import runtime_id  # type: ignore[no-redef]

setup_logging()
logger = get_logger("orion.main")


def _atomic_write_json(target_path: str, payload: dict, trailing_newline: bool = False) -> None:
    """Write JSON atomically using a PID+UUID temp file and os.replace."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    tmp_path = f"{target_path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            if trailing_newline:
                f.write("\n")
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup & shutdown lifecycle hook."""
    try:
        from app.stdout_rotation import repoint_handlers

        repoint_handlers(sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    _klaim_lama = runtime_id.read_pid_file()
    pid_path = runtime_id.write_pid_file()
    if _klaim_lama["exists"] and _klaim_lama["error"] is None:
        _p = _klaim_lama.get("pid")
        if isinstance(_p, int) and _p != os.getpid():
            if runtime_id.pid_alive(_p):
                print(
                    f"[ORION RUNTIME WARNING] TWIN SUSPECTED: the PID file "
                    f"claimed alive pid {_p} (version "
                    f"{_klaim_lama.get('version')}) while this process "
                    f"({os.getpid()}) booted — two backends raced for this "
                    f"install; only one can hold port 8000. Diagnose: "
                    f"GET /runtime/forensics on the survivor."
                )
            else:
                print(
                    f"[ORION RUNTIME] PID file held dead pid {_p} (unclean "
                    f"previous shutdown); the claim was replaced by this "
                    f"process ({os.getpid()})."
                )
    if pid_path:
        print(f"[ORION RUNTIME] PID file written: {pid_path} (pid={os.getpid()})")
    await auto_restore_project()
    yield
    # >>> P9.0: Stop MCP subprocesses across ALL active ProjectContexts
    stopped_managers = set()
    for _proj_ctx in list(_project_contexts.values()):
        if _proj_ctx.mcp_manager is not None and id(_proj_ctx.mcp_manager) not in stopped_managers:
            stopped_managers.add(id(_proj_ctx.mcp_manager))
            await _proj_ctx.mcp_manager.stop_all()
    if mcp_manager is not None and id(mcp_manager) not in stopped_managers:
        await mcp_manager.stop_all()
    runtime_id.clear_pid_file()


app = FastAPI(
    title="Project Orion Backend",
    version=runtime_id.BACKEND_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^vscode-webview://",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def shared_secret_guard(request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/":
        return await call_next(request)
    if auth.request_is_authorized(request.headers):
        return await call_next(request)
    return JSONResponse(
        {"error": "unauthorized: missing or wrong Orion shared token (X-Orion-Token)"},
        status_code=401,
    )


# >>> P9.0: Multi-Window ProjectContext & Multi-Session SessionContext Registry
SESSION_IDLE_TTL_SECONDS = float(os.getenv("ORION_SESSION_IDLE_TTL", "1800"))
MAX_CACHED_SESSIONS_PER_PROJECT = int(os.getenv("ORION_MAX_CACHED_SESSIONS", "32"))


@dataclass
class SessionContext:
    """Isolated per-(project_path, session_id) runtime container."""

    session_id: str
    context_manager: ContextManager
    todo_manager: object | None
    state: SessionState
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_accessed: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_accessed = time.monotonic()
        self.state.sentuh_aktivitas()


@dataclass
class ProjectContext:
    """Isolated per-project runtime container (Multi-Window safe)."""

    project_path: str
    real_path: str
    project_map: str = ""
    orion_config: dict = field(default_factory=lambda: {"max_iterations": 20})
    tool_executor: object | None = None
    mcp_manager: object | None = None
    project_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sessions: dict[str, SessionContext] = field(default_factory=dict)

    def get_or_create_session(self, session_id: str | None = "default") -> SessionContext:
        sid = normalisasi_session_id(session_id)
        self.evict_idle_sessions()
        sess = self.sessions.get(sid)
        if sess is None:
            cm = ContextManager(self.project_path, session_id=sid)
            cm.set_project_map(self.project_map)
            tm = TodoManager(self.project_path, session_id=sid) if TodoManager else None
            st = SessionState.muat_checkpoint(self.project_path, session_id=sid)
            sess = SessionContext(
                session_id=sid,
                context_manager=cm,
                todo_manager=tm,
                state=st,
            )
            self.sessions[sid] = sess
        else:
            sess.context_manager.set_project_map(self.project_map)
        sess.touch()
        return sess

    def evict_idle_sessions(
        self,
        now: float | None = None,
        ttl: float | None = None,
        max_sessions: int | None = None,
    ) -> list[str]:
        """Evict idle non-'default' unlocked sessions from RAM after persisting checkpoint."""
        current = time.monotonic() if now is None else now
        effective_ttl = SESSION_IDLE_TTL_SECONDS if ttl is None else ttl
        effective_max = (
            MAX_CACHED_SESSIONS_PER_PROJECT if max_sessions is None else max_sessions
        )
        evicted: list[str] = []

        # 1. TTL-based eviction
        for sid, sess in list(self.sessions.items()):
            if sid == "default" or sess.lock.locked():
                continue
            if (current - sess.last_accessed) > effective_ttl:
                sess.state.simpan_checkpoint(self.project_path)
                self.sessions.pop(sid, None)
                evicted.append(sid)

        # 2. Capacity-based LRU eviction if still over max_sessions
        if len(self.sessions) > effective_max:
            candidates = [
                (sess.last_accessed, sid, sess)
                for sid, sess in self.sessions.items()
                if sid != "default" and not sess.lock.locked()
            ]
            candidates.sort(key=lambda item: item[0])
            while len(self.sessions) > effective_max and candidates:
                _, sid, sess = candidates.pop(0)
                sess.state.simpan_checkpoint(self.project_path)
                self.sessions.pop(sid, None)
                evicted.append(sid)

        return evicted


_project_contexts: dict[str, ProjectContext] = {}
_active_project_path: str | None = None

# Legacy module-level references (kept in sync with active project + 'default' session
# for 100% backward compatibility with older tests and callers).
context_manager = ContextManager()
tool_executor = None
todo_manager = TodoManager() if TodoManager else None
mcp_manager = MCPManager() if MCPManager else None
orion_config = {"max_iterations": 20}

_synced_context_manager = context_manager
_synced_tool_executor = tool_executor
_synced_todo_manager = todo_manager
_synced_mcp_manager = mcp_manager
_synced_orion_config = orion_config

LAST_PROJECT_FILE = os.path.join(os.path.expanduser("~"), ".orion_last_project.json")

_project_states: dict[str, SessionState] = {}
_project_locks: dict[str, asyncio.Lock] = {}


def _get_or_create_project_context(project_path: str) -> ProjectContext:
    real_key = os.path.realpath(project_path)
    ctx = _project_contexts.get(real_key)
    if ctx is None:
        ctx = ProjectContext(
            project_path=project_path,
            real_path=real_key,
            orion_config={"max_iterations": 20},
            mcp_manager=MCPManager() if MCPManager else None,
        )
        _project_contexts[real_key] = ctx
    else:
        ctx.project_path = project_path
    return ctx


def _sync_legacy_globals(proj_ctx: ProjectContext) -> SessionContext:
    """Keep legacy module-level globals synced to the active project & 'default' session."""
    global _active_project_path, context_manager, tool_executor, todo_manager
    global mcp_manager, orion_config
    global _synced_context_manager, _synced_tool_executor, _synced_todo_manager
    global _synced_mcp_manager, _synced_orion_config

    _active_project_path = proj_ctx.project_path
    default_sess = proj_ctx.get_or_create_session("default")

    context_manager = default_sess.context_manager
    tool_executor = proj_ctx.tool_executor
    todo_manager = default_sess.todo_manager
    mcp_manager = proj_ctx.mcp_manager
    orion_config = proj_ctx.orion_config

    _synced_context_manager = context_manager
    _synced_tool_executor = tool_executor
    _synced_todo_manager = todo_manager
    _synced_mcp_manager = mcp_manager
    _synced_orion_config = orion_config

    _project_states[proj_ctx.real_path] = default_sess.state
    _project_locks[proj_ctx.real_path] = default_sess.lock
    return default_sess


def _is_legacy_active_path(project_path: str) -> bool:
    """True when project_path corresponds to the active legacy single-window project."""
    cm_path = getattr(context_manager, "project_path", None) if context_manager is not None else None
    if cm_path and os.path.realpath(cm_path) == os.path.realpath(project_path):
        return True
    if _active_project_path is None:
        return True
    return os.path.realpath(project_path) == os.path.realpath(_active_project_path)


def _resolve_project_context(project_path: str | None = None) -> ProjectContext | None:
    """Resolve explicit project_path or fall back strictly to context_manager.project_path."""
    target = project_path
    if target is None:
        if context_manager is not None and getattr(context_manager, "project_path", None):
            target = context_manager.project_path
    if not target:
        return None

    real_key = os.path.realpath(target)
    ctx = _project_contexts.get(real_key)
    if ctx is None:
        if not os.path.isdir(target):
            return None
        ctx = _get_or_create_project_context(target)
        if os.path.isfile(os.path.join(target, ".orion", "orion_config.json")):
            load_orion_config(target, target_config=ctx.orion_config)

    # Honor direct monkeypatching on module globals in legacy tests
    is_legacy_active = project_path is None or (
        context_manager is not None
        and getattr(context_manager, "project_path", None)
        and os.path.realpath(context_manager.project_path) == real_key
    )
    if is_legacy_active:
        if tool_executor is not _synced_tool_executor:
            ctx.tool_executor = tool_executor
        elif ctx.tool_executor is None and os.path.isdir(target):
            ctx.tool_executor = ToolExecutor(target)

        if mcp_manager is not _synced_mcp_manager:
            ctx.mcp_manager = mcp_manager

        if orion_config is not _synced_orion_config and isinstance(orion_config, dict):
            ctx.orion_config = orion_config

        if (
            context_manager is not _synced_context_manager
            and getattr(context_manager, "project_map", "")
            and not ctx.project_map
        ):
            ctx.project_map = context_manager.project_map

    return ctx


def _resolve_session_context(
    project_path: str | None = None, session_id: str | None = None
) -> tuple[ProjectContext | None, SessionContext | None]:
    """Resolve both ProjectContext and SessionContext for (project_path, session_id)."""
    proj_ctx = _resolve_project_context(project_path)
    if proj_ctx is None:
        return None, None

    sid = normalisasi_session_id(session_id)
    sess_ctx = proj_ctx.get_or_create_session(sid)

    if sid == "default":
        if proj_ctx.real_path in _project_states:
            sess_ctx.state = _project_states[proj_ctx.real_path]
        else:
            sess_ctx.state = SessionState.muat_checkpoint(
                proj_ctx.project_path, session_id="default"
            )
            _project_states[proj_ctx.real_path] = sess_ctx.state

        if proj_ctx.real_path in _project_locks:
            sess_ctx.lock = _project_locks[proj_ctx.real_path]
        else:
            _project_locks[proj_ctx.real_path] = sess_ctx.lock

    is_legacy_default = sid == "default" and (
        project_path is None
        or (
            context_manager is not None
            and getattr(context_manager, "project_path", None)
            and os.path.realpath(context_manager.project_path) == proj_ctx.real_path
        )
    )
    if is_legacy_default:
        if context_manager is not _synced_context_manager and context_manager is not None:
            sess_ctx.context_manager = context_manager
        if todo_manager is not _synced_todo_manager:
            sess_ctx.todo_manager = todo_manager

    sess_ctx.touch()
    return proj_ctx, sess_ctx


def _get_project_lock(project_path: str, session_id: str | None = "default") -> asyncio.Lock:
    proj_ctx, sess_ctx = _resolve_session_context(project_path, session_id)
    if sess_ctx is not None:
        return sess_ctx.lock
    key = os.path.realpath(project_path)
    lock = _project_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _project_locks[key] = lock
    return lock


def _get_or_restore_state(
    project_path: str, pending_image=None, session_id: str | None = "default"
) -> SessionState:
    sid = normalisasi_session_id(session_id)
    real_key = os.path.realpath(project_path)
    existing_proj = _project_contexts.get(real_key)
    if sid == "default":
        already_active = real_key in _project_states
    else:
        already_active = existing_proj is not None and sid in existing_proj.sessions

    _, sess_ctx = _resolve_session_context(project_path, sid)
    if sess_ctx is not None:
        state = sess_ctx.state
        if already_active:
            state.reset_per_turn()
        state.pending_image = pending_image
        return state

    state = _project_states.get(real_key)
    if state is None:
        state = SessionState.muat_checkpoint(project_path, session_id=sid)
        _project_states[real_key] = state
    else:
        state.reset_per_turn()
    state.pending_image = pending_image
    return state


def save_last_project(path: str):
    """Save the last project path to disk."""
    try:
        _atomic_write_json(LAST_PROJECT_FILE, {"path": path})
        print(f"[ORION PERSIST] Project path saved to {LAST_PROJECT_FILE}")
    except Exception as e:
        print(f"[ORION PERSIST WARNING] Failed to save last project: {e}")


def load_last_project():
    """Load the last project path from disk."""
    try:
        if os.path.exists(LAST_PROJECT_FILE):
            with open(LAST_PROJECT_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("path")
    except Exception as e:
        print(f"[ORION PERSIST WARNING] Failed to load last project: {e}")
    return None


def load_orion_config(project_path: str, target_config: dict | None = None) -> dict:
    """Read or create the default config file at .orion/orion_config.json."""
    global orion_config
    config_path = os.path.join(project_path, ".orion", "orion_config.json")
    base_cfg = target_config if target_config is not None else {"max_iterations": 20}

    print(f"[ORION CONFIG DEBUG] Attempting to read config from: {config_path}")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                loaded_config = json.load(f)
                merged = {**base_cfg, **loaded_config}
                if target_config is not None:
                    target_config.clear()
                    target_config.update(merged)
                if target_config is None or _is_legacy_active_path(project_path):
                    orion_config = target_config if target_config is not None else merged
                print(f"[ORION CONFIG] ✅ Loaded successfully: {merged}")
                print(
                    f"[ORION CONFIG] max_iterations value: {merged.get('max_iterations')}"
                )
                return target_config if target_config is not None else merged
        except Exception as e:
            print(
                f"[ORION CONFIG WARNING] Failed to read config, using defaults. Error: {e}"
            )
            return base_cfg
    else:
        default_config = _config_defaults()
        try:
            _atomic_write_json(config_path, default_config)
            if target_config is not None:
                target_config.clear()
                target_config.update(default_config)
            if (
                target_config is None
                or _active_project_path is None
                or os.path.realpath(project_path)
                == os.path.realpath(_active_project_path)
            ):
                orion_config = (
                    target_config if target_config is not None else default_config
                )
            print(f"[ORION CONFIG] Default config file created at {config_path}")
            return target_config if target_config is not None else default_config
        except Exception as e:
            print(f"[ORION CONFIG WARNING] Failed to create config file: {e}")
            return base_cfg


def periksa_konsistensi_tools() -> None:
    """Compare the TOOLS schema against the dispatch list. Log a warning when they diverge."""
    dideklarasikan = set()
    for t in TOOLS:
        nama = (t.get("function") or {}).get("name")
        if nama:
            dideklarasikan.add(nama)

    mati = dideklarasikan - tool_dispatch.NAMA_TOOL_AKTIF
    berlebih = tool_dispatch.NAMA_TOOL_AKTIF - dideklarasikan

    if mati:
        logger.error(
            "[TOOLS] %d tools DECLARED but have NO dispatch branch in /chat: %s. "
            "The model will always receive 'tool is NOT AVAILABLE'. "
            "Add an `elif func_name == ...` branch in generate_stream().",
            len(mati),
            sorted(mati),
        )
    if berlebih:
        logger.warning(
            "[TOOLS] %d tools have dispatch but are NOT declared in the TOOLS schema: %s. "
            "The model will never call them.",
            len(berlebih),
            sorted(berlebih),
        )
    if not mati and not berlebih:
        logger.info(
            "[TOOLS] Consistent: %d tools declared & dispatched.", len(dideklarasikan)
        )


periksa_konsistensi_tools()


class ProjectPathRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    message: str
    attachments: list = []
    editor: dict = {}
    project_path: str | None = None
    session_id: str | None = None


class ClearChatRequest(BaseModel):
    project_path: str | None = None
    session_id: str | None = None


@app.get("/")
async def root():
    return {
        "status": "Orion Backend is running!",
        "version": runtime_id.BACKEND_VERSION,
    }


@app.get("/health")
async def health(project_path: str | None = None):
    proj_ctx = _resolve_project_context(project_path)
    resolved_path = proj_ctx.project_path if proj_ctx else context_manager.project_path
    active_mcp = proj_ctx.mcp_manager if proj_ctx else mcp_manager

    payload = runtime_id.health_payload(resolved_path)
    if active_mcp is not None:
        payload.update(active_mcp.health_fields())
    return payload


@app.get("/runtime/forensics")
async def runtime_forensics(project_path: str | None = None):
    proj_ctx = _resolve_project_context(project_path)
    resolved_path = proj_ctx.project_path if proj_ctx else context_manager.project_path
    return runtime_id.forensics_payload(resolved_path)


class McpConfigRequest(BaseModel):
    op: str
    name: str | None = None
    spec: dict | None = None
    disabled: bool | None = None
    servers: dict | None = None
    keep_env: bool | None = None
    enabled: bool | None = None
    project_path: str | None = None


@app.get("/mcp/status")
async def mcp_status(project_path: str | None = None):
    proj_ctx = _resolve_project_context(project_path)
    active_mcp = proj_ctx.mcp_manager if proj_ctx else mcp_manager
    if active_mcp is None:
        return JSONResponse(
            {"error": "MCP support is unavailable in this backend"},
            status_code=503,
        )
    return active_mcp.status_detail()


@app.post("/mcp/config")
async def mcp_config(request: McpConfigRequest):
    proj_ctx = _resolve_project_context(request.project_path)
    active_mcp = proj_ctx.mcp_manager if proj_ctx else mcp_manager
    if active_mcp is None:
        return JSONResponse(
            {"error": "MCP support is unavailable in this backend"},
            status_code=503,
        )
    op_payload = {
        "name": request.name,
        "spec": request.spec,
        "disabled": request.disabled,
        "servers": request.servers,
        "keep_env": request.keep_env,
        "enabled": request.enabled,
    }
    if proj_ctx is not None:
        async with proj_ctx.project_lock:
            result = await active_mcp.apply_config_op(request.op, op_payload)
            if result["success"] and request.op == "master_switch" and isinstance(request.enabled, bool):
                proj_ctx.orion_config["mcp_enabled"] = request.enabled
    else:
        result = await active_mcp.apply_config_op(request.op, op_payload)
    if not result["success"]:
        return JSONResponse(result, status_code=400)
    return result


SUBAGENT_ROLE_KEYS = ("explorer", "verifier", "workers")
PROFILE_GROUP_PATTERN = re.compile(r"^(gemini|provider_[1-9][0-9]?|fallback)$")


class ModelsTestRequest(BaseModel):
    group: str
    model: str


class ModelsConfigRequest(BaseModel):
    subagent_models: dict | None = None
    project_path: str | None = None


def _orion_config_path(project_path: str | None = None) -> str | None:
    proj_ctx = _resolve_project_context(project_path)
    resolved = proj_ctx.project_path if proj_ctx else context_manager.project_path
    if not resolved:
        return None
    return os.path.join(resolved, ".orion", "orion_config.json")


def validate_subagent_models(payload) -> str | None:
    """Return an error string when the payload is not a valid profile map."""
    if not isinstance(payload, dict):
        return "subagent_models must be an object mapping role -> profile."
    for role, entry in payload.items():
        if role not in SUBAGENT_ROLE_KEYS:
            return (
                f"unknown role '{role}'; allowed roles: "
                f"{', '.join(SUBAGENT_ROLE_KEYS)}."
            )
        if entry is None:
            continue
        if not isinstance(entry, dict):
            return f"role '{role}' must be null or an object {{group, model}}."
        group = entry.get("group")
        model = entry.get("model")
        if not isinstance(group, str) or not PROFILE_GROUP_PATTERN.match(group.strip()):
            return (
                f"role '{role}': group must be 'gemini', 'provider_<N>' or "
                f"'fallback' (got {group!r})."
            )
        if not isinstance(model, str) or not model.strip():
            return f"role '{role}': model must be a non-empty string."
        extra = set(entry) - {"group", "model"}
        if extra:
            return f"role '{role}': unexpected field(s) {sorted(extra)}."
    return None


def _sanitize_profile_error(text: str, queue: list) -> str:
    """Strip credential values out of a provider error before it leaves."""
    for target in queue:
        key = str(target.get("api_key") or "")
        if key and key in text:
            text = text.replace(key, "***")
    return text[:300]


@app.get("/models/options")
async def models_options_route(project_path: str | None = None):
    """Groups + models offered by this environment, and the saved profiles."""
    saved = {}
    cfg_path = _orion_config_path(project_path)
    if cfg_path and os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            candidate = (
                loaded.get("subagent_models") if isinstance(loaded, dict) else None
            )
            if isinstance(candidate, dict):
                saved = candidate
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ORION MODELS WARNING] could not read orion_config.json: {e}")
    return {
        "roles": list(SUBAGENT_ROLE_KEYS),
        "groups": model_options(),
        "subagent_models": saved,
    }


@app.post("/models/test")
async def models_test_route(request: ModelsTestRequest):
    """Probe one group+model with a tiny prompt. Never raises a 500."""
    profile = {"group": request.group, "model": request.model}
    queue = build_profile_queue(profile)
    if not queue:
        return {
            "success": False,
            "profile": profile,
            "error": (
                f"no credentials found for group '{request.group}' in this "
                "backend's environment (or the group/model name is empty)."
            ),
        }
    try:
        response = await get_ai_response(
            [{"role": "user", "content": "Reply with the single word: pong"}],
            tools=None,
            profile=profile,
        )
        content = ""
        choices = getattr(response, "choices", None)
        if choices:
            content = str(getattr(choices[0].message, "content", "") or "")
        return {
            "success": True,
            "profile": profile,
            "resolved_model": queue[0]["model"],
            "reply_preview": content[:120],
        }
    except Exception as exc:
        return {
            "success": False,
            "profile": profile,
            "resolved_model": queue[0]["model"],
            "error": _sanitize_profile_error(f"{type(exc).__name__}: {exc}", queue),
        }


@app.post("/models/config")
async def models_config_route(request: ModelsConfigRequest):
    """Validate -> atomic write -> live sync of subagent_models."""
    proj_ctx = _resolve_project_context(request.project_path)
    cfg_path = _orion_config_path(request.project_path)
    if cfg_path is None:
        return JSONResponse(
            {"success": False, "error": "No project is set; open a folder first."},
            status_code=400,
        )

    payload = request.subagent_models if request.subagent_models is not None else {}
    error = validate_subagent_models(payload)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)

    async def _apply_models_cfg():
        data = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError) as e:
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"orion_config.json is unreadable/corrupt: {e}",
                    },
                    status_code=400,
                )

        data["subagent_models"] = payload
        try:
            _atomic_write_json(cfg_path, data)
        except OSError as e:
            return JSONResponse(
                {"success": False, "error": f"could not write orion_config.json: {e}"},
                status_code=500,
            )

        if proj_ctx is not None:
            proj_ctx.orion_config["subagent_models"] = payload
            if _is_legacy_active_path(proj_ctx.project_path):
                orion_config["subagent_models"] = payload
        else:
            orion_config["subagent_models"] = payload
        print(f"[ORION MODELS] subagent_models saved: {payload}")
        return {"success": True, "subagent_models": payload}

    if proj_ctx is not None:
        async with proj_ctx.project_lock:
            return await _apply_models_cfg()
    return await _apply_models_cfg()


CONFIG_RANGES = {
    "max_iterations": (1, 200),
    "token_budget_threshold": (1000, 200000),
    "subagent_max_per_turn": (1, 10),
    "subagent_max_iterations": (1, 30),
    "subagent_workers_max": (1, 6),
    "inbox_max_files": (1, 20),
}
CONFIG_BOOLEANS = (
    "subagents_enabled",
    "inbox_enabled",
    "mcp_enabled",
    "semantic_auto_index",
)
CONFIG_OTHER = (
    "subagent_models",
    "description",
)


def _config_defaults() -> dict:
    """The same defaults load_orion_config() writes on first touch."""
    return {
        "max_iterations": 20,
        "token_budget_threshold": 12000,
        "subagents_enabled": True,
    "strict_worker_coding": True,
        "subagent_max_per_turn": 3,
        "subagent_max_iterations": 6,
        "subagent_workers_max": 3,
        "mcp_enabled": True,
        "inbox_enabled": True,
        "inbox_max_files": 5,
        "subagent_models": {},
        "description": (
            "Orion settings. Change max_iterations to limit the AI thinking "
            "loop. token_budget_threshold controls when emergency context "
            "pruning occurs. subagents_* control the internal sub-agents "
            "(UPDATE-016). inbox_* control the .orion/inbox drop-zone; "
            "subagent_models pins per-role models (UPDATE-018)."
        ),
        "semantic_auto_index": True,
    }


def _env_summary() -> dict:
    """Key-COUNT-only view of .env for the Model tab."""
    gemini_count = 0
    for name, value in os.environ.items():
        if name.startswith("GEMINI_API_KEY_") and value and value.strip():
            gemini_count += 1

    providers = []
    pattern = re.compile(
        r"^PROVIDER_([1-9][0-9]?)_(NAME|API_KEY|BASE_URL|MODELS|TEMPERATURE)$"
    )
    slots: dict = {}
    for name, value in os.environ.items():
        m = pattern.match(name)
        if not m:
            continue
        idx = int(m.group(1))
        field_name = m.group(2).lower()
        val = (value or "").strip()
        slots.setdefault(idx, {})[field_name] = val
    for idx in sorted(slots):
        slot = slots[idx]
        if not slot.get("api_key"):
            continue
        models = [m.strip() for m in slot.get("models", "").split(",") if m.strip()]
        providers.append(
            {
                "n": idx,
                "name": slot.get("name") or f"Provider {idx}",
                "base_url": slot.get("base_url", ""),
                "models": models,
            }
        )

    fallback_has = bool(os.environ.get("FALLBACK_API_KEY", "").strip())
    fallback_models = [
        m.strip() for m in os.environ.get("FALLBACK_MODELS", "").split(",") if m.strip()
    ]
    cf_has = bool(
        os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        and os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    )
    cf_models = [
        m.strip()
        for m in os.environ.get("CLOUDFLARE_MODELS", "").split(",")
        if m.strip()
    ]

    return {
        "gemini_keys_count": gemini_count,
        "default_model": os.environ.get("DEFAULT_MODEL", "").strip(),
        "gemini_disabled": os.environ.get("GEMINI_DISABLED", "").strip().lower()
        in ("1", "true", "yes", "on"),
        "router_fallback": os.environ.get("ROUTER_FALLBACK", "").strip().lower()
        or "none",
        "providers": providers,
        "fallback": {"has_key": fallback_has, "models": fallback_models},
        "cloudflare": {"has_credentials": cf_has, "models": cf_models},
    }


def validate_orion_config_payload(payload) -> str | None:
    """Return an error string when the incoming orion_config is malformed."""
    if not isinstance(payload, dict):
        return "orion_config must be an object."

    for key, (lo, hi) in CONFIG_RANGES.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{key}' must be an integer."
        if not (lo <= value <= hi):
            return f"'{key}' must be between {lo} and {hi} (got {value})."

    for key in CONFIG_BOOLEANS:
        if key not in payload:
            continue
        if not isinstance(payload[key], bool):
            return f"'{key}' must be true or false."

    if "subagent_models" in payload:
        err = validate_subagent_models(payload["subagent_models"])
        if err:
            return err

    if "description" in payload and not isinstance(payload["description"], str):
        return "'description' must be a string."

    allowed = set(CONFIG_RANGES) | set(CONFIG_BOOLEANS) | set(CONFIG_OTHER)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return f"unknown key(s) {unknown}; allowed: {sorted(allowed)}."
    return None


@app.get("/config")
async def get_config(project_path: str | None = None):
    """Current orion_config + defaults + ranges + safe env summary."""
    proj_ctx = _resolve_project_context(project_path)
    resolved_path = proj_ctx.project_path if proj_ctx else context_manager.project_path
    cfg_path = (
        os.path.join(resolved_path, ".orion", "orion_config.json")
        if resolved_path
        else None
    )
    data: dict = {}
    if cfg_path and os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ORION CONFIG WARNING] GET /config could not read file: {e}")

    return {
        "project_path": resolved_path,
        "config_path": cfg_path,
        "orion_config": data,
        "defaults": _config_defaults(),
        "ranges": {k: list(v) for k, v in CONFIG_RANGES.items()},
        "env_summary": _env_summary(),
    }


@app.post("/config")
async def post_config(request: dict):
    """Validate -> atomic write -> live sync of orion_config."""
    req_proj = request.get("project_path") if isinstance(request, dict) else None
    proj_ctx = _resolve_project_context(req_proj)
    resolved_path = proj_ctx.project_path if proj_ctx else context_manager.project_path
    if not resolved_path:
        return JSONResponse(
            {"success": False, "error": "No project is set; open a folder first."},
            status_code=400,
        )

    payload = request.get("orion_config") if isinstance(request, dict) else None
    error = validate_orion_config_payload(payload)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)

    cfg_path = os.path.join(resolved_path, ".orion", "orion_config.json")

    async def _apply_cfg():
        on_disk: dict = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    on_disk = loaded
            except (OSError, json.JSONDecodeError) as e:
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"orion_config.json is unreadable/corrupt: {e}",
                    },
                    status_code=400,
                )

        merged = {**on_disk, **payload}

        try:
            _atomic_write_json(cfg_path, merged, trailing_newline=True)
        except OSError as e:
            return JSONResponse(
                {"success": False, "error": f"could not write orion_config.json: {e}"},
                status_code=500,
            )

        if proj_ctx is not None:
            proj_ctx.orion_config.update(merged)
            if _is_legacy_active_path(proj_ctx.project_path):
                orion_config.update(merged)
            active_cfg = dict(proj_ctx.orion_config)
        else:
            orion_config.update(merged)
            active_cfg = dict(orion_config)
        print(f"[ORION CONFIG] saved via /config: {sorted(payload)}")
        return {
            "success": True,
            "orion_config": active_cfg,
        }

    if proj_ctx is not None:
        async with proj_ctx.project_lock:
            return await _apply_cfg()
    return await _apply_cfg()


@app.post("/set-project")
async def set_project(request: ProjectPathRequest):
    if not os.path.isdir(request.path):
        return {"success": False, "error": "Folder not found."}

    proj_ctx = _get_or_create_project_context(request.path)

    async with proj_ctx.project_lock:
        # 1. Validate the folder structure
        structure_warnings = validate_project_structure(request.path)

        # 2. Initialize project-scoped ToolExecutor & default session
        proj_ctx.tool_executor = ToolExecutor(request.path)
        try:
            if hasattr(proj_ctx.tool_executor, "_semantic_engine"):
                proj_ctx.tool_executor._semantic_engine = None
        except Exception:
            pass

        default_sess = proj_ctx.get_or_create_session("default")
        default_sess.context_manager.set_project_path(request.path, session_id="default")
        default_sess.state = SessionState.muat_checkpoint(
            request.path, session_id="default"
        )
        if default_sess.todo_manager:
            default_sess.todo_manager.set_project_path(
                request.path, session_id="default"
            )

        # 3. Save to disk for auto-restore
        save_last_project(request.path)

        # 4. Initialize the .orion/ folder
        orion_dir = os.path.join(request.path, ".orion")
        if not os.path.exists(orion_dir):
            os.makedirs(orion_dir)
            print(f"[ORION INIT] Creating .orion/ folder at {orion_dir}")

            default_files = {
                "master_context.md": "# Master Context\n\nProject: [Project Name]\nVersion: 0.1\nTechnology: \n",
                "decision_log.md": "# Decision Log\n\nNo architecture decisions yet.\n",
                "coding_rules.md": "# Coding Rules\n\nNo coding rules yet.\n",
                "current_sprint.md": "# Current Sprint\n\nSprint: Initialization\nStatus: Just started\n",
            }
            for filename, content in default_files.items():
                file_path = os.path.join(orion_dir, filename)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

        # 5. Scan dependencies and save to project_map
        try:
            scanner = DependencyScanner(request.path)
            scan_result = scanner.scan()
            print(
                f"[ORION INIT] Dependency Map generated. Total {scan_result['total_files']} files scanned."
            )

            map_lines = [f"📁 {path}" for path in scan_result["files"].keys()]
            project_map_str = "Project Structure:\n" + "\n".join(map_lines)
            if scan_result.get("truncated"):
                note = (
                    f"⚠️ PROJECT MAP TRUNCATED: showing {scan_result['total_files']} files "
                    f"(scanner cap {scan_result.get('file_cap')}). The project contains more "
                    "scannable files than Orion maps at once; explore beyond this list with "
                    "search_code / read_file_range instead of assuming the map is complete."
                )
                project_map_str = note + "\n" + project_map_str
                structure_warnings.append(note)
                print(f"[SCANNER WARNING] {note}")
            proj_ctx.project_map = project_map_str
            for _s in proj_ctx.sessions.values():
                _s.context_manager.set_project_map(project_map_str)

        except Exception as e:
            print(f"[ORION INIT WARNING] Failed to generate Dependency Map: {e}")
            proj_ctx.project_map = "Project Structure: Scan failed."
            for _s in proj_ctx.sessions.values():
                _s.context_manager.set_project_map(proj_ctx.project_map)

        # 6. Load project-scoped config
        load_orion_config(request.path, target_config=proj_ctx.orion_config)

        # 7. Sync legacy globals for single-window / older test compatibility
        _sync_legacy_globals(proj_ctx)

        # 8. (Re)start MCP servers ONLY for THIS project (other open projects stay alive)
        mcp_summary = None
        if proj_ctx.mcp_manager is not None:
            mcp_summary = await proj_ctx.mcp_manager.start_all(request.path)
            for _w in mcp_summary.get("warnings", []):
                print(f"[ORION MCP WARNING] {_w}")
            for _srv in mcp_summary.get("servers", []):
                if _srv.get("status") == "ok":
                    print(
                        f"[ORION MCP] server '{_srv['name']}' ready with {_srv['tools']} tool(s)"
                    )
                elif _srv.get("status") == "error":
                    print(
                        f"[ORION MCP] server '{_srv['name']}' FAILED to start: {_srv.get('error')}"
                    )

        asyncio.create_task(
            _trigger_semantic_auto_index("set-project", proj_ctx=proj_ctx)
        )
        return {
            "success": True,
            "message": "Project scanned successfully.",
            "project_map_preview": proj_ctx.project_map[:500] + "...",
            "structure_warnings": structure_warnings,
            "mcp": mcp_summary,
        }


_semantic_indexing_projects: set[str] = set()


async def _trigger_semantic_auto_index(
    reason: str = "set-project", proj_ctx: ProjectContext | None = None
):
    cfg = proj_ctx.orion_config if proj_ctx is not None else orion_config
    if not cfg.get("semantic_auto_index", True):
        return
    executor = proj_ctx.tool_executor if proj_ctx is not None else tool_executor
    if executor is None:
        return
    proj_key = os.path.realpath(executor.project_path)
    if proj_key in _semantic_indexing_projects:
        return
    _semantic_indexing_projects.add(proj_key)
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, executor.index_semantic_search)
        logger.info("[SEMANTIC AUTO-INDEX] (%s) selesai: %s", reason, str(res)[:120])
    except Exception as e:
        logger.warning("[SEMANTIC AUTO-INDEX] (%s) gagal: %s", reason, e)
    finally:
        _semantic_indexing_projects.discard(proj_key)


async def auto_restore_project():
    """Auto-restore the last project when the backend starts up."""
    auth.get_shared_token()
    print(
        f"[ORION AUTH] Shared token file: {auth.token_file_path()} — "
        "X-Orion-Token required on all routes except GET /"
    )
    last_path = load_last_project()
    if last_path and os.path.isdir(last_path):
        print(f"[ORION STARTUP] 🔄 Auto-restoring previous project: {last_path}")
        proj_ctx = _get_or_create_project_context(last_path)
        proj_ctx.tool_executor = ToolExecutor(last_path)
        default_sess = proj_ctx.get_or_create_session("default")
        default_sess.context_manager.set_project_path(last_path, session_id="default")
        if default_sess.todo_manager:
            default_sess.todo_manager.set_project_path(last_path, session_id="default")
        load_orion_config(last_path, target_config=proj_ctx.orion_config)
        _sync_legacy_globals(proj_ctx)
        if proj_ctx.mcp_manager is not None:
            try:
                _ringkasan_mcp = await proj_ctx.mcp_manager.start_all(last_path)
                for _w in _ringkasan_mcp.get("warnings", []):
                    print(f"[ORION MCP WARNING] {_w}")
            except Exception as e:
                print(f"[ORION MCP WARNING] MCP startup skipped: {e}")
        asyncio.create_task(
            _trigger_semantic_auto_index("auto-restore", proj_ctx=proj_ctx)
        )
        print("[ORION STARTUP] ✅ Project restored successfully. Tool executor ready.")
    else:
        print("[ORION STARTUP] No previous project found. Waiting for /set-project...")


@app.post("/chat")
async def chat_with_orion(request: ChatRequest):
    proj_ctx, sess_ctx = _resolve_session_context(
        request.project_path, request.session_id
    )
    active_cm = sess_ctx.context_manager if sess_ctx else context_manager
    active_executor = proj_ctx.tool_executor if proj_ctx else tool_executor
    active_map = (
        (proj_ctx.project_map if proj_ctx else "")
        or getattr(active_cm, "project_map", "")
    )

    if not active_map or active_executor is None or proj_ctx is None or sess_ctx is None:

        async def error_gen():
            yield sse.ev("error", text="Please set a project first.")

        return StreamingResponse(
            stream_utils.jamin_done(error_gen(), logger=logger),
            media_type="text/event-stream",
        )

    from app.services.attachments import build_user_content, describe_attachments

    _lamp_desc = describe_attachments(request.attachments)
    _content_parts, _riwayat_text = build_user_content(
        request.message,
        request.attachments,
        project_path=proj_ctx.project_path,
    )
    if _lamp_desc:
        logger.info("[ATTACHMENT] %s", _lamp_desc)

    async def generate_stream():
        proj_path = proj_ctx.project_path
        sid = sess_ctx.session_id

        async with sess_ctx.lock:
            sess_ctx.touch()
            state = sess_ctx.state
            state.reset_per_turn()
            state.pending_image = (
                _content_parts if isinstance(_content_parts, list) else None
            )

            if inbox_manager is not None:
                _inbox = inbox_manager.ingest(proj_path, proj_ctx.orion_config)
                _inbox_desc = inbox_manager.describe(_inbox)
                _inbox_block = inbox_manager.context_block(_inbox)
            else:
                _inbox, _inbox_desc, _inbox_block = {"names": [], "images": []}, "", ""

            _inbox_riwayat = "".join(
                f"\n[INBOX FILE: {_n} — processed this turn; archived copy under "
                ".orion/inbox/archive/]"
                for _n in _inbox.get("names", [])
            )
            if _inbox.get("names"):
                logger.info("[INBOX] %s", _inbox_desc)
                for _w in _inbox.get("warnings", []):
                    logger.warning("[INBOX] %s", _w)

            if _inbox.get("images"):
                _bagian = (
                    list(state.pending_image)
                    if isinstance(state.pending_image, list)
                    else []
                )
                if not _bagian:
                    _bagian = [{"type": "text", "text": "(see attached images)"}]
                if not any(
                    isinstance(_p, dict) and _p.get("type") == "text" for _p in _bagian
                ):
                    _bagian.insert(
                        0,
                        {
                            "type": "text",
                            "text": (_riwayat_text + _inbox_riwayat)
                            or "(see the inbox images)",
                        },
                    )
                for _nama, _mime, _b64 in _inbox["images"]:
                    _bagian.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{_mime};base64,{_b64}"},
                        }
                    )
                state.pending_image = _bagian

            sess_cm = sess_ctx.context_manager
            sess_tm = sess_ctx.todo_manager
            sess_cm.add_message("user", _riwayat_text + _inbox_riwayat)

            _editor_ctx = request.editor if isinstance(request.editor, dict) else {}
            _editor_block = ""

            _aktif = str(_editor_ctx.get("activeFile") or "")
            if _aktif and not _aktif.startswith(".."):
                _editor_block += f"\n\nFILE THE USER CURRENTLY HAS OPEN: {_aktif}"

            _sel = str(_editor_ctx.get("selection") or "")
            if _sel:
                _editor_block += (
                    f"\n\nTEXT THE USER CURRENTLY HAS SELECTED IN THAT FILE:\n```\n{_sel[:4000]}\n```"
                )

            _diags = _editor_ctx.get("diagnostics") or []
            if isinstance(_diags, list) and _diags:
                _editor_block += (
                    "\n\nLATEST DIAGNOSTICS FROM VS CODE (Problems panel). "
                    "These are FACTS about the current state of the code, more accurate than your guesses:\n"
                    + "\n".join(str(d) for d in _diags[:30])
                    + "\n\nRULE: if you just changed a file and the list above "
                    "mentions an ERROR in that file, your change is BROKEN or incomplete. "
                    "Fix it first until the error is gone BEFORE reporting the task complete. "
                    "If the error is in a file you did not touch, mention it to the user as a finding, "
                    "do not silently ignore it."
                )

            _prev_changed = getattr(state, "_checkpoint_file_terakhir", []) or []
            _resume_block = ""
            if _prev_changed or state.analyzed_files:
                _parts_cp = ["\n\n[SESSION CHECKPOINT — PRESERVED ACROSS PAUSE]"]
                if _prev_changed:
                    _parts_cp.append(
                        "Files already written/edited in the previous turn (do NOT redo unless fixing an error): "
                        + ", ".join(_prev_changed[-15:])
                    )
                if state.analyzed_files:
                    _parts_cp.append(
                        f"Files already read in this session ({len(state.analyzed_files)} total, e.g. "
                        + ", ".join(sorted(state.analyzed_files)[:10])
                        + ") — skip re-reading unchanged files."
                    )
                _resume_block = "\n".join(_parts_cp)

            current_map = (
                proj_ctx.project_map
                or getattr(sess_cm, "project_map", "")
                or active_map
            )
            context_message = {
                "role": "user",
                "content": f"Current project structure:\n{current_map}"
                + _editor_block
                + _inbox_block
                + findings_ledger.blok_konteks(proj_path)
                + blok_jangkar_tugas(
                    sess_cm.get_history(),
                    sess_tm,
                    state.utang_verifikasi_terbuka(),
                )
                + _resume_block,
            }

            deps = Deps(
                context_manager=sess_cm,
                todo_manager=sess_tm,
                tool_executor=proj_ctx.tool_executor,
                llm_call=get_ai_response,
                kirim_status=lambda teks: sse.ev("status", text=teks),
                config=proj_ctx.orion_config,
                logger=logger,
                context_message=context_message,
                lampiran_desc=_lamp_desc,
                mcp=proj_ctx.mcp_manager,
                inbox_desc=_inbox_desc,
            )

            try:
                async for potongan in stream_utils.jamin_done(
                    aliran_agent(state, deps), logger=logger
                ):
                    yield potongan
            finally:
                state._checkpoint_file_terakhir = list(state.files_changed_this_turn)[
                    -30:
                ]
                state.simpan_checkpoint(proj_path, session_id=sid)
                sess_ctx.touch()

    return StreamingResponse(generate_stream(), media_type="text/event-stream")


@app.post("/clear-chat")
async def clear_chat(request: ClearChatRequest | None = None):
    req_proj = request.project_path if request else None
    req_sid = request.session_id if request else None
    proj_ctx, sess_ctx = _resolve_session_context(req_proj, req_sid)

    if proj_ctx is not None and sess_ctx is not None:
        async with sess_ctx.lock:
            sid = sess_ctx.session_id
            sess_ctx.context_manager.clear_history(persist=True)
            SessionState.hapus_checkpoint(proj_ctx.project_path, session_id=sid)
            fresh_state = SessionState(session_id=sid)
            sess_ctx.state = fresh_state
            sess_ctx.touch()
            if sid == "default":
                _project_states.pop(proj_ctx.real_path, None)
        return {
            "success": True,
            "message": "Chat history cleared.",
            "session_id": sid,
        }

    context_manager.clear_history(persist=True)
    if context_manager.project_path:
        key = os.path.realpath(context_manager.project_path)
        _project_states.pop(key, None)
        SessionState.hapus_checkpoint(context_manager.project_path, session_id="default")
    return {"success": True, "message": "Chat history cleared.", "session_id": "default"}


class TerminalRequest(BaseModel):
    command: str
    project_path: str | None = None
    session_id: str | None = None


@app.post("/execute-terminal")
async def execute_approved_terminal(request: TerminalRequest):
    """Dedicated endpoint for executing a command the user already approved."""
    proj_ctx, sess_ctx = _resolve_session_context(
        request.project_path, request.session_id
    )
    active_executor = proj_ctx.tool_executor if proj_ctx else tool_executor
    if active_executor is None:
        return {"success": False, "output": "Tool executor not initialized."}

    async def _run_and_record():
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None, lambda: active_executor.run_terminal(command=request.command)
        )

        active_cm = sess_ctx.context_manager if sess_ctx else context_manager
        history = active_cm.get_history()
        for i in range(len(history) - 1, -1, -1):
            msg = history[i]
            if msg.get("role") == "tool" and "[AWAITING CONFIRMATION]" in msg.get(
                "content", ""
            ):
                history[i]["content"] = output
                print(
                    f"[ORION] Dangerous command executed. Tool call ID {msg.get('tool_call_id')} updated."
                )
                if hasattr(active_cm, "save_history"):
                    active_cm.save_history()
                break
        if sess_ctx is not None:
            sess_ctx.touch()
        return {"success": True, "output": output}

    if sess_ctx is not None:
        async with sess_ctx.lock:
            return await _run_and_record()
    return await _run_and_record()


@app.get("/performance")
async def get_performance(project_path: str | None = None):
    """Read and compute statistics from performance_log.jsonl."""
    proj_ctx = _resolve_project_context(project_path)
    active_executor = proj_ctx.tool_executor if proj_ctx else tool_executor
    if active_executor is None:
        return {"error": "Project not set yet."}

    log_file = os.path.join(
        active_executor.project_path, ".orion", "performance_log.jsonl"
    )

    if not os.path.exists(log_file):
        return {
            "summary": {
                "total_requests": 0,
                "total_cost_usd": 0,
                "avg_response_time_ms": 0,
            },
            "recent_logs": [],
        }

    logs = []
    total_cost = 0.0
    total_duration = 0.0
    success_count = 0
    error_count = 0

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        logs.append(entry)
                        total_cost += entry.get("estimated_cost_usd", 0)
                        total_duration += entry.get("duration_ms", 0)
                        if entry.get("status") == "success":
                            success_count += 1
                        else:
                            error_count += 1
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
    except OSError as e:
        return {"error": f"Failed to read log: {e!s}"}

    total_requests = len(logs)
    avg_duration = total_duration / total_requests if total_requests > 0 else 0

    return {
        "summary": {
            "total_requests": total_requests,
            "total_input_tokens": sum(l.get("input_tokens", 0) for l in logs),
            "total_cached_tokens": sum(l.get("cached_tokens", 0) for l in logs),
            "total_output_tokens": sum(l.get("output_tokens", 0) for l in logs),
            "total_cost_usd": round(total_cost, 6),
            "avg_response_time_ms": round(avg_duration, 2),
            "success_count": success_count,
            "error_count": error_count,
        },
        "recent_logs": logs[-10:],
    }
