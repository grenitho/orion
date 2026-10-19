"""
test_tool_dispatch.py — penjaga tabel dispatch (Fase B, ORION-38)
=================================================================
Memastikan pemecahan main.py tidak menggeser tanggung jawab secara diam-diam:

  * setiap tool di tabel benar-benar terdeklarasi di skema TOOLS
  * tool yang punya guard/interaksi TIDAK bocor ke tabel sederhana
  * setiap entri tabel menunjuk method yang benar-benar ada di ToolExecutor
  * cabang individual tool sederhana sudah tidak tersisa di main.py
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.services import tool_dispatch
from app.services.tool_executor import ToolExecutor
from app.services.tools_schema import TOOLS

MAIN = Path(__file__).resolve().parents[1] / "main.py"
NAMA_SKEMA = {t["function"]["name"] for t in TOOLS}


def test_semua_entri_tabel_terdeklarasi_di_skema():
    hilang = set(tool_dispatch.TABLE) - NAMA_SKEMA
    assert not hilang, f"tool di tabel tapi tidak ada di skema TOOLS: {hilang}"


def test_tool_berguard_tidak_masuk_tabel():
    bocor = set(tool_dispatch.TABLE) & tool_dispatch.WAJIB_DI_MAIN
    assert not bocor, (
        f"tool ini punya guard/interaksi dan HARUS ditangani main.py: {bocor}. "
        "Memindahkannya ke tabel sederhana akan membuang pengamannya."
    )


def test_setiap_entri_tabel_punya_method_di_executor():
    for nama, method in tool_dispatch.METHOD.items():
        assert hasattr(ToolExecutor, method), f"ToolExecutor tidak punya method '{method}' untuk tool '{nama}'"
        assert callable(getattr(ToolExecutor, method))


def test_cabang_individual_sudah_diangkat_dari_main():
    src = MAIN.read_text(encoding="utf-8")
    for nama in tool_dispatch.TABLE:
        assert f'elif func_name == "{nama}":' not in src, (
            f"cabang '{nama}' masih ada di main.py padahal sudah masuk tabel dispatch"
        )
    assert "tool_dispatch.tool_sederhana(func_name)" in src


def test_tool_rumit_masih_ditangani_main():
    src = MAIN.read_text(encoding="utf-8")
    for nama in sorted(tool_dispatch.WAJIB_DI_MAIN):
        assert f'func_name == "{nama}"' in src, (
            f"'{nama}' seharusnya tetap ditangani main.py tapi cabangnya tidak ditemukan"
        )


def test_label_tidak_kosong_dan_tahan_args_kosong():
    for nama in tool_dispatch.TABLE:
        mulai, selesai = tool_dispatch.label(nama, {})
        assert isinstance(mulai, str) and mulai.strip()
        assert selesai is None or (isinstance(selesai, str) and selesai.strip())


def test_jalankan_memanggil_method_yang_benar():
    """Panggil satu tool read-only sungguhan lewat tabel, tanpa efek samping."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        ex = ToolExecutor(d)
        hasil = tool_dispatch.jalankan("list_files", ex, {"path": "."})
        assert isinstance(hasil, str) and hasil != ""
        assert inspect.isfunction(tool_dispatch.jalankan)
