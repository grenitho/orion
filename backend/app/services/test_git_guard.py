"""
test_git_guard.py — test pemblokiran git berbahaya di run_terminal (ORION-27)
==============================================================================
Latar belakang bug NYATA: Orion pernah menjalankan `git init` lewat run_terminal,
hang, lalu backend mati dan user melihat "koneksi terputus". Guard ini menolak
operasi git yang merusak/jaringan/hang DI LEVEL KODE, sebelum proses dilahirkan.

Test ini memastikan:
  - setiap keluarga perintah terlarang benar-benar ditolak
  - penolakan terjadi TANPA efek samping (tidak ada .git yang tercipta)
  - perintah aman tetap jalan (tidak over-blocking)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.tool_executor import ToolExecutor

DIBLOKIR = [
    "git init",
    "git init .",
    "GIT INIT",                      # uji case-insensitivity
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
    assert hasil.startswith("[REJECTED BY SYSTEM]"), f"'{perintah}' seharusnya ditolak: {hasil[:80]}"


def test_penolakan_tanpa_efek_samping(eksekutor):
    """git init yang ditolak tidak boleh meninggalkan folder .git."""
    t, d = eksekutor
    t.run_terminal("git init", timeout=5)
    assert not os.path.isdir(os.path.join(d, ".git"))


@pytest.mark.parametrize("perintah", AMAN)
def test_perintah_aman_tidak_overblocking(eksekutor, perintah):
    t, _ = eksekutor
    hasil = t.run_terminal(perintah, timeout=10)
    assert not hasil.startswith("[REJECTED BY SYSTEM]"), f"'{perintah}' tidak seharusnya ditolak"


def test_pesan_penolakan_memberi_jalan_keluar(eksekutor):
    """Penolakan yang baik memberi tahu apa yang harus dilakukan, bukan hanya 'tidak'."""
    t, _ = eksekutor
    hasil = t.run_terminal("git push origin main", timeout=5)
    assert "Ask the user" in hasil or "manual" in hasil
