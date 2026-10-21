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
import time
from pathlib import Path

# Bumped 0.4.1 -> 0.5.0 for UPDATE-013: /health + PID file are new
# machine-readable surface, so a minor bump is the honest signal.
BACKEND_VERSION = "0.5.0"
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


def health_payload(project_path: str | None) -> dict:
    """The body of GET /health. Keep keys stable — the extension parses them."""
    return {
        "app": APP_NAME,
        "version": BACKEND_VERSION,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "backend_path": str(BACKEND_DIR),
        "project_path": project_path,
    }
