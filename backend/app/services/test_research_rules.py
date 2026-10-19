"""
test_research_rules.py — tests for the automatic research when stuck (ORION-35)
===============================================================================
Guards two things: the research trigger threshold, and the cleanliness of the
query built from a raw error (tracebacks, file paths and carets must not be
dragged along into the search engine).
"""

from __future__ import annotations

from app.services.research_rules import (
    AMBANG_GAGAL,
    perlu_riset_otomatis,
    rancang_kueri_riset,
)


def test_riset_dipicu_tepat_pada_ambang():
    assert perlu_riset_otomatis(2) is False
    assert perlu_riset_otomatis(3) is True
    assert perlu_riset_otomatis(4) is True


def test_ambang_bisa_diubah():
    assert perlu_riset_otomatis(2, ambang=2) is True
    assert perlu_riset_otomatis(1, ambang=2) is False


def test_ambang_default_tidak_bergeser():
    """Raising/lowering the threshold changes when Orion stops trying on its own.
    The change must be deliberate, via this test."""
    assert AMBANG_GAGAL == 3


def test_kueri_membersihkan_traceback():
    error = (
        "Traceback (most recent call last):\n"
        '  File "D:\\proj\\app.py", line 42, in main\n'
        "    raise ValueError(x)\n"
        "ValueError: konfigurasi tidak valid untuk modul audio\n"
        "^^^^^^^^^\n"
    )
    kueri = rancang_kueri_riset("run_terminal", error)
    assert "Traceback" not in kueri
    assert "D:\\proj" not in kueri
    assert "^^^" not in kueri
    assert "konfigurasi tidak valid untuk modul audio" in kueri


def test_kueri_dibatasi_panjangnya():
    kueri = rancang_kueri_riset("edit_file", "x " * 500)
    assert len(kueri) <= 220 + len("edit_file error: ")


def test_kueri_menyebut_nama_tool():
    assert rancang_kueri_riset("run_terminal", "segfault saat compile").startswith("run_terminal error:")


def test_error_kosong_tetap_menghasilkan_kueri():
    kueri = rancang_kueri_riset("git_commit", "")
    assert kueri == "git_commit common errors and fixes"
    assert rancang_kueri_riset("git_commit", None) == "git_commit common errors and fixes"
