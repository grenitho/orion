"""
test_security_posture.py — guardian of the security posture (ORION-37)
=======================================================================
These tests do not exercise behaviour; they REJECT CONFIGURATION REGRESSIONS
that once formed real security holes:

  1. CORS `allow_origins=["*"]` + /execute-terminal without auth = local RCE
     via a malicious web page while the backend is alive.
  2. Backend binding to a non-loopback interface.
  3. The owner's personal paths embedded in published code.
  4. Personal artifacts (.orion, .vsix) leaking into the package/repo.

If someone "tidies up" this configuration and opens it back up, these tests
fail before that code can ever be pushed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

def _cari_root_repo() -> Path | None:
    """Walk up from this file until finding the folder that contains backend/ AND orion/.
    Does not rely on a fixed depth, so the test stays correct even when the
    folder structure changes or it runs from a temporary harness."""
    sini = Path(__file__).resolve()
    for induk in sini.parents:
        if (induk / "backend" / "app" / "main.py").is_file() and (induk / "orion").is_dir():
            return induk
        if induk == induk.parent:
            break
    return None


REPO = _cari_root_repo()
MAIN = REPO / "backend" / "app" / "main.py" if REPO else Path(__file__)
BACKEND_MANAGER = REPO / "orion" / "src" / "services" / "backendManager.ts" if REPO else Path(__file__)

pytestmark = pytest.mark.skipif(REPO is None, reason="root repo ORION tidak ditemukan")


def test_cors_tidak_terbuka_ke_semua_origin():
    """Check the ASSIGNMENT LINE only, not raw substrings: a comment explaining
    the old bug may legitimately mention the old shape."""
    src = MAIN.read_text(encoding="utf-8")
    nilai = [
        b.split("=", 1)[1].strip()
        for b in src.splitlines()
        if b.strip().startswith("allow_origins=")
    ]
    assert nilai, "allow_origins tidak ditemukan di konfigurasi CORS"
    assert '"*"' not in nilai and "'*'" not in nilai, (
        f"CORS terbuka ke semua origin ({nilai}): halaman web jahat bisa memanggil "
        "/execute-terminal di 127.0.0.1 dan menjalankan perintah shell."
    )


def test_backend_bind_loopback():
    src = BACKEND_MANAGER.read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in src or "--host localhost" in src, (
        "uvicorn harus bind eksplisit ke loopback; tanpa --host beberapa "
        "versi/konfigurasi bisa jatuh ke antarmuka jaringan lain."
    )


def test_tidak_ada_path_pribadi_di_kode():
    NAMA = "Gren" + "itho"
    pola = re.compile(NAMA + r"|C:\\Users\\ASUS|D:\\APP", re.I)
    berkas = list(REPO.rglob("backend/app/**/*.py")) + list(REPO.rglob("orion/src/**/*.ts"))
    bocor = []
    for f in berkas:
        if "__pycache__" in f.parts or "node_modules" in f.parts:
            continue
        if pola.search(f.read_text(encoding="utf-8", errors="replace")):
            bocor.append(str(f.relative_to(REPO)))
    assert not bocor, f"Path/identitas pribadi tertanam di: {bocor}"


def test_artefak_pribadi_tidak_ikut_kemasan():
    vsc = (REPO / "orion" / ".vscodeignore").read_text(encoding="utf-8")
    assert ".orion/**" in vsc, ".orion berisi riwayat percakapan; wajib di luar VSIX"
    assert "*.vsix" in vsc, "VSIX lama mengandung .orion/session_history.json"
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".orion" in gi, ".orion wajib di-ignore git agar riwayat chat tidak ter-push"
