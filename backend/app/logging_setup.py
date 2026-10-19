"""
logging_setup.py
================
The `print()` REPLACEMENT for the ORION backend.

WHY THIS MATTERS (this was a likely cause of "stopping silently"):

The ORION backend runs as a CHILD PROCESS spawned by the VS Code extension
(see start-backend.bat). A comment in llm_router.py had already identified
the mechanism:

    "...this can flood the stdout pipe captured by the parent process
     (e.g. the VS Code extension). If the parent does not read fast
     enough, the backend can stall waiting for the buffer to drain
     (backpressure), then get force-killed by the parent's
     watchdog/health-check."

Fixing `litellm._turn_on_debug()` was good, but the backend still had
**122 `print()` calls**. Every `print()` writes to that same pipe. When the
agent runs many steps, the pipe fills, the backend clogs, then dies. From
the user's side: the assistant stops without any message.

SOLUTION: log to a FILE, not to stdout. stdout stays nearly empty.

USAGE (replace every `print(...)` in the backend):

    from logging_setup import get_logger
    log = get_logger(__name__)

    log.info("Trying model %s", model_name)
    log.warning("Rate limit: %s", e)
    log.exception("Unexpected failure")     # <- automatically includes the traceback

The log file lives at:  <project>/.orion/logs/backend.log  (rotation 5 MB x 5 files)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_DISETUP = False
_LOG_DIR: Path | None = None


def _cari_folder_log() -> Path:
    """Find .orion/logs, walking upward from this file's location."""
    env = os.getenv("ORION_LOG_DIR")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for induk in [here.parent, *here.parents]:
        kandidat = induk / ".orion"
        if kandidat.is_dir():
            return kandidat / "logs"
        if (induk / "backend").is_dir() and (induk / ".orion").is_dir():
            return induk / ".orion" / "logs"
    return Path.cwd() / "logs"


def setup_logging(level: str | None = None, *, ke_stdout: bool = False) -> Path:
    """Call ONCE when the application starts (in main.py, at the very top).

    ke_stdout=False (default) -> log ONLY to file. That is what you want,
    because stdout is the pipe to the VS Code extension and must not be
    flooded.
    """
    global _DISETUP, _LOG_DIR
    if _DISETUP and _LOG_DIR is not None:
        return _LOG_DIR

    _LOG_DIR = _cari_folder_log()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    akar = logging.getLogger()
    akar.setLevel((level or os.getenv("LOG_LEVEL", "INFO")).upper())
    # clear old handlers (uvicorn often already added a few)
    for h in list(akar.handlers):
        akar.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    berkas = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    berkas.setFormatter(fmt)
    akar.addHandler(berkas)

    if ke_stdout:
        # only for manual debugging in a terminal, NEVER in production
        layar = logging.StreamHandler(sys.stdout)
        layar.setFormatter(fmt)
        akar.addHandler(layar)

    # Silence noisy logs from third-party libraries
    for nama in ("litellm", "LiteLLM", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(nama).setLevel(logging.WARNING)

    _DISETUP = True
    return _LOG_DIR


def get_logger(nama: str) -> logging.Logger:
    """Get a logger. Runs setup automatically if not done yet."""
    if not _DISETUP:
        setup_logging()
    return logging.getLogger(nama)


def path_log() -> str:
    """Current log file path — to show the user while debugging."""
    if _LOG_DIR is None:
        setup_logging()
    return str(_LOG_DIR / "backend.log")


# ---------------------------------------------------------------------------
# Gradual-migration trick: while the 122 print() calls have not all been
# replaced yet, put this at the VERY TOP of main.py to divert print() into
# the log file. This is a temporary safety net, not the end state.
#
#     import logging_setup
#     logging_setup.setup_logging()
#     logging_setup.alihkan_print()      # <- sementara
#
def alihkan_print() -> None:
    """Redefine the global print() so it writes to the log file, not stdout.

    IMPORTANT: this is a temporary solution. The end goal remains replacing
    print() with real log.info()/log.warning() calls.
    """
    if not _DISETUP:
        setup_logging()
    import builtins

    _asli = builtins.print
    logger = logging.getLogger("stdout")

    def print_ke_log(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        # only divert when it really goes to stdout (not to another file)
        if kwargs.get("file") not in (None, sys.stdout):
            return _asli(*args, **kwargs)
        try:
            sep = kwargs.get("sep", " ")
            logger.info(sep.join(str(a) for a in args))
        except Exception:  # noqa: BLE001 - never fail because of logging
            pass

    builtins.print = print_ke_log  # type: ignore[assignment]
