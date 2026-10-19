"""
test_attachments.py — 8 tests for attachments.py
=================================================
These were also the FIRST tests in the ORION project (your test ratio was 0%).

How to run:
    cd "<your-project-folder>"
    pip install pytest
    python -m pytest backend/app/services/test_attachments.py -v

No API key needed, no internet needed, does not touch project files.
Finishes in < 1 second.
"""

from __future__ import annotations

import base64

import pytest

from app.services.attachments import (
    MAX_GAMBAR,
    build_user_content,
    describe_attachments,
    proses,
)

PNG_PALSU = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 5000).decode()


def _gambar(nama="a.png", mime="image/png", data=None):
    return {"name": nama, "mime": mime, "data": data if data is not None else PNG_PALSU}


# ---------------------------------------------------------------- basics


def test_tanpa_lampiran_mengembalikan_string_bukan_list():
    """The old behaviour MUST NOT change: no attachments -> content is a str."""
    content, ringkasan = build_user_content("halo", None)
    assert isinstance(content, str)
    assert content == "halo"
    assert ringkasan == "halo"


def test_gambar_menghasilkan_dua_part_dengan_text_di_depan():
    """Gemini needs the text part first, then the image."""
    hasil = proses("perbaiki error ini", [_gambar()])
    assert len(hasil.parts) == 2
    assert hasil.parts[0]["type"] == "text"
    assert hasil.parts[0]["text"] == "perbaiki error ini"
    assert hasil.parts[1]["type"] == "image_url"
    assert hasil.parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------- history


def test_base64_tidak_bocor_ke_riwayat():
    """Most important: the history must stay small.

    If base64 entered .orion/session_history.json, every agent iteration
    would resend that image and the free-tier quota would be gone within a
    few messages.
    """
    hasil = proses("cek", [_gambar()])
    assert "iVBOR" not in hasil.ringkasan          # not a PNG base64 fragment
    assert PNG_PALSU not in hasil.ringkasan
    assert len(hasil.ringkasan) < 500              # placeholder pendek
    assert "IMAGE ATTACHMENT" in hasil.ringkasan


def test_file_teks_ditempel_sebagai_teks_bukan_part_gambar():
    """Code files must become plain text so they stay useful in the next iterations."""
    kode = base64.b64encode(b"def hitung(a, b):\n    return a + b\n").decode()
    hasil = proses("review fungsi ini", [{"name": "util.py", "mime": "text/x-python", "data": kode}])
    assert hasil.parts == []                        # no image parts
    assert "def hitung(a, b):" in hasil.ringkasan   # isinya ikut


# ---------------------------------------------------------------- format input


def test_data_uri_penuh_diterima_dan_mime_dibaca_dari_kepala():
    """The extension can send a full data URI; mime need not be provided separately."""
    hasil = proses("cek", [{"name": "foto", "data": f"data:image/jpeg;base64,{PNG_PALSU}"}])
    assert len(hasil.parts) == 2
    assert "data:image/jpeg;base64," in hasil.parts[1]["image_url"]["url"]
    assert hasil.catatan_gagal == []


def test_mime_ditebak_dari_ekstensi_nama_file():
    hasil = proses("cek", [{"name": "tangkapan.webp", "data": PNG_PALSU}])
    assert len(hasil.parts) == 2
    assert "image/webp" in hasil.parts[1]["image_url"]["url"]


# ---------------------------------------------------------------- batas & kegagalan


def test_jumlah_gambar_dibatasi():
    hasil = proses("x", [_gambar(nama=f"g{i}.png") for i in range(MAX_GAMBAR + 3)])
    jumlah_gambar = sum(1 for p in hasil.parts if p.get("type") == "image_url")
    assert jumlah_gambar == MAX_GAMBAR
    assert len(hasil.catatan_gagal) == 3


def test_gambar_lebih_dari_7mb_ditolak():
    besar = base64.b64encode(b"x" * (8 * 1024 * 1024)).decode()
    hasil = proses("x", [_gambar(nama="besar.png", data=besar)])
    assert hasil.parts == []
    assert any("7 MB" in c for c in hasil.catatan_gagal)


def test_base64_rusak_dilaporkan_bukan_membuat_crash():
    hasil = proses("x", [_gambar(nama="rusak.png", data="!!!bukan-base64!!!")])
    assert hasil.parts == []
    assert any("invalid" in c for c in hasil.catatan_gagal)


def test_tipe_tidak_didukung_ditolak_dengan_pesan_jelas():
    hasil = proses("x", [{"name": "arsip.zip", "mime": "application/zip", "data": PNG_PALSU}])
    assert hasil.parts == []
    assert any("not supported" in c for c in hasil.catatan_gagal)


def test_model_diberitahu_kalau_lampiran_gagal():
    """IMPORTANT: when an attachment fails, the model must know it CANNOT see it.

    Without this, the model would invent the contents of an image it never
    received.
    """
    hasil = proses("x", [_gambar(nama="rusak.png", data="!!!")])
    assert "FAILED to process" in hasil.ringkasan
    assert "Do not assume" in hasil.ringkasan


def test_teks_panjang_dipotong_dengan_penanda():
    panjang = base64.b64encode(("x" * 200_000).encode()).decode()
    hasil = proses("cek", [{"name": "besar.log", "mime": "text/plain", "data": panjang}])
    assert "TRUNCATED" in hasil.ringkasan or "truncated" in hasil.ringkasan


# ---------------------------------------------------------------- util


def test_describe_attachments_untuk_log():
    assert describe_attachments(None) == ""
    assert describe_attachments([]) == ""
    teks = describe_attachments([{"name": "a.png"}, {"name": "b.py"}])
    assert "2 attachments" in teks
    assert "a.png" in teks


def test_payload_kosong_tidak_membuat_crash():
    """The extension can send broken objects; the backend must not die."""
    for buruk in [None, {}, [], {"name": None}, "bukan-objek", 42]:
        hasil = proses("pesan", [buruk] if not isinstance(buruk, list) else buruk)
        assert isinstance(hasil.ringkasan, str)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
