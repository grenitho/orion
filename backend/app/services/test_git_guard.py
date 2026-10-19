"""
test_git_guard.py — tests for blocking dangerous git in run_terminal (ORION-27)
===============================================================================
REAL bug background: Orion once ran `git init` through run_terminal, hung,
then the backend died and the user saw "connection lost". This guard rejects
destructive/network/hanging git operations AT CODE LEVEL, before any process
is spawned.

These tests ensure:
  - every family of forbidden commands is really rejected
  - rejection happens WITHOUT side effects (no .git is created)
  - safe commands still run (no over-blocking)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.tool_executor import ToolExecutor

DIBLOKIR = [
    "git init",
    "git init .",
    "GIT INIT",                      # test case-insensitivity
    "git push origin main",
    "git pull",
    "git fetch --all",
    "git clone https://contoh.dev/x.git",
    "git reset --hard HEAD",
    "git clean -fd",
    "git checkout .",
    "git restore .",
    "git branch -D fitur",
    "git rebase main",
    "git merge fitur",
]

AMAN = [
    "git status",
    "git diff",
    "git log --oneline -3",
    "echo halo",
]


@pytest.fixture()
def eksekutor():
    with tempfile.TemporaryDirectory() as d:
        yield ToolExecutor(d), d


@pytest.mark.parametrize("perintah", DIBLOKIR)
def test_perintah_berbahaya_ditolak(eksekutor, perintah):
    t, _ = eksekutor
    hasil = t.run_terminal(perintah, timeout=5)
    assert hasil.startswith("[REJECTED BY SYSTEM]"), f"'{perintah}' should be rejected: {hasil[:80]}"


def test_penolakan_tanpa_efek_samping(eksekutor):
    """A rejected git init must not leave a .git folder behind."""
    t, d = eksekutor
    t.run_terminal("git init", timeout=5)
    assert not os.path.isdir(os.path.join(d, ".git"))


@pytest.mark.parametrize("perintah", AMAN)
def test_perintah_aman_tidak_overblocking(eksekutor, perintah):
    t, _ = eksekutor
    hasil = t.run_terminal(perintah, timeout=10)
    assert not hasil.startswith("[REJECTED BY SYSTEM]"), f"'{perintah}' should not be rejected"


def test_pesan_penolakan_memberi_jalan_keluar(eksekutor):
    """A good rejection tells you what to do, not just 'no'."""
    t, _ = eksekutor
    hasil = t.run_terminal("git push origin main", timeout=5)
    assert "Ask the user" in hasil or "manual" in hasil
