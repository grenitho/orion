"""
test_git_commit_hygiene.py — UPDATE-025 hotfix-2, git_commit teeth
==================================================================
InvoiceFlow incident (2026-10-28): a blind `git add -A` checkpoint
committed frontend/node_modules (thousands of files), frontend/dist,
__pycache__ pyc files AND .orion/session_history.json (a copy of the
owner's conversation) because the project had no .gitignore. The tool
now: creates/tops up a baseline .gitignore, untracks generated paths
(index only — files stay on disk), reports the staged file count and
warns above 200 files. Pinned here against REAL throwaway git repos.
"""

from __future__ import annotations

import subprocess

import pytest

from app.services.tool_executor import ToolExecutor


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    # git needs an identity; the sandbox has none configured globally.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "T")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "T")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    proyek = tmp_path / "proyek"
    proyek.mkdir()
    (proyek / "app").mkdir()
    (proyek / "app" / "main.py").write_text("print('hai')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=proyek, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proyek, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "awal"], cwd=proyek, check=True)
    return proyek


def _ls_files(proyek):
    r = subprocess.run(
        ["git", "ls-files"], cwd=proyek, capture_output=True, text=True, check=True
    )
    return r.stdout.splitlines()


def test_gitignore_dibuat_dan_artefak_tak_masuk(repo):
    (repo / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "frontend" / "node_modules" / "pkg" / "i.js").write_text("x", encoding="utf-8")
    (repo / "app" / "__pycache__").mkdir()
    (repo / "app" / "__pycache__" / "main.pyc").write_text("x", encoding="utf-8")
    (repo / ".orion").mkdir()
    (repo / ".orion" / "session_history.json").write_text("{}", encoding="utf-8")
    (repo / "src_baru.py").write_text("x = 1\n", encoding="utf-8")

    hasil = ToolExecutor(str(repo)).git_commit("checkpoint")

    assert hasil.startswith("Success: git checkpoint created.")
    assert ".gitignore created" in hasil
    assert "Staged files in this commit:" in hasil
    isi_gi = (repo / ".gitignore").read_text(encoding="utf-8")
    for pola in ("node_modules/", ".orion/", "__pycache__/", "*.pyc"):
        assert pola in isi_gi
    terlacak = _ls_files(repo)
    assert not any("node_modules" in b for b in terlacak)
    assert not any(b.startswith(".orion/") for b in terlacak)
    assert not any(b.endswith(".pyc") for b in terlacak)
    assert "src_baru.py" in terlacak
    # files stay on disk (index-only removal)
    assert (repo / "frontend" / "node_modules" / "pkg" / "i.js").exists()
    assert (repo / ".orion" / "session_history.json").exists()


def test_repo_kotor_lama_disembuhkan_saat_checkpoint(repo):
    # Simulate the InvoiceFlow state: node_modules + .orion ALREADY
    # tracked by an earlier blind commit.
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "i.js").write_text("x", encoding="utf-8")
    (repo / ".orion").mkdir()
    (repo / ".orion" / "session_history.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "blind"], cwd=repo, check=True)
    assert any("node_modules" in b for b in _ls_files(repo))

    hasil = ToolExecutor(str(repo)).git_commit("heal")

    assert "untracked (kept on disk)" in hasil
    terlacak = _ls_files(repo)
    assert not any("node_modules" in b for b in terlacak)
    assert not any(b.startswith(".orion/") for b in terlacak)
    assert (repo / "node_modules" / "pkg" / "i.js").exists()


def test_gitignore_ada_ditambal_bukan_ditimpa(repo):
    (repo / ".gitignore").write_text("node_modules/\n*.log\n", encoding="utf-8")
    (repo / "baru.txt").write_text("x\n", encoding="utf-8")
    hasil = ToolExecutor(str(repo)).git_commit("topup")
    assert "topped up" in hasil
    isi = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in isi and "node_modules/" in isi and ".orion/" in isi
    # the user's own line survived and was not duplicated
    assert isi.count("node_modules/") == 1


def test_bukan_repo_ditolak_dan_pohon_bersih_tetap_jujur(tmp_path):
    kosong = tmp_path / "bukan-repo"
    kosong.mkdir()
    hasil = ToolExecutor(str(kosong)).git_commit("x")
    assert hasil.startswith("Error: this project folder is NOT a git repo")
