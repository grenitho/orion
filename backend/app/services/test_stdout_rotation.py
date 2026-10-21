"""
test_stdout_rotation.py — the rotating stdout sink (UPDATE-013)
===============================================================
Guards the two promises main.py now makes when it redirects sys.stdout:

  1. an oversized log is rotated instead of growing forever (the pre-013
     sink was a plain append; real logs reached megabytes);
  2. rotation NEVER breaks writing — a print() must succeed before, during
     and after a generation shift, and the content is never lost silently.

MAX_BYTES/BACKUP_COUNT are monkeypatched small so the tests stay fast and
never touch the real project log.
"""

from __future__ import annotations

from app import stdout_rotation
from app.stdout_rotation import RotatingFileWriter, rotate_now


def _shrink(monkeypatch, max_bytes: int = 200, backups: int = 2) -> None:
    monkeypatch.setattr(stdout_rotation, "MAX_BYTES", max_bytes)
    monkeypatch.setattr(stdout_rotation, "BACKUP_COUNT", backups)


def test_rotate_now_noop_bila_kecil(tmp_path, monkeypatch):
    _shrink(monkeypatch)
    log = tmp_path / "orion_stdout.log"
    log.write_text("masih kecil", encoding="utf-8")
    assert rotate_now(log) is False
    assert log.read_text(encoding="utf-8") == "masih kecil"
    assert not (tmp_path / "orion_stdout.log.1").exists()


def test_rotate_now_memindahkan_generasi(tmp_path, monkeypatch):
    _shrink(monkeypatch, max_bytes=10)
    log = tmp_path / "orion_stdout.log"
    log.write_text("0123456789ABCDEF", encoding="utf-8")  # > 10 bytes
    (tmp_path / "orion_stdout.log.1").write_text("gen-1-lama", encoding="utf-8")
    (tmp_path / "orion_stdout.log.2").write_text("gen-2-lama", encoding="utf-8")

    assert rotate_now(log) is True
    assert not log.exists()
    assert (tmp_path / "orion_stdout.log.1").read_text(encoding="utf-8") == "0123456789ABCDEF"
    assert (tmp_path / "orion_stdout.log.2").read_text(encoding="utf-8") == "gen-1-lama"
    # generasi tertua (.2 lama) terhapus — rantai tidak tumbuh tanpa batas


def test_rotate_now_force_walau_kecil(tmp_path, monkeypatch):
    _shrink(monkeypatch)
    log = tmp_path / "orion_stdout.log"
    log.write_text("x", encoding="utf-8")
    assert rotate_now(log, force=True) is True
    assert (tmp_path / "orion_stdout.log.1").read_text(encoding="utf-8") == "x"


def test_writer_merotasi_saat_menulis(tmp_path, monkeypatch):
    _shrink(monkeypatch, max_bytes=100)
    log = tmp_path / "orion_stdout.log"
    w = RotatingFileWriter(log)
    try:
        for i in range(20):
            w.write(f"baris nomor {i:02d} yang cukup panjang untuk cepat penuh\n")
        w.flush()
    finally:
        w.close()

    current = log.read_text(encoding="utf-8")
    assert "baris nomor 19" in current, "baris terakhir harus ada di file aktif"
    assert (tmp_path / "orion_stdout.log.1").exists(), "minimal satu generasi backup dibuat"
    assert len(current.encode("utf-8")) <= 160, "file aktif tetap di sekitar cap"
    assert not (tmp_path / "orion_stdout.log.3").exists(), (
        "rantai backup tidak boleh tumbuh melebihi BACKUP_COUNT"
    )


def test_writer_mereklamasi_log_besar_saat_dibuka(tmp_path, monkeypatch):
    """Log warisan pra-013 yang sudah kebesaran dirotasi pada open pertama."""
    _shrink(monkeypatch, max_bytes=50)
    log = tmp_path / "orion_stdout.log"
    log.write_text("X" * 500, encoding="utf-8")
    w = RotatingFileWriter(log)
    try:
        w.write("baru\n")
    finally:
        w.close()
    assert (tmp_path / "orion_stdout.log.1").read_text(encoding="utf-8") == "X" * 500
    assert log.read_text(encoding="utf-8") == "baru\n"


def test_writer_mewakili_attribut_file(tmp_path, monkeypatch):
    _shrink(monkeypatch)
    log = tmp_path / "orion_stdout.log"
    w = RotatingFileWriter(log)
    try:
        assert w.encoding.lower().replace("-", "") == "utf8"
        assert w.isatty() is False
        assert isinstance(w.fileno(), int)
        w.write("hello\n")
        w.flush()
    finally:
        w.close()
    assert log.read_text(encoding="utf-8") == "hello\n"
