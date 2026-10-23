"""
runtime_id.py — identity & lifecycle of the running backend (UPDATE-013)
========================================================================
Before this module existed, the extension decided "the backend is ready"
from a bare TCP connect to port 8000: ANY process listening there — an
orphaned old backend that survived VS Code, a second Orion install, or a
completely unrelated app — was accepted as "Orion is running". Stale code
then answered chat requests after an upgrade, which is invisible to the
user and extremely confusing to debug.

This module gives every backend process a verifiable identity:

  * ``GET /health`` (token-guarded like every other route) answers with
    the payload from :func:`health_payload` — app name, version, pid,
    boot timestamp, absolute backend folder, and the active project.
  * A PID file (``<repo>/.orion/orion_backend.pid`` by default, override
    with ``ORION_PID_FILE`` for tests) is written at startup and removed
    at shutdown, so tooling can tell which process claims this install.

The extension compares ``backend_path`` with the folder it discovered and
``started_at`` with the mtime of ``app/main.py``: a backend that booted
BEFORE the code was last modified is running stale code and gets offered
a restart (see orion/src/services/backendManager.ts, UPDATE-013).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Bumped 0.4.1 -> 0.5.0 for UPDATE-013: /health + PID file are new
# machine-readable surface, so a minor bump is the honest signal.
# Bumped 0.5.0 -> 0.5.1 for patch 0007: /health gained the interpreter
# fields (python_executable / python_prefix) and the scanner hardening
# landed — both machine-visible, so the version should move.
# Bumped 0.5.1 -> 0.6.0 for UPDATE-014: MCP tool-server foundation —
# /set-project gained the additive "mcp" field, /health gained
# mcp_servers/mcp_tools, and the agent loop can dispatch mcp__* tools.
# New machine-visible surface without breaking old clients = minor bump.
# Bumped 0.6.0 -> 0.6.1 for the UPDATE-014 adendum (patch 0005): mcp.json
# now tolerates a Windows BOM (utf-8-sig) and the test suite no longer
# touches the developer's real ~/.orion_last_project.json / MCP config.
# Bumped 0.6.1 -> 0.7.0 for UPDATE-015: token-guarded management surface
# GET /mcp/status + POST /mcp/config (validate -> atomic write -> reload),
# consumed by the new VS Code MCP panel and wizard card.
# Bumped 0.7.0 -> 0.8.0 for UPDATE-016: internal sub-agents
# (delegate_explorer / delegate_verifier / delegate_workers) with depth-1
# rule, disjoint write scopes, per-turn and per-run budgets; closes the
# MCP phase together with 0.6.x/0.7.x -> milestone v0.1.0.
# Bumped 0.8.0 -> 0.8.1 for UPDATE-017: delegation policy in SYSTEM_PROMPT,
# claim-discipline in sub-agent role prompts, and the mcp_enabled master
# switch (behaviour-shaping, machine-visible via /mcp/status 'enabled').
# Bumped 0.8.1 -> 0.9.0 for UPDATE-018: the .orion/inbox drop-zone (new
# per-turn ingest, 📥 status frame, archive+manifest) and per-role
# sub-agent model profiles (new /models/* routes, subagent_models config).
# Bumped 0.9.0 -> 0.9.1 for UPDATE-019 (defects & forensics): the
# duplicate-assets structure check is pattern-pruned and capped, and the
# incident-013 twin-process diagnosis gained a token-guarded surface
# (GET /runtime/forensics) plus a startup log line — additive diagnostic
# surface, no behaviour change for existing routes.
BACKEND_VERSION = "0.9.1"
APP_NAME = "orion-backend"

# This file is <repo>/backend/app/runtime_id.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Captured at import time, i.e. moments before uvicorn binds the port.
STARTED_AT = time.time()


def pid_file_path() -> Path:
    """Where this install announces its live process (overridable for tests)."""
    override = os.getenv("ORION_PID_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _REPO_ROOT / ".orion" / "orion_backend.pid"


def write_pid_file() -> Path | None:
    """Announce this process as the live backend of THIS install.

    Called from the lifespan startup hook, i.e. only after uvicorn has
    successfully bound port 8000 — a backend that fails to bind never
    leaves a misleading PID file behind. Best-effort: an unwritable
    location degrades to a warning, never to a crashed startup.
    """
    path = pid_file_path()
    payload = {
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "version": BACKEND_VERSION,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path
    except OSError as e:
        print(f"[ORION RUNTIME WARNING] cannot write PID file {path}: {e}")
        return None


def clear_pid_file() -> None:
    """Remove the PID file on shutdown — but ONLY if it is still ours.

    When two installs race (or the file was replaced by a newer process),
    deleting someone else's announcement would recreate the very confusion
    this module exists to prevent.
    """
    path = pid_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("pid") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


# >>> UPDATE-019: twin-process forensics (incident 013) ====================
# The 013 incident ("two backends alive at once") was painful because the
# three sources of truth — the PID file's claim, the process actually
# answering port 8000, and the code mtime on disk — can each disagree, and
# nothing exposed all three at once. These helpers never raise: forensics
# must work exactly when the install is misbehaving.
_IS_WINDOWS = os.name == "nt"


def _pid_alive_windows(pid: int):
    """Side-effect-free liveness probe for Windows; None when unavailable.

    REGRESSION GUARD (owner's machine, 2026-10-23): ``os.kill(pid, 0)`` is
    UNUSABLE on Windows — CPython maps any signal besides the two console
    events to ``TerminateProcess(pid, sig)``, so a "liveness probe" would
    KILL the target process (and with sig=0 it can even surface as
    SystemError: <built-in function kill> returned a result with an
    exception set). The supported probe is OpenProcess +
    GetExitCodeProcess == STILL_ACTIVE, which only observes.
    """
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None  # not a real Windows runtime: degrade safely
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False  # no such process (or no rights to even query)
        try:
            exit_code = ctypes.c_ulong(0)
            if not windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return None
            return exit_code.value == STILL_ACTIVE
        finally:
            windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - a broken probe must never raise
        return None


def pid_alive(pid) -> bool:
    """Best-effort liveness probe for a pid; never raises, never kills.

    POSIX: signal 0 via os.kill. Windows: the OpenProcess probe above —
    os.kill would terminate the target there (see _pid_alive_windows).
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if _IS_WINDOWS:
        return bool(_pid_alive_windows(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it just is not ours to signal
    except OSError:
        return False
    return True


def read_pid_file() -> dict:
    """The raw PID-file claim: {path, exists, pid, started_at, version, error}.

    ``error`` is set when the file EXISTS but cannot be parsed (corrupt or
    foreign content); a missing file is simply exists=False, error=None.
    """
    path = pid_file_path()
    out = {
        "path": str(path),
        "exists": False,
        "pid": None,
        "started_at": None,
        "version": None,
        "error": None,
    }
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return out  # missing file = simply no claim, not an error
    except OSError as e:
        out["error"] = f"unreadable: {type(e).__name__}"
        return out
    out["exists"] = True
    try:
        data = json.loads(raw)
    except ValueError:
        out["error"] = "corrupt: not valid JSON"
        return out
    if isinstance(data, dict):
        out["pid"] = data.get("pid")
        out["started_at"] = data.get("started_at")
        out["version"] = data.get("version")
    else:
        out["error"] = "corrupt: not a JSON object"
    return out


def forensics_payload(project_path: str | None) -> dict:
    """The body of GET /runtime/forensics — incident-013 evidence in one call.

    Compares the three sources of truth that can disagree:
      1. the PID file's CLAIM (which process announced this install),
      2. THIS process (by definition the one answering port 8000),
      3. the code on disk (booted before the last modification = stale).
    ``verdicts`` holds honest, human-readable conclusions; an empty list
    means nothing suspicious. The extension's own staleness check
    (backendManager.ts) stays as it is — this route is the owner/agent
    facing diagnostic, and the startup log line uses the same evidence.
    """
    claim = read_pid_file()
    me = os.getpid()
    claim_pid = claim.get("pid")
    claim_alive = pid_alive(claim_pid)
    verdicts: list = []

    if claim["exists"] and claim["error"] is not None:
        verdicts.append(
            f"PID FILE UNREADABLE ({claim['error']}); its claim is ignored. "
            "A corrupt or foreign file at this path usually means another "
            "tool wrote here, or the previous process died mid-write."
        )
    elif claim["exists"] and isinstance(claim_pid, int) and claim_pid != me:
        if claim_alive:
            verdicts.append(
                f"TWIN SUSPECTED: the PID file claims pid {claim_pid} for this "
                f"install and that pid is ALIVE, yet the process answering this "
                f"request is {me}. Two backends are racing for one install — "
                f"only one can hold port 8000. Find the stray process "
                f"(Get-Process -Id {claim_pid}) and kill it, or restart VS Code."
            )
        else:
            verdicts.append(
                f"STALE PID FILE: it claims pid {claim_pid}, which is not alive "
                f"— the previous shutdown was unclean (crash/kill). This "
                f"process ({me}) replaced the claim at boot; no twin."
            )

    code_modified_at = None
    try:
        code_modified_at = (BACKEND_DIR / "app" / "main.py").stat().st_mtime
    except OSError:
        pass
    if code_modified_at is not None and STARTED_AT < code_modified_at:
        verdicts.append(
            "STALE CODE: this process booted BEFORE app/main.py was last "
            "modified, so it is running old code. Restart the backend "
            "(kill the port-8000 listener, then Start Backend)."
        )

    return {
        "app": APP_NAME,
        "version": BACKEND_VERSION,
        "self": {"pid": me, "started_at": STARTED_AT},
        "pid_file": claim,
        "pid_file_claim_alive": claim_alive,
        "code_modified_at": code_modified_at,
        "project_path": project_path,
        "verdicts": verdicts,
    }
# >>> END OF UPDATE-019 forensics ==========================================


def health_payload(project_path: str | None) -> dict:
    """The body of GET /health. Keep keys stable — the extension parses them.

    UPDATE-013 patch 0007: the interpreter fields exist because a real
    incident showed a backend listening on port 8000 while running under a
    DIFFERENT Python than the project venv (wrong package stack, silent
    provider failures). Version, folder and PID alone could not see it;
    the executable/prefix can.
    """
    return {
        "app": APP_NAME,
        "version": BACKEND_VERSION,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "backend_path": str(BACKEND_DIR),
        "project_path": project_path,
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
    }
