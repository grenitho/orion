"""
test_security_posture.py — penjaga postur keamanan (ORION-37)
==============================================================
Test ini tidak menguji perilaku, melainkan MENOLAK REGRESI KONFIGURASI yang
pernah membentuk lubang keamanan nyata:

  1. CORS `allow_origins=["*"]` + /execute-terminal tanpa auth = RCE lokal
     lewat halaman web jahat selagi backend hidup.
  2. Backend bind ke antarmuka selain loopback.
  3. Path pribadi pemilik tertanam di kode yang dipublikasikan.
  4. Artefak pribadi (.orion, .vsix) tidak masuk kemasan/repo.

Kalau seseorang "merapikan" konfigurasi ini dan membukanya kembali,
test ini gagal sebelum kode itu sempat ter-push.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

def _cari_root_repo() -> Path | None:
    """Naik dari file ini sampai menemukan folder yang memuat backend/ DAN orion/.
    Tidak mengandalkan kedalaman tetap, supaya test tetap benar walau struktur
    folder berubah atau dijalankan dari harness sementara."""
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
    """Periksa BARIS ASSIGNMENT saja, bukan substring mentah: komentar yang
    menjelaskan bug lama wajar menyebut bentuk lamanya."""
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
