"""UPDATE-026 commit 33 — closing echo/monologue strip + respon log.

Evidence (owner incident 2026-11-02, confirmation-only turn on the
owner's machine): the closing that reached the chat panel opened with
SIX English monologue paragraphs ("Look at `app/routers/customers.py`
lines 88-95!", "Wait, …", "Let's check …", a backticked quote of the
turn's prompt, a backticked quote of the mid-turn challenge, "Let's
address this directly …") and quoted "[SISTEM - PEMERIKSAAN BUKTI]: …"
verbatim INSIDE the user frame; the owner could not find the real
answer ("Fakta verifikasi saat ini: …") buried below it. Three
deterministic holes, three code teeth:

1. ``prose_perencanaan_closing`` — closing-only superset of the
   planning-prose predicate: the wild openers ("look at …", "the
   user's prompt …", "and the system check …") join the shape rules;
   every escape (decline / user-addressed / presentational start) is
   shared with the base predicate.
2. ``buang_gema_suntikan`` — paragraphs echoing the challenge injection
   markers or quoting the turn's prompt/system-check are scaffolding:
   removed ANYWHERE in a closing; ``catatan_untuk_user`` refuses them
   for tool-call content too (the "anda" inside the quote no longer
   buys a user-addressed escape). The honest-decline escape still
   outranks both suppressors.
3. ``rekam_respon_mentah`` + logging_setup's ``orion.respon`` logger —
   the RAW closing is appended to ``.orion/logs/respon_orion.log``
   (rotation 5 MB x 3) so the chat panel carries only cleaned text
   while forensics stay recoverable (owner directive 2026-11-02).
"""

import logging.handlers
import pathlib

from app import logging_setup
from app.services import stream_utils as su

_MONOLOG_BOCOR = (
    "Look at `app/routers/customers.py` lines 88-95!\n\n"
    "Wait, `delete_customer` actually soft-deletes unless there is "
    "another check or error message the user mentioned: \"Pelanggan "
    "masih memiliki faktur.\" Let's search across the codebase for "
    "\"faktur\" or \"invoice\" or delete error messages.\n\n"
    "Let's check the user request and system prompt instructions "
    "carefully.\n\n"
    "The user's prompt in this turn:\n`orion, baca file yang saya "
    "lampirkan ini. Saya Mau tampilan front end seperti petunjuk yang "
    "ada didalam file ini.`\n\n"
    "And the system check says:\n`[SISTEM - PEMERIKSAAN BUKTI]: Anda "
    "memakai kata-kata penyelesaian, tetapi TIDAK ADA file yang berubah "
    "pada turn ini. jawab ITU lebih dulu, secara langsung.`\n\n"
    "Let's address this directly and factually in Indonesian:\n1. Jawab "
    "langsung permintaan pengguna. 2. Jangan menggunakan kata-kata klaim "
    "pytest penyelesaian.\n\n"
    "Let's do a quick terminal check of pytest to have factual "
    "verification on hand.\n\n"
    "Fakta verifikasi saat ini:\n- Pengujian backend menunjukkan 7 tes "
    "lolos tanpa kegagalan.\n\n"
    "Tidak ada keraguan mendasar. Apakah ada bagian spesifik yang ingin "
    "diprioritaskan terlebih dahulu?"
)

_JAWABAN = "Fakta verifikasi saat ini:"


def test_monolog_dan_gema_dibersihkan_jawaban_tertimbang():
    bersih = su.bersih_closing(_MONOLOG_BOCOR)
    assert bersih.startswith(_JAWABAN)
    assert "[SISTEM - PEMERIKSAAN BUKTI]" not in bersih
    assert "Look at `app/routers/customers.py`" not in bersih
    assert "The user's prompt in this turn" not in bersih
    # jawaban asli dan pertanyaan penutup tetap sampai ke user
    assert "7 tes lolos" in bersih
    assert bersih.rstrip().endswith("terlebih dahulu?")


def test_gema_marker_dibuang_di_tengah_teks():
    teks = (
        "Jawaban: registry sudah saya baca di `app/main.py` baris 12.\n\n"
        "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata "
        "penyelesaian, tetapi TIDAK ADA file yang berubah.\n\n"
        "Silakan periksa hasilnya dan beri tahu saya langkah berikutnya."
    )
    bersih = su.bersih_closing(teks)
    assert "[SISTEM - PEMERIKSAAN BUKTI]" not in bersih
    assert "registry sudah saya baca" in bersih
    assert "Silakan periksa hasilnya" in bersih


def test_gema_marker_bahasa_inggris_juga_dibuang():
    teks = (
        "[SYSTEM - EVIDENCE CHECK]: You used completion wording, but NO "
        "file changed on this turn.\n\n"
        "Here is the factual state of the project."
    )
    bersih = su.bersih_closing(teks)
    assert "[SYSTEM - EVIDENCE CHECK]" not in bersih
    assert "Here is the factual state" in bersih


def test_semua_paragraf_gema_teks_tidak_berubah():
    teks = (
        "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata "
        "penyelesaian.\n\n"
        "And the system check says:\n`[SISTEM - PEMERIKSAAN BUKTI] …`"
    )
    assert su.bersih_closing(teks) == teks


def test_penolakan_jujur_tetap_lolos_di_depan_monolog():
    tolak = (
        "Maaf, saya tidak bisa mengambil screenshot karena tool "
        "browser_take_screenshot tidak tersedia di konfigurasi MCP "
        "Anda; saya tidak akan berpura-pura sebaliknya."
    )
    teks = tolak + "\n\nLook at the config file `mcp.json` for the allowlist."
    bersih = su.bersih_closing(teks)
    assert bersih.startswith("Maaf, saya tidak bisa mengambil screenshot")
    assert "tidak tersedia" in bersih


def test_catatan_untuk_user_menolak_blob_bermarker():
    blob = (
        "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata "
        "penyelesaian, tetapi TIDAK ADA file yang berubah."
    )
    assert su.catatan_untuk_user(blob) is False
    # penolakan jujur tetap mengungguli supresi marker
    tolak = (
        "Saya tidak bisa melanjutkan: server tidak responds. "
        "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata."
    )
    assert su.catatan_untuk_user(tolak) is True


def test_prose_perencanaan_dasar_tidak_berubah():
    # gate tool-call tetap memakai predikat dasar untuk bentuk lama
    assert su.prose_perencanaan("Let's check git_diff now.") is True
    # paragraf jawaban user-facing tetap menghentikan rantai
    assert su.prose_perencanaan_closing(
        "Fakta verifikasi saat ini: 7 tes lolos."
    ) is False


class _LoggerPalsu:
    def __init__(self):
        self.pesan = []

    def info(self, fmt, *args):
        self.pesan.append(fmt % args if args else fmt)


def test_rekam_respon_mentah_menyimpan_raw_dan_clean():
    log = _LoggerPalsu()
    su.rekam_respon_mentah(_MONOLOG_BOCOR, "Fakta verifikasi saat ini: x",
                           logger=log)
    assert len(log.pesan) == 1
    assert "----- RAW -----" in log.pesan[0]
    assert "----- CLEAN -----" in log.pesan[0]
    assert "[SISTEM - PEMERIKSAAN BUKTI]" in log.pesan[0]
    log2 = _LoggerPalsu()
    su.rekam_respon_mentah("sama", "sama", logger=log2)
    assert "clean, no strip" in log2.pesan[0]


def test_rekam_tidak_meledak_tanpa_logger():
    # logger None + logging_setup gagal sekalipun tidak boleh raise
    assert su.rekam_respon_mentah("a", "b", logger=None) is None


def test_logger_respon_terdaftar_ke_file_sendiri():
    logger = logging_setup.get_logger("orion.respon")
    assert logger.propagate is False
    target = [
        h for h in logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
        and pathlib.Path(h.baseFilename).name == "respon_orion.log"
    ]
    assert target, "logger orion.respon harus menulis respon_orion.log"


def test_wiring_closing_memakai_bersih_dan_rekam():
    src = pathlib.Path(su.__file__).parent / "agent_loop.py"
    teks = src.read_text(encoding="utf-8")
    assert teks.count("stream_utils.bersih_closing(") == 2
    assert teks.count("stream_utils.rekam_respon_mentah(") == 2
    # kedua situs closing tak lagi memanggil stripper lama langsung
    assert "buang_paragraf_perencanaan_awal" not in teks


def test_wiring_logging_setup_file_respon():
    src = pathlib.Path(logging_setup.__file__).read_text(encoding="utf-8")
    assert "respon_orion.log" in src
    assert 'logging.getLogger("orion.respon")' in src
