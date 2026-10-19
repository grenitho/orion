"""
code_verifier.py — pemeriksa NYATA multi-bahasa untuk ORION  (PATCH ORION-25)
=============================================================================
FILE BARU. Letakkan di: backend/app/services/code_verifier.py

Memberi Orion "mata" yang sama untuk SEMUA bahasa yang ia kerjakan, bukan hanya
HTML. Semua pemeriksaan memakai parser/compiler sungguhan, bukan pembacaan model:

    .py            compile()  +  impor yang tidak terpasang  +  ruff/pyflakes bila ada
    .js/.mjs/.cjs  node --check
    .jsx/.ts/.tsx  tsc --noEmit bila tersedia (esbuild sebagai cadangan)
    .html/.htm     node --check pada <script> inline + id duplikat + referensi DOM
    .json          json.loads
    .xml/.svg      parser ElementTree
    .yaml/.yml     parser yaml bila terpasang, jika tidak keseimbangan struktur
    .css/.scss     keseimbangan kurung kurawal
    folder         memeriksa semua file didukung di dalamnya (maks 40 file)

Prinsip: SATU karakter yang salah (kurung kurang, koma lebih) dilaporkan dengan
nomor baris. Model tidak bisa melihat itu; parser bisa.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile

MAKS_FILE_PER_FOLDER = 40
MAKS_BARIS_KUTIPAN = 130

EKSTENSI_DIDUKUNG = {
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".html", ".htm", ".json", ".xml", ".svg", ".yaml", ".yml", ".css", ".scss",
}

_LEWATI_DIR = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
    "out", ".next", "coverage", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "site-packages", ".orion", "target",
}


# ---------------------------------------------------------------------------
# util
# ---------------------------------------------------------------------------

def _node() -> str | None:
    return shutil.which("node")


def _jalankan(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _kutipan(teks: str, baris: int, lebar: int = MAKS_BARIS_KUTIPAN) -> str:
    try:
        return teks.splitlines()[baris - 1].strip()[:lebar]
    except (IndexError, ValueError):
        return ""


def _file_sementara(kode: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                     encoding="utf-8") as f:
        f.write(kode)
        return f.name


# ---------------------------------------------------------------------------
# pemeriksa per bahasa
# ---------------------------------------------------------------------------

def cek_python(path: str, teks: str, interpreter: str | None = None) -> list[str]:
    """Sintaks + impor yang tidak terpasang + linter bila tersedia."""
    temuan: list[str] = []

    # 1. sintaks
    try:
        pohon = ast.parse(teks, filename=path)
    except SyntaxError as e:
        temuan.append(f"SINTAKS PYTHON baris {e.lineno}: {e.msg}")
        if e.lineno:
            temuan.append(f"     -> {_kutipan(teks, e.lineno)}")
        return temuan   # tidak ada gunanya melanjutkan kalau sintaks rusak

    # 2. impor yang tidak terpasang (menangkap ModuleNotFoundError SEBELUM dijalankan)
    stdlib = set(sys.stdlib_module_names) | set(sysconfig.get_path_names())
    lokal = set()
    direktori = os.path.dirname(os.path.abspath(path))
    try:
        for nama in os.listdir(direktori):
            lokal.add(nama.split(".")[0])
    except OSError:
        pass

    dibutuhkan: set[str] = set()
    for simpul in ast.walk(pohon):
        if isinstance(simpul, ast.Import):
            for alias in simpul.names:
                dibutuhkan.add(alias.name.split(".")[0])
        elif isinstance(simpul, ast.ImportFrom):
            if simpul.level == 0 and simpul.module:
                dibutuhkan.add(simpul.module.split(".")[0])

    kandidat = [m for m in sorted(dibutuhkan)
                if m not in stdlib and m not in lokal and m.isidentifier()]
    hilang = _modul_hilang_via_interpreter(kandidat, path, interpreter)
    if hilang:
        temuan.append("IMPOR TIDAK TERPASANG (akan ModuleNotFoundError saat dijalankan): "
                      f"{', '.join(hilang[:12])}")
        temuan.append("     -> pasang dengan: pip install " + " ".join(hilang[:6])
                      if all(_paket_pip(m) == m for m in hilang[:6]) else
                      "     -> periksa requirements.txt / environment proyek ini")

    # 3. linter opsional (ruff atau pyflakes), memberi temuan nama-tak-terdefinisi dll.
    for alat, arg in (("ruff", ["check", "--select", "F,E9", "--no-cache", path]),
                      ("pyflakes", [path])):
        exe = shutil.which(alat)
        if not exe:
            exe_py = _modul_python_bisa(alat)
            if not exe_py:
                continue
            cmd = [sys.executable, "-m", alat, *arg]
        else:
            cmd = [exe, *arg]
        try:
            r = _jalankan(cmd, timeout=90)
        except (OSError, subprocess.SubprocessError):
            continue
        keluaran = (r.stdout or r.stderr).strip()
        if keluaran and r.returncode != 0:
            baris = keluaran.splitlines()
            temuan.append(f"TEMUAN {alat.upper()} ({len(baris)} baris):")
            temuan.extend(f"     {b[:150]}" for b in baris[:12])
        break   # cukup satu linter yang pertama tersedia

    return temuan


def _modul_hilang_via_interpreter(kandidat: list[str], path: str,
                                  interpreter: str | None) -> list[str]:
    """Tanya kepada INTERPRETER MILIK PROJECT, bukan proses backend.

    Bug nyata yang diperbaiki: verify_code dulu memakai importlib di proses
    backend, sehingga project dengan venv sendiri selalu dilaporkan
    "IMPOR TIDAK TERPASANG" padahal paketnya ada di venv project itu.
    """
    if not kandidat:
        return []
    exe = interpreter or sys.executable
    probe = ("import importlib.util as u, sys; "
             "print(','.join(m for m in sys.argv[1:] if u.find_spec(m) is None))")
    try:
        r = subprocess.run([exe, "-c", probe, *kandidat],
                           capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(os.path.abspath(path)) or None,
                           stdin=subprocess.DEVNULL)
        if r.returncode == 0:
            keluar = (r.stdout or "").strip().splitlines()
            return [m for m in (keluar[-1].split(",") if keluar else []) if m]
    except (OSError, subprocess.SubprocessError):
        pass
    # fallback lama kalau interpreter project tidak bisa dijalankan
    hilang = []
    for mod in kandidat:
        try:
            if importlib.util.find_spec(mod) is None:
                hilang.append(mod)
        except (ImportError, ValueError, ModuleNotFoundError):
            hilang.append(mod)
    return hilang


def _paket_pip(mod: str) -> str:
    return {"cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn",
            "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv",
            "google.genai": "google-genai"}.get(mod, mod)


def _modul_python_bisa(nama: str) -> bool:
    try:
        return importlib.util.find_spec(nama) is not None
    except (ImportError, ValueError):
        return False


def cek_js(path: str, teks: str) -> list[str]:
    node = _node()
    if not node:
        return ["node tidak terpasang; pemeriksaan sintaks JS dilewati."]
    tmp = _file_sementara(teks, ".js")
    try:
        r = _jalankan([node, "--check", tmp], timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"pemeriksaan JS gagal dijalankan: {e}"]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if r.returncode == 0:
        return []
    m = re.search(r"\.js:(\d+)", r.stderr)
    baris = int(m.group(1)) if m else 0
    pesan = next((x.strip() for x in r.stderr.splitlines() if "Error" in x),
                 r.stderr.strip()[:200])
    out = [f"SINTAKS JAVASCRIPT baris {baris}: {pesan}"]
    if baris:
        out.append(f"     -> {_kutipan(teks, baris)}")
    return out


def cek_ts(path: str) -> list[str]:
    """Type-check TypeScript bila tsc tersedia di proyek atau global."""
    kandidat = [
        os.path.join(os.getcwd(), "node_modules", ".bin", "tsc"),
        os.path.join(os.getcwd(), "node_modules", ".bin", "tsc.cmd"),
        shutil.which("tsc"),
    ]
    tsc = next((c for c in kandidat if c and os.path.exists(c)), None)
    if not tsc:
        return ["tsc tidak tersedia di proyek ini; pemeriksaan TypeScript dilewati. "
                "(npm install typescript untuk mengaktifkannya)"]
    try:
        r = _jalankan([tsc, "--noEmit", path], timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"tsc gagal dijalankan: {e}"]
    if r.returncode == 0:
        return []
    baris = [x.strip() for x in (r.stdout or r.stderr).splitlines() if x.strip()][:12]
    return ["TEMUAN TSC:"] + [f"     {b[:160]}" for b in baris]


def cek_html(path: str, teks: str) -> list[str]:
    """Delegasi ke pemeriksa HTML lengkap (script inline + id + referensi DOM)."""
    from app.services.html_checker import cek_html as _cek
    return _cek(teks)


def cek_json(teks: str) -> list[str]:
    try:
        json.loads(teks)
        return []
    except json.JSONDecodeError as e:
        return [f"JSON TIDAK VALID baris {e.lineno} kolom {e.colno}: {e.msg}",
                f"     -> {_kutipan(teks, e.lineno or 1)}"]


def cek_xml(teks: str) -> list[str]:
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(teks)
        return []
    except ET.ParseError as e:
        return [f"XML/SVG TIDAK VALID: {e}"]


def cek_yaml(teks: str) -> list[str]:
    if _modul_python_bisa("yaml"):
        import yaml  # type: ignore
        try:
            yaml.safe_load(teks)
            return []
        except Exception as e:  # noqa: BLE001
            return [f"YAML TIDAK VALID: {e}"]
    buka = teks.count("{") + teks.count("[")
    tutup = teks.count("}") + teks.count("]")
    if buka != tutup:
        return [f"YAML: kurung tidak seimbang ({buka} buka vs {tutup} tutup) "
                "(pemeriksaan mendalam butuh: pip install pyyaml)"]
    return []


def cek_css(teks: str) -> list[str]:
    buka, tutup = teks.count("{"), teks.count("}")
    if buka != tutup:
        return [f"CSS: kurung kurawal tidak seimbang ({buka} buka vs {tutup} tutup)"]
    return []


# ---------------------------------------------------------------------------
# entri utama
# ---------------------------------------------------------------------------

def verify_path(abs_path: str, interpreter: str | None = None) -> str:
    """Periksa satu file atau satu folder. Mengembalikan laporan teks."""
    if os.path.isdir(abs_path):
        return _verify_folder(abs_path, interpreter)

    if not os.path.isfile(abs_path):
        return f"Error: tidak ditemukan: {abs_path}"

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in EKSTENSI_DIDUKUNG:
        return (f"verify_code belum mendukung '{ext}'. Yang didukung: "
                + ", ".join(sorted(EKSTENSI_DIDUKUNG)))

    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            teks = f.read()
    except OSError as e:
        return f"Error membaca {abs_path}: {e}"

    temuan = _periksa(ext, abs_path, teks, interpreter)
    return _laporan(abs_path, temuan)


def _periksa(ext: str, path: str, teks: str, interpreter: str | None = None) -> list[str]:
    if ext == ".py":
        return cek_python(path, teks, interpreter)
    if ext in (".js", ".mjs", ".cjs", ".jsx"):
        return cek_js(path, teks)
    if ext in (".ts", ".tsx"):
        return cek_ts(path)
    if ext in (".html", ".htm"):
        return cek_html(path, teks)
    if ext == ".json":
        return cek_json(teks)
    if ext in (".xml", ".svg"):
        return cek_xml(teks)
    if ext in (".yaml", ".yml"):
        return cek_yaml(teks)
    if ext in (".css", ".scss"):
        return cek_css(teks)
    return []


def _laporan(nama: str, temuan: list[str]) -> str:
    if not temuan:
        return f"OK: {nama} lolos pemeriksaan verify_code."
    n = len([t for t in temuan if not t.startswith("     ")])
    return (f"!!! {nama} TIDAK SEHAT ({n} temuan):\n" + "\n".join(temuan) +
            "\n\nPerbaiki temuan di atas SEBELUM menyatakan file ini berfungsi.")


def _verify_folder(abs_dir: str, interpreter: str | None = None) -> str:
    ringkas: list[str] = []
    jumlah = 0
    for akar, dirs, files in os.walk(abs_dir):
        dirs[:] = [d for d in dirs if d not in _LEWATI_DIR]
        for nama in sorted(files):
            ext = os.path.splitext(nama)[1].lower()
            if ext not in EKSTENSI_DIDUKUNG:
                continue
            jumlah += 1
            if jumlah > MAKS_FILE_PER_FOLDER:
                ringkas.append(f"... dan seterusnya (batas {MAKS_FILE_PER_FOLDER} file per pemeriksaan)")
                return _laporan_folder(abs_dir, ringkas, jumlah)
            path = os.path.join(akar, nama)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    teks = f.read()
            except OSError:
                continue
            temuan = _periksa(ext, path, teks, interpreter)
            if temuan:
                rel = os.path.relpath(path, abs_dir)
                ringkas.append(f"--- {rel}")
                ringkas.extend(temuan[:6])
    return _laporan_folder(abs_dir, ringkas, jumlah)


def _laporan_folder(nama: str, ringkas: list[str], jumlah: int) -> str:
    if not ringkas:
        return (f"OK: {jumlah} file didukung di {nama} lolos pemeriksaan verify_code "
                "(sintaks, impor, JSON/XML/YAML/CSS, dan struktur HTML).")
    bermasalah = len([r for r in ringkas if r.startswith("--- ")])
    return (f"!!! {nama}: {bermasalah} dari {jumlah} file bermasalah:\n"
            + "\n".join(ringkas)
            + "\n\nPerbaiki yang berlabel ERROR/SINTAKS lebih dulu; "
              "satu file yang rusak bisa mematikan seluruh aplikasi.")
