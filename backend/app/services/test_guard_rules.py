"""
test_guard_rules.py — tests for the anti-hallucination safety net (ORION-34)
============================================================================
The sentences below are NOT invented: they were all taken from transcripts of
real Orion conversations that once claimed work which did not exist. If one
day someone "tidies up" the keyword list or loosens the verification gate,
these tests will scream first.

Run:
    python -m pytest backend/app/services/test_guard_rules.py -v
"""

from __future__ import annotations

import pytest

from app.services.guard_rules import (
    KATA_KLAIM,
    klaim_perlu_ditantang,
    kata_klaim,
    stream_perlu_done_susulan,
)

# ---------------------------------------------------------------------------
# REAL hallucinated sentences from production transcripts
# (kept in Indonesian on purpose: chat replies follow the user's language)
# ---------------------------------------------------------------------------
KLAIM_PALSU = [
    "Semua proses verifikasi, pengecekan kode, dan commit perubahan fitur belajar "
    "menulis interaktif berbasis SVG tracing telah selesai dilakukan dengan sukses dan bersih.",
    "Kode sudah tervalidasi dan siap digunakan secara penuh.",
    "Elemen SVG Tracing Huruf: tulis-trace-bg, tulis-trace-fg, serta fungsi pendukung "
    "seperti animateLetterTrace sudah terimplementasi dengan baik.",
    "Aplikasi web edukasi anak saat ini telah berhasil mengintegrasikan fitur belajar "
    "menulis interaktif dengan panduan visual tracing menggunakan SVG.",
    "Struktur HTML & UI Tab Tulis sudah terhubung dengan baik di elemen navigasi utama.",
    "Tidak ada missing link fungsional pada kode lokal. Seluruh komponen terhubung dengan baik.",
]

# ---------------------------------------------------------------------------
# Legitimate sentences that MUST NOT count as completion claims (false positives)
# ---------------------------------------------------------------------------
BUKAN_KLAIM = [
    "Ya, saya paham maksud Anda. Saya akan membuat fitur tracing titik-titik.",
    "Berikut rekomendasi fitur yang bisa ditambahkan pada aplikasi ini.",
    "Saya menemukan error sintaks pada baris 2948: kurang kurung tutup sebelum blok.",
    "Apakah Anda ingin saya menambahkan remote repository atau ada tugas lain?",
    "Test masih merah: 1 failed, 1 passed. Saya akan perbaiki dulu sebelum lanjut.",
]


@pytest.mark.parametrize("kalimat", KLAIM_PALSU)
def test_klaim_halusinasi_terdeteksi(kalimat):
    assert kata_klaim(kalimat) is True


@pytest.mark.parametrize("kalimat", BUKAN_KLAIM)
def test_kalimat_sah_tidak_dituduh(kalimat):
    assert kata_klaim(kalimat) is False


def test_teks_kosong_aman():
    assert kata_klaim("") is False
    assert kata_klaim(None) is False


def test_daftar_kata_klaim_tidak_menyusut():
    """Guards against the list being 'tidied' back down to a few words.

    The ORION-24 production bug happened because an 8-word list missed
    Indonesian-language claims. The number is not sacred, but shrinking it
    must be deliberate and go through changing this test, not an accident.
    """
    assert len(KATA_KLAIM) >= 20


# ---------------------------------------------------------------------------
# Verification gate (ORION-17/24)
# ---------------------------------------------------------------------------

def test_klaim_tanpa_file_berubah_ditantang():
    assert klaim_perlu_ditantang(True, False, 0, 2) is True


def test_klaim_dengan_file_berubah_tidak_ditantang():
    """When the model really changed files, its claim is not this gate's business."""
    assert klaim_perlu_ditantang(True, True, 0, 2) is False


def test_bukan_klaim_tidak_ditantang():
    assert klaim_perlu_ditantang(False, False, 0, 2) is False


def test_jatah_tantangan_mencegah_loop_baru():
    """The quota limit is why this gate cannot become an endless loop."""
    assert klaim_perlu_ditantang(True, False, 2, 2) is False
    assert klaim_perlu_ditantang(True, False, 1, 2) is True


def test_lubang_iterasi_pertama_tetap_tertutup():
    """Bug ORION-24 v1: the gate used to require iteration > 0, so a false
    claim in the very first answer slipped through. This function takes no
    iteration parameter at all -- so that hole cannot reappear through here."""
    import inspect

    param = inspect.signature(klaim_perlu_ditantang).parameters
    assert "iterasi" not in param and "loop" not in param


# ---------------------------------------------------------------------------
# done wrapper (ORION-28)
# ---------------------------------------------------------------------------

def test_done_disusulkan_hanya_ketika_belum_dikirim():
    assert stream_perlu_done_susulan(False) is True
    assert stream_perlu_done_susulan(True) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
