"""
logging_setup.py
================
PENGGANTI `print()` untuk backend ORION.

KENAPA INI PENTING (ini kemungkinan penyebab "berhenti diam-diam"):

Backend ORION dijalankan sebagai PROSES ANAK oleh extension VS Code
(lihat start-backend.bat). Komentar Anda sendiri di llm_router.py sudah
menemukan mekanismenya:

    "...ini bisa membanjiri pipe stdout yang ditangkap proses induk
     (mis. ekstensi VS Code). Kalau proses induk tidak membaca cukup
     cepat, backend bisa macet menunggu buffer kosong (backpressure),
     lalu di-kill paksa oleh watchdog/health-check proses induk."

Anda sudah memperbaiki `litellm._turn_on_debug()` — bagus. Tapi masih ada
**122 `print()`** di backend. Setiap `print()` menulis ke pipe yang sama.
Saat agent menjalankan banyak langkah, pipe penuh, backend tersumbat, lalu mati.
Dari sisi user: assistant berhenti tanpa pesan apa pun.

SOLUSI: log ke FILE, bukan ke stdout. stdout dibiarkan nyaris kosong.

CARA PAKAI (ganti semua `print(...)` di backend):

    from logging_setup import get_logger
    log = get_logger(__name__)

    log.info("Mencoba model %s", model_name)
    log.warning("Rate limit: %s", e)
    log.exception("Gagal tak terduga")     # <- otomatis menyertakan traceback

File log ada di:  <proyek>/.orion/logs/backend.log  (rotasi 5 MB x 5 file)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_DISETUP = False
_LOG_DIR: Path | None = None


def _cari_folder_log() -> Path:
    """Cari .orion/logs, naik ke atas dari lokasi file ini."""
    env = os.getenv("ORION_LOG_DIR")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for induk in [here.parent, *here.parents]:
        kandidat = induk / ".orion"
        if kandidat.is_dir():
            return kandidat / "logs"
        if (induk / "backend").is_dir() and (induk / ".orion").is_dir():
            return induk / ".orion" / "logs"
    return Path.cwd() / "logs"


def setup_logging(level: str | None = None, *, ke_stdout: bool = False) -> Path:
    """Panggil SEKALI saat aplikasi mulai (di main.py, paling atas).

    ke_stdout=False (default) -> log HANYA ke file. Ini yang Anda inginkan,
    karena stdout adalah pipe ke extension VS Code dan tidak boleh dibanjiri.
    """
    global _DISETUP, _LOG_DIR
    if _DISETUP and _LOG_DIR is not None:
        return _LOG_DIR

    _LOG_DIR = _cari_folder_log()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    akar = logging.getLogger()
    akar.setLevel((level or os.getenv("LOG_LEVEL", "INFO")).upper())
    # bersihkan handler lama (uvicorn sering sudah menambah beberapa)
    for h in list(akar.handlers):
        akar.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    berkas = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    berkas.setFormatter(fmt)
    akar.addHandler(berkas)

    if ke_stdout:
        # hanya untuk debugging manual di terminal, JANGAN di produksi
        layar = logging.StreamHandler(sys.stdout)
        layar.setFormatter(fmt)
        akar.addHandler(layar)

    # Redam log berisik dari pustaka pihak ketiga
    for nama in ("litellm", "LiteLLM", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(nama).setLevel(logging.WARNING)

    _DISETUP = True
    return _LOG_DIR


def get_logger(nama: str) -> logging.Logger:
    """Ambil logger. Otomatis menjalankan setup kalau belum."""
    if not _DISETUP:
        setup_logging()
    return logging.getLogger(nama)


def path_log() -> str:
    """Path file log saat ini — untuk ditampilkan ke user saat debugging."""
    if _LOG_DIR is None:
        setup_logging()
    return str(_LOG_DIR / "backend.log")


# ---------------------------------------------------------------------------
# Trik migrasi bertahap: selama Anda belum sempat mengganti 122 print(),
# pasang ini di main.py PALING ATAS untuk mengalihkan print() ke file log.
# Ini jaring pengaman sementara, bukan solusi akhir.
#
#     import logging_setup
#     logging_setup.setup_logging()
#     logging_setup.alihkan_print()      # <- sementara
#
def alihkan_print() -> None:
    """Ubah print() global supaya menulis ke file log, bukan stdout.

    PENTING: ini solusi sementara. Tujuan akhirnya tetap mengganti print()
    dengan log.info()/log.warning() yang sesungguhnya.
    """
    if not _DISETUP:
        setup_logging()
    import builtins

    _asli = builtins.print
    logger = logging.getLogger("stdout")

    def print_ke_log(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        # hanya alihkan kalau memang menuju stdout (bukan ke file lain)
        if kwargs.get("file") not in (None, sys.stdout):
            return _asli(*args, **kwargs)
        try:
            sep = kwargs.get("sep", " ")
            logger.info(sep.join(str(a) for a in args))
        except Exception:  # noqa: BLE001 - jangan pernah gagal karena logging
            pass

    builtins.print = print_ke_log  # type: ignore[assignment]
