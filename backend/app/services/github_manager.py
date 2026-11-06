"""
github_manager.py — Safe GitHub integration via gh CLI (P5)
"""

import subprocess
import shutil
import json


import os


def _cek_gh() -> bool:
    if shutil.which("gh") is not None:
        return True
    # Paksa cek lokasi default Windows jika PATH gagal
    if os.name == "nt":
        default_path = r"C:\Program Files\GitHub CLI\gh.exe"
        if os.path.isfile(default_path):
            return True
    return False


def _jalankan_gh(args: list[str], cwd: str) -> tuple[bool, str]:
    gh_exe = shutil.which("gh")
    if not gh_exe and os.name == "nt":
        default_path = r"C:\Program Files\GitHub CLI\gh.exe"
        if os.path.isfile(default_path):
            gh_exe = default_path

    if not gh_exe:
        return False, "Error: GitHub CLI (gh) tidak terinstal di sistem ini."

    try:
        r = subprocess.run(
            [gh_exe] + args, cwd=cwd, capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, f"GitHub Error: {r.stderr.strip()}"
    except Exception as e:
        return False, f"Error menjalankan gh: {e!s}"


def list_issues(project_path: str, limit: int = 5) -> str:
    """Melihat daftar issue terbuka di repo GitHub."""
    ok, hasil = _jalankan_gh(
        ["issue", "list", "--limit", str(limit), "--json", "number,title,labels,state"],
        project_path,
    )
    if not ok:
        return hasil

    try:
        data = json.loads(hasil)
        if not data:
            return "Tidak ada issue terbuka."
        teks = "Daftar Issue Terbuka:\n"
        for i in data:
            label = ", ".join([l["name"] for l in i.get("labels", [])])
            teks += f"- #{i['number']} [{i['title']}] (Label: {label or 'none'})\n"
        return teks
    except Exception:
        return hasil


def create_pr(
    project_path: str, title: str, body: str, base_branch: str = "main"
) -> str:
    """Membuat Pull Request baru. Hanya bisa create, tidak bisa merge/delete."""
    ok, hasil = _jalankan_gh(
        ["pr", "create", "--title", title, "--body", body, "--base", base_branch],
        project_path,
    )
    if not ok:
        return hasil
    return f"Pull Request berhasil dibuat:\n{hasil}"


def get_repo_info(project_path: str) -> str:
    """Melihat info repo saat ini (nama, owner, url)."""
    ok, hasil = _jalankan_gh(["repo", "view", "--json", "name,owner,url"], project_path)
    if not ok:
        return hasil
    return hasil
