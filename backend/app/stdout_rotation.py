"""
stdout_rotation.py — size-capped sink for the backend's stdout log (UPDATE-013)
================================================================================
main.py redirects sys.stdout/sys.stderr into a log file at import time so the
pipe to VS Code can never be flooded (see logging_setup.py for the history).
That redirect used a plain ``open(path, "a")`` — the file grew WITHOUT BOUND.
Real-world evidence: an orion_stdout.log of 2.6 MB and counting on a daily-
driver machine. backend.log already rotates (5 MB x 5, RotatingFileHandler);
this module brings the same discipline to the stdout log.

Design constraints:
  * sys.stdout needs a file-LIKE object (write/flush/encoding/...), not a
    logging handler — so RotatingFileHandler is not directly usable here.
  * Rotation must never crash the backend: every filesystem error degrades
    to "keep writing to the current file".
  * Naming follows the stdlib convention: orion_stdout.log.1, .2, ...

Policy: rotate when a write would push the file past MAX_BYTES (5 MB, same
cap as backend.log); keep BACKUP_COUNT (2) generations, so the stdout log
family is capped at ~15 MB per project. Startup rotates an already-oversized
file too, so an old unbounded log gets reclaimed on the first backend start
after this update.
"""

from __future__ import annotations

import threading
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024   # same cap as backend.log (RotatingFileHandler)
BACKUP_COUNT = 2              # orion_stdout.log.1 and .2


def rotate_now(path: Path | str, *, force: bool = False) -> bool:
    """Shift ``path`` -> ``path.1`` -> ... -> ``path.<BACKUP_COUNT>`` (oldest deleted).

    Without ``force`` this is a no-op unless the file is larger than MAX_BYTES.
    Returns True when a rotation actually happened. Never raises: a read-only
    folder or a locked file simply means "no rotation this time".
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False
        if not force and p.stat().st_size <= MAX_BYTES:
            return False
        oldest = p.with_name(f"{p.name}.{BACKUP_COUNT}")
        if oldest.exists():
            oldest.unlink()
        for i in range(BACKUP_COUNT - 1, 0, -1):
            src = p.with_name(f"{p.name}.{i}")
            if src.exists():
                src.rename(p.with_name(f"{p.name}.{i + 1}"))
        p.rename(p.with_name(f"{p.name}.1"))
        return True
    except OSError:
        return False


class RotatingFileWriter:
    """Line-buffered append-mode text sink that rotates at MAX_BYTES.

    Duck-types the pieces of a text file object that sys.stdout consumers
    actually touch (write/writelines/flush/close/fileno/isatty/encoding);
    everything else is delegated to the underlying file via __getattr__.
    """

    def __init__(self, path: Path | str, encoding: str = "utf-8"):
        self._path = Path(path)
        self._encoding = encoding
        # >>> UPDATE-024 Commit 2: one lock for the whole write/rotate/flush
        # path. Owner log forensics (2026-10-27): 707 copies of a 60-line
        # "Logging error / OSError EINVAL" block — ~75% of a 56k-line log —
        # because uvicorn's access logger flushes this sink from request
        # threads while rotation could swap the underlying file from
        # another thread. The sink must be unkillable AND race-free.
        self._lock = threading.Lock()
        # Reclaim an already-oversized legacy log on open (the pre-013 file
        # was unbounded, so the first start after the update should shrink it).
        rotate_now(self._path)
        self._file = open(self._path, "a", encoding=encoding, buffering=1)

    # -- write path ------------------------------------------------------

    def write(self, s) -> int:
        if not isinstance(s, str):
            s = str(s)
        with self._lock:
            try:
                # character count as a byte estimate — good enough for a cap, and
                # far cheaper than encoding every line just to measure it
                if self._file.tell() + len(s) >= MAX_BYTES:
                    self._rotate()
            except (OSError, ValueError):
                pass  # a closed/odd underlying file must never kill a print()
            try:
                return self._file.write(s)
            except (OSError, ValueError):
                # >>> UPDATE-024: a dead handle must degrade to silence, not
                # into a 60-line logging-error block per request.
                self._reopen_quietly()
                return len(s)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def _rotate(self) -> None:
        """Close, shift the generations, reopen. Errors degrade to 'keep going'."""
        try:
            self._file.close()
        except OSError:
            pass
        rotate_now(self._path, force=True)
        self._file = open(self._path, "a", encoding=self._encoding, buffering=1)

    def _reopen_quietly(self) -> None:
        """Self-heal after a write/flush hit a dead handle (UPDATE-024).

        Called WITH the lock held. Never raises: if even the reopen fails
        the sink keeps a closed file and swallows output — losing log
        lines is always better than flooding the log with the complaint
        about losing log lines.
        """
        try:
            self._file.close()
        except (OSError, ValueError):
            pass
        try:
            self._file = open(self._path, "a", encoding=self._encoding, buffering=1)
        except OSError:
            pass

    # -- passthroughs that matter for sys.stdout ------------------------------

    def flush(self) -> None:
        # >>> UPDATE-024 Commit 2: guarded + locked. This exact call is where
        # the OSError EINVAL escaped into logging's error printer 707 times.
        with self._lock:
            try:
                self._file.flush()
            except (OSError, ValueError):
                self._reopen_quietly()

    def close(self) -> None:
        self._file.close()

    def fileno(self) -> int:
        return self._file.fileno()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return self._file.encoding

    def __getattr__(self, name):
        # delegate anything not defined here (buffer, readable, seekable, ...)
        try:
            return getattr(self.__dict__["_file"], name)
        except KeyError:
            raise AttributeError(name) from None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RotatingFileWriter {self._path} max={MAX_BYTES} backups={BACKUP_COUNT}>"
