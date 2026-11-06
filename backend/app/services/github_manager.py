"""
github_manager.py — Safe GitHub integration via gh CLI (P5)
"""

import subprocess
import shutil
import json


def _cek_gh() -> bool:
    return shutil.which("gh") is not None


def _jalankan_gh(args: list[str], cwd: str) -> tuple[bool, str]:
    if not _cek_gh():
        return (
            False,
            "Error: GitHub CLI (gh) tidak terinstal di sistem ini. Minta user untuk menginstalnya.",
        )

    try:
        # Timeout 30 detik agar tidak menggantung
        r = subprocess.run(
            ["gh"] + args, cwd=cwd, capture_output=True, text=True, timeout=30
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
    if ok:
        return hasil

    # Fallback ke git remote origin jika gh CLI tidak ada / error
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0:
            url = res.stdout.strip()
            owner = ""
            name = ""
            if "github.com" in url:
                if ":" in url and "@" in url:
                    path_part = url.split(":")[-1]
                else:
                    path_part = url.split("github.com/")[-1]
                path_part = path_part.removesuffix(".git")
                subparts = path_part.split("/")
                if len(subparts) >= 2:
                    owner = subparts[0]
                    name = subparts[1]
            return json.dumps(
                {
                    "owner": owner,
                    "name": name,
                    "url": url,
                    "note": "Diperoleh via git remote fallback (GitHub CLI tidak aktif).",
                },
                indent=2,
            )
    except Exception:
        pass

    return hasil
