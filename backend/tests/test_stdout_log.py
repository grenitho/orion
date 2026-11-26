"""
test_stdout_log.py — guards for the UPDATE-012 stdout-log relocation
====================================================================
Every ``print()`` of the backend (including the ``[LLM_ROUTER] Detected ...``
startup lines) goes to the file that ``main.py`` opens at import time.
UPDATE-012 moved that file from ``~/orion_stdout.log`` into the project
(``<project>/.orion/logs/orion_stdout.log``) so both logs live side by side
and projects no longer share one home-directory file.

These tests pin the resolution contract of ``main._resolve_stdout_log``:

  1. ``$ORION_STDOUT_LOG`` wins (explicit override for tests/debugging);
  2. the default is the per-project path, derived from ``__file__`` —
     NOT from the current working directory, so it works no matter where
     uvicorn was launched from;
  3. if the project folder cannot be created (read-only install), the
     historical ``~/orion_stdout.log`` location is used as fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import main as main_module


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-stdout.log"
    monkeypatch.setenv("ORION_STDOUT_LOG", str(custom))
    assert main_module._resolve_stdout_log() == custom


def test_default_is_per_project_and_cwd_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ORION_STDOUT_LOG", raising=False)
    # Launching the backend from an unrelated directory must NOT move the log.
    monkeypatch.chdir(tmp_path)
    resolved = main_module._resolve_stdout_log()

    project_root = Path(main_module.__file__).resolve().parents[2]
    assert resolved == project_root / ".orion" / "logs" / "orion_stdout.log"
    assert resolved.parent.is_dir()  # created on demand


def test_falls_back_to_home_when_project_dir_unwritable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_STDOUT_LOG", raising=False)

    def refuse_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)
    assert main_module._resolve_stdout_log() == Path.home() / "orion_stdout.log"


def test_live_redirect_points_at_resolved_file() -> None:
    """The module-level redirect must target the per-project file (or the
    documented home fallback) — never a bare pipe that floods the parent.
    (We check the recorded path, not ``sys.stdout`` identity: pytest
    replaces ``sys.stdout`` with its own capture object while tests run.)"""
    target = Path(main_module._STDOUT_LOG_PATH)
    allowed = {
        Path(main_module.__file__).resolve().parents[2]
        / ".orion" / "logs" / "orion_stdout.log",
        Path.home() / "orion_stdout.log",
    }
    assert target in allowed
    # the redirect really opened that file in append mode at import time
    assert target.exists()
