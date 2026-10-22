import os
import sys
from pathlib import Path


def _resolve_stdout_log() -> Path:
    """Decide where every print()/stdout line of the backend goes.

    >>> UPDATE-012: the file moved from ``~/orion_stdout.log`` into the
    project so BOTH logs live side by side and several projects no longer
    mix their output in one home-directory file.

    Priority:
      1. ``$ORION_STDOUT_LOG`` — explicit override (tests / debugging).
      2. ``<project>/.orion/logs/orion_stdout.log`` — deterministic via
         ``__file__``: this file is ``<project>/backend/app/main.py``, so
         ``parents[2]`` is the project root. Same folder as ``backend.log``
         (see ``logging_setup._cari_folder_log``).
      3. ``~/orion_stdout.log`` — the historical location, used only when
         the project folder cannot be created (read-only install, exotic
         layout). Old copies are never deleted; the backend simply stops
         writing to them.
    """
    env = os.getenv("ORION_STDOUT_LOG")
    if env:
        return Path(env).expanduser()
    try:
        target = (
            Path(__file__).resolve().parents[2]
            / ".orion" / "logs" / "orion_stdout.log"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        return Path.home() / "orion_stdout.log"


_STDOUT_LOG_PATH = _resolve_stdout_log()


def _open_stdout_sink(path: Path):
    """Open the stdout sink for ``path`` — rotating since UPDATE-013.

    The pre-013 sink was a plain ``open(path, "a")``: the log grew without
    bound (2.6 MB and counting on a daily-driver machine). The rotating
    writer caps the family at 5 MB x (1 + 2 backups); an oversized legacy
    log is reclaimed on the very first open. If the rotation module itself
    cannot be imported (exotic layout), fall back to the old plain append so
    a missing helper can never stop the backend from booting.
    """
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

# >>> CRITICAL FIX: load_dotenv() MUST be called HERE, FIRST, BEFORE any
# module below is imported. Previously load_dotenv() was called far below
# (after `from app.services.llm_router import get_ai_response`).
# llm_router.py reads os.getenv("GEMINI_API_KEY_1/2/3", "DEFAULT_MODEL", etc)
# DIRECTLY at module top level (executed ONCE when the module is imported, not
# inside a function) -- so if load_dotenv() has not run by then, .env has not
# entered os.environ at all, and GEMINI_API_KEYS becomes an EMPTY list. The
# condition `if GEMINI_API_KEYS and PRIMARY_MODEL` in llm_router.py then fails,
# and Gemini (Tier 1) is SKIPPED ENTIRELY without ever being tried -- the
# router jumps straight to Tier 2 (OpenRouter) / Tier 3 (Cloudflare) even
# though the Gemini quota is nowhere near exhausted. This was the real cause
# of the report "Orion never uses Gemini, jumps straight to fallback".
from dotenv import load_dotenv

load_dotenv()
# >>> END OF CRITICAL FIX <<<

import json


def validate_project_structure(project_path: str) -> list:
    """Check and report suspicious folder structures (duplicates, junk, etc.)."""
    warnings = []

    # Check for duplicate assets folders
    for root, dirs, _files in os.walk(project_path):
        # Ignore venv and target folders to avoid false positives
        if "venv" in root or "target" in root or ".git" in root:
            continue

        if "assets" in dirs:
            assets_path = os.path.join(root, "assets")
            # If there is an assets folder inside another folder (not the project root), warn
            expected_assets = os.path.join(project_path, "assets")
            if os.path.normpath(assets_path) != os.path.normpath(expected_assets):
                warnings.append(f"⚠️ Duplicate folder detected: {assets_path}")

    return warnings


from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.services import auth, sse, stream_utils
from app.services import tool_dispatch
from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState
from app.services.tools_schema import TOOLS
from app.services.context_manager import ContextManager
from app.services.dependency_scanner import DependencyScanner
from app.services.llm_router import get_ai_response  # <-- The only gateway to the LLM
from app.services.tool_executor import ToolExecutor

# >>> CRITICAL FIX: 3 additional modules (todo tracking, post-edit
# verification, permission/sandboxing) were PREVIOUSLY hard-imported -- if any
# of these files had not been placed in app/services/ (or had a typo/was
# broken), the ENTIRE backend would ModuleNotFoundError and fail to start AT
# ALL before ever listening on any port (exactly the symptom "the backend is
# completely unusable"). They are now soft dependencies: if the import fails,
# only the related feature is disabled (with a safe fallback) and the backend
# still starts normally.
try:
    from app.services.todo_manager import TodoManager
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] todo_manager.py failed to import ({e}). Todo-list feature disabled."
    )
    TodoManager = None


import logging

logging.basicConfig(
    filename=os.path.join(os.path.expanduser("~"), "orion_crash.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

# (load_dotenv() has already been called at the very top of this file, before
# the app.services.* imports -- see the CRITICAL FIX comment around line ~11)

# >>> PATCH ORION-5: structured logging to a FILE.
# CORRECTION NOTE: the initial suspicion that the 122 print() calls caused
# stdout backpressure was NOT accurate -- the top of this file already
# redirects sys.stdout/stderr to a log file with buffering=1, so the
# parent pipe is never flooded. That problem had already been solved.
#
# What is STILL true and fixed here:
#   1. (historical) the stdout log was opened in "a" mode WITHOUT ROTATION
#      -> it grew forever. UPDATE-013 rotates the stdout sink too (see
#      app/stdout_rotation.py: 5 MB cap x 2 backups). The
#      RotatingFileHandler below caps backend.log at 5 MB x 5.
#   2. print() has no level/timestamp/module name -> hard to filter.
# The stdout redirect is DELIBERATELY kept so there is no unnecessary
# behaviour change; the two can coexist.
#
# >>> UPDATE-012: the redirect target moved from ~/orion_stdout.log to
# <project>/.orion/logs/orion_stdout.log (see _resolve_stdout_log at the
# top), which also resolves old point 3 ("logs of several projects mix").
try:
    from app.logging_setup import get_logger, setup_logging
except ImportError:  # pragma: no cover
    from logging_setup import get_logger, setup_logging  # type: ignore[no-redef]

# >>> UPDATE-013: identity of the running backend — the GET /health payload
# and the PID-file lifecycle live in app/runtime_id.py so both the route and
# the lifespan hook share one source of truth.
try:
    from app import runtime_id
except ImportError:  # pragma: no cover
    import runtime_id  # type: ignore[no-redef]

setup_logging()
logger = get_logger("orion.main")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup hook via lifespan (UPDATE-008: replaces the deprecated
    ``@app.on_event("startup")`` which modern FastAPI flags and will remove).
    ``auto_restore_project`` is defined further down in this module; the name
    is resolved at startup time, not at import time.

    UPDATE-013: the PID file is announced HERE (not at import time) because
    the lifespan startup only runs after uvicorn successfully bound the port
    — a backend that dies on "address already in use" must never leave a
    PID file claiming this install is alive."""
    pid_path = runtime_id.write_pid_file()
    if pid_path:
        print(f"[ORION RUNTIME] PID file written: {pid_path} (pid={os.getpid()})")
    await auto_restore_project()
    yield
    runtime_id.clear_pid_file()


app = FastAPI(
    title="Project Orion Backend",
    version=runtime_id.BACKEND_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    # >>> PATCH ORION-37 (SECURITY): allow_origins=["*"] + /execute-terminal
    # without auth = a local RCE hole: while the backend is alive, a malicious
    # web page opened by the user could POST cross-origin to 127.0.0.1:8000
    # and run shell commands, bypassing the VS Code approval modal. Cross-origin
    # JSON requests trigger a preflight; with the origin not allowed, the
    # browser never sends the request at all. Legitimate clients (extension
    # host / Node) are not subject to CORS, so nothing breaks.
    allow_origins=[],
    allow_origin_regex=r"^vscode-webview://",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# >>> SECURITY (UPDATE-003): SHARED-SECRET TOKEN ==========================
# Loopback bind + closed CORS stop browsers and remote hosts, but any local
# process could still call these endpoints. The middleware below demands the
# X-Orion-Token header (see app/services/auth.py) on every route except the
# bare health probe GET / and CORS preflight. The extension reads the same
# token file, so normal installs need no configuration.
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
# >>> END OF SHARED-SECRET TOKEN =========================================
context_manager = ContextManager()
tool_executor = None
todo_manager = (
    TodoManager() if TodoManager else None
)  # >>> FIX: None-safe if the import fails
orion_config = {"max_iterations": 20}  # Default config

# File for storing the last project
LAST_PROJECT_FILE = os.path.join(os.path.expanduser("~"), ".orion_last_project.json")


def save_last_project(path: str):
    """Save the last project path to disk."""
    try:
        with open(LAST_PROJECT_FILE, "w", encoding="utf-8") as f:
            json.dump({"path": path}, f)
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


def load_orion_config(project_path: str):
    """Read or create the default config file at .orion/orion_config.json"""
    global orion_config
    config_path = os.path.join(project_path, ".orion", "orion_config.json")

    print(f"[ORION CONFIG DEBUG] Attempting to read config from: {config_path}")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)
                orion_config = {**orion_config, **loaded_config}
                print(f"[ORION CONFIG] ✅ Loaded successfully: {orion_config}")
                print(
                    f"[ORION CONFIG] max_iterations value: {orion_config.get('max_iterations')}"
                )
        except Exception as e:
            print(
                f"[ORION CONFIG WARNING] Failed to read config, using defaults. Error: {e}"
            )
    else:
        default_config = {
            "max_iterations": 20,
            # >>> NEW: the token budget threshold is configurable per project.
            # Match it to the context window of the model you actually use
            # (do not assume 8K if your model supports a larger context).
            "token_budget_threshold": 12000,
            "description": "Orion settings. Change max_iterations to limit the AI thinking loop. token_budget_threshold controls when emergency context pruning occurs.",
        }
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2)
            orion_config = default_config
            print(f"[ORION CONFIG] Default config file created at {config_path}")
        except Exception as e:
            print(f"[ORION CONFIG WARNING] Failed to create config file: {e}")


# >>> PATCH ORION-15: TOOL CONSISTENCY CHECKER =====================
# The "3 dead tools" bug happened because the TOOLS schema and the /chat
# dispatch chain were maintained separately, so the two could diverge
# undetected. This checker runs once at startup and REPORTS to the log when
# a tool is declared but has no method on tool_executor.
# (The dispatch branch itself still has to be added manually in /chat -- see
#  the warning message.)
# =====================================================================


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
            len(mati), sorted(mati),
        )
    if berlebih:
        logger.warning(
            "[TOOLS] %d tools have dispatch but are NOT declared in the TOOLS schema: %s. "
            "The model will never call them.",
            len(berlebih), sorted(berlebih),
        )
    if not mati and not berlebih:
        logger.info("[TOOLS] Consistent: %d tools declared & dispatched.", len(dideklarasikan))


periksa_konsistensi_tools()
# >>> END OF PATCH ORION-15


class ProjectPathRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    message: str
    # >>> ATTACHMENT FEATURE: list of {name, mime, data(base64)} from the extension.
    # Default [] so old requests (without this field) stay valid.
    attachments: list = []
    # >>> PATCH ORION-20c: Orion's "eyes" from VS Code.
    # {activeFile, selection, diagnostics[]}. Empty with an old extension.
    editor: dict = {}


@app.get("/")
async def root():
    return {"status": "Orion Backend is running!", "version": runtime_id.BACKEND_VERSION}


# >>> UPDATE-013: identity check for the extension. Unlike the bare GET /
# (deliberately unauthenticated so anything can probe "is A server alive"),
# /health sits behind the shared-secret middleware: only a caller holding
# the token can fingerprint the process. backendManager.ts uses the fields
# below to tell a FRESH Orion from a stale orphan (booted before the code
# was last modified) or a foreign app squatting on port 8000.
@app.get("/health")
async def health():
    return runtime_id.health_payload(context_manager.project_path)
# >>> END OF UPDATE-013 /health


@app.post("/set-project")
async def set_project(request: ProjectPathRequest):
    global tool_executor

    if not os.path.isdir(request.path):
        return {"success": False, "error": "Folder not found."}

    # 1. Validate the folder structure
    structure_warnings = validate_project_structure(request.path)

    # 2. Set the path and initialize the tool executor
    context_manager.set_project_path(request.path)
    tool_executor = ToolExecutor(request.path)
    if todo_manager:
        todo_manager.set_project_path(request.path)  # >>> FIX: None-safe guard

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

        # Build the project structure string and save it to context_manager
        map_lines = [f"📁 {path}" for path in scan_result["files"].keys()]
        project_map_str = "Project Structure:\n" + "\n".join(map_lines)
        # >>> UPDATE-013 hardening (patch 0007): when the scan hit the file
        # cap, SAY SO inside the map itself. A silent partial map would let
        # the model believe the project is smaller than it is; a loud one
        # tells it to explore beyond the list with search_code.
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
        context_manager.set_project_map(project_map_str)

    except Exception as e:
        print(f"[ORION INIT WARNING] Failed to generate Dependency Map: {e}")
        context_manager.set_project_map("Project Structure: Scan failed.")

    # 6. Load config
    load_orion_config(request.path)

    return {
        "success": True,
        "message": "Project scanned successfully.",
        "project_map_preview": context_manager.get_project_map()[:500] + "...",
        "structure_warnings": structure_warnings,  # Send warnings to the frontend
    }


async def auto_restore_project():
    """Auto-restore the last project when the backend starts up.
    Invoked by the ``lifespan`` handler above (UPDATE-008)."""
    auth.get_shared_token()  # mint at startup so clients can read it immediately
    print(
        f"[ORION AUTH] Shared token file: {auth.token_file_path()} — "
        "X-Orion-Token required on all routes except GET /"
    )
    global tool_executor
    last_path = load_last_project()
    if last_path and os.path.isdir(last_path):
        print(f"[ORION STARTUP] 🔄 Auto-restoring previous project: {last_path}")
        context_manager.set_project_path(last_path)
        tool_executor = ToolExecutor(last_path)
        if todo_manager:
            todo_manager.set_project_path(last_path)  # >>> FIX: None-safe guard
        load_orion_config(last_path)
        print("[ORION STARTUP] ✅ Project restored successfully. Tool executor ready.")
    else:
        print(
            "[ORION STARTUP] No previous project found. Waiting for /set-project..."
        )


@app.post("/chat")
async def chat_with_orion(request: ChatRequest):
    if not context_manager.project_map or tool_executor is None:

        # >>> UPDATE-013 (SSE robustness): this early-exit was the ONLY /chat
        # path that bypassed stream_utils.jamin_done — the stream ended with
        # 'error' and NO 'done'. The extension (PATCH ORION-12: "no done ==
        # crash") then layered a false 'Connection to the backend was lost'
        # alarm on top of the perfectly clear error bubble. Found by the SSE
        # harness (scenario h recorded 1 frame instead of 2). The wrapper
        # guarantees the terminal done, exactly like every other path.
        async def error_gen():
            yield sse.ev('error', text='Please set a project first.')

        return StreamingResponse(
            stream_utils.jamin_done(error_gen(), logger=logger),
            media_type="text/event-stream",
        )

    # >>> ATTACHMENT FEATURE ==============================================
    # Images are NOT stored in the history. The base64 of one 1 MB screenshot
    # becomes +/- 1.4 million characters; if it entered session_history.json then
    # every agent iteration would resend it and the free-tier quota would run out fast.
    # The history only stores a text placeholder; the actual image is injected
    # into api_messages ONLY on the first iteration (see patch B4b).
    # ==================================================================
    from app.services.attachments import build_user_content, describe_attachments

    _lamp_desc = describe_attachments(request.attachments)
    _content_parts, _riwayat_text = build_user_content(request.message, request.attachments)
    # >>> PHASE C STEP 2: per-turn mutable state now lives in one SessionState
    # (app/services/session_state.py) instead of loose locals inside the
    # generator. The attachment image parts are single-use: injected into
    # api_messages on the first iteration, then state.pending_image is set
    # back to None. The old 1-element-list holder (a workaround for ruff
    # F823) is no longer needed — assigning through `state.` cannot create a
    # generator-local.
    state = SessionState(
        pending_image=_content_parts if isinstance(_content_parts, list) else None
    )
    if _lamp_desc:
        logger.info("[ATTACHMENT] %s", _lamp_desc)

    context_manager.add_message("user", _riwayat_text)

    # >>> CACHE OPTIMIZATION: separate static and dynamic messages <<<

    # (The static SYSTEM_PROMPT itself is imported by agent_loop.py since Phase C step 4.)

    # >>> PATCH ORION-20d ================================================
    # INJECT THE EDITOR CONTEXT. This is what makes Orion "see":
    #   - which file you currently have open
    #   - which text you have selected (the most relevant context, for free)
    #   - the errors VS Code reports in the Problems panel
    # The last part is the strongest hallucination preventer: if Orion just
    # edited file X and Problems mentions an ERROR in file X, it gets explicit
    # instructions to fix it before claiming completion.
    # ====================================================================
    _editor_ctx = request.editor if isinstance(request.editor, dict) else {}
    _editor_block = ""

    _aktif = str(_editor_ctx.get("activeFile") or "")
    if _aktif and not _aktif.startswith(".."):
        _editor_block += f"\n\nFILE THE USER CURRENTLY HAS OPEN: {_aktif}"

    _sel = str(_editor_ctx.get("selection") or "")
    if _sel:
        _editor_block += f"\n\nTEXT THE USER CURRENTLY HAS SELECTED IN THAT FILE:\n```\n{_sel[:4000]}\n```"

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

    context_message = {
        "role": "user",
        "content": f"Current project structure:\n{context_manager.get_project_map()}"
        + _editor_block,
    }

    # >>> END OF CACHE OPTIMIZATION <<<

    # >>> PHASE C STEP 4: the agent loop itself now lives in
    # app/services/agent_loop.py. This route composes its explicit
    # dependencies; the terminal-'done' guarantee stays here, in the
    # stream_utils.jamin_done wrapper below (PATCH ORION-28/36).
    deps = Deps(
        context_manager=context_manager,
        todo_manager=todo_manager,
        tool_executor=tool_executor,
        llm_call=get_ai_response,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config=orion_config,
        logger=logger,
        context_message=context_message,
        lampiran_desc=_lamp_desc,
    )

    # >>> PATCH ORION-28 ================================================
    # The audit found TEN exit paths in the core stream that ended the
    # stream WITHOUT sending a 'done' event (ask_user, waiting for terminal
    # confirmation, emergency stop, iteration cap, etc). As a result the
    # extension read those LEGITIMATE closures as crashes and showed
    # "Connection to the backend was lost" -- a false alarm.
    #
    # Instead of patching ten places (and future ones), a single
    # wrapper guarantees: whatever the path, if the stream ends
    # without 'done', the wrapper sends it. A genuine crash (dead
    # process) still produces no done, so ORION-12 in the extension
    # can still tell a real disconnect from a normal ending.
    # ==================================================================
    async def generate_stream():
        # >>> PATCH ORION-36: the "guarantee a done" logic moved to stream_utils
        # so it can be unit-tested without running FastAPI (test_stream_utils).
        async for potongan in stream_utils.jamin_done(aliran_agent(state, deps), logger=logger):
            yield potongan

    return StreamingResponse(generate_stream(), media_type="text/event-stream")


@app.post("/clear-chat")
async def clear_chat():
    context_manager.clear_history(persist=True)
    return {"success": True, "message": "Chat history cleared."}


class TerminalRequest(BaseModel):
    command: str


@app.post("/execute-terminal")
async def execute_approved_terminal(request: TerminalRequest):
    """Dedicated endpoint for executing a command the user already approved."""
    if tool_executor is None:
        return {"success": False, "output": "Tool executor not initialized."}

    output = tool_executor.run_terminal(command=request.command)

    # >>> CRITICAL FIX: update the placeholder with the real result <<<
    history = context_manager.get_history()
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "tool" and "[AWAITING CONFIRMATION]" in msg.get(
            "content", ""
        ):
            history[i]["content"] = output
            print(
                f"[ORION] Dangerous command executed. Tool call ID {msg.get('tool_call_id')} updated."
            )
            if hasattr(context_manager, "save_history"):
                context_manager.save_history()
            break

    return {"success": True, "output": output}


@app.get("/performance")
async def get_performance():
    """Read and compute statistics from performance_log.jsonl"""
    if tool_executor is None:
        return {"error": "Project not set yet."}

    log_file = os.path.join(
        tool_executor.project_path, ".orion", "performance_log.jsonl"
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
    total_input_tokens = 0
    total_output_tokens = 0
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
                        total_input_tokens += entry.get("input_tokens", 0)
                        total_output_tokens += entry.get("output_tokens", 0)
                        if entry.get("status") == "success":
                            success_count += 1
                        else:
                            error_count += 1
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        # >>> PATCH ORION-3: `except: pass` swallowed ALL errors including
                        # KeyboardInterrupt/SystemExit, and hid broken log lines.
                        continue
    except OSError as e:
        return {"error": f"Failed to read log: {e!s}"}

    total_requests = len(logs)
    avg_duration = total_duration / total_requests if total_requests > 0 else 0

    return {
        "summary": {
            "total_requests": total_requests,
            "total_input_tokens": sum(
                l.get("input_tokens", 0) for l in logs
            ),
            "total_output_tokens": sum(
                l.get("output_tokens", 0) for l in logs
            ),
            "total_cost_usd": round(total_cost, 6),
            "avg_response_time_ms": round(avg_duration, 2),
            "success_count": success_count,
            "error_count": error_count,
        },
        "recent_logs": logs[-10:],
    }
