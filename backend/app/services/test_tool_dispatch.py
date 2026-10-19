"""
test_tool_dispatch.py — guardian of the dispatch table (Phase B, ORION-38)
==========================================================================
Ensures the main.py split did not silently shift responsibilities:

  * every tool in the table is really declared in the TOOLS schema
  * tools with guards/interaction do NOT leak into the simple table
  * every table entry points at a method that really exists on ToolExecutor
  * no individual branches for simple tools remain in the agent loop
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.services import tool_dispatch
from app.services.tool_executor import ToolExecutor
from app.services.tools_schema import TOOLS

AGENT_LOOP = Path(__file__).resolve().parent / "agent_loop.py"
NAMA_SKEMA = {t["function"]["name"] for t in TOOLS}


def test_semua_entri_tabel_terdeklarasi_di_skema():
    hilang = set(tool_dispatch.TABLE) - NAMA_SKEMA
    assert not hilang, f"tool in the table but missing from the TOOLS schema: {hilang}"


def test_tool_berguard_tidak_masuk_tabel():
    bocor = set(tool_dispatch.TABLE) & tool_dispatch.WAJIB_DI_MAIN
    assert not bocor, (
        f"these tools have guards/interaction and MUST be handled by main.py: {bocor}. "
        "Moving them into the simple table would discard their safeguards."
    )


def test_setiap_entri_tabel_punya_method_di_executor():
    for nama, method in tool_dispatch.METHOD.items():
        assert hasattr(ToolExecutor, method), f"ToolExecutor has no method '{method}' for tool '{nama}'"
        assert callable(getattr(ToolExecutor, method))


def test_cabang_individual_sudah_diangkat_dari_main():
    src = AGENT_LOOP.read_text(encoding="utf-8")
    for nama in tool_dispatch.TABLE:
        assert f'elif func_name == "{nama}":' not in src, (
            f"branch '{nama}' still exists in agent_loop.py even though it is in the dispatch table"
        )
    assert "tool_dispatch.tool_sederhana(func_name)" in src


def test_tool_rumit_masih_ditangani_main():
    src = AGENT_LOOP.read_text(encoding="utf-8")
    for nama in sorted(tool_dispatch.WAJIB_DI_MAIN):
        assert f'func_name == "{nama}"' in src, (
            f"'{nama}' should still be handled by the agent loop but its branch was not found"
        )


def test_label_tidak_kosong_dan_tahan_args_kosong():
    for nama in tool_dispatch.TABLE:
        mulai, selesai = tool_dispatch.label(nama, {})
        assert isinstance(mulai, str) and mulai.strip()
        assert selesai is None or (isinstance(selesai, str) and selesai.strip())


def test_jalankan_memanggil_method_yang_benar():
    """Call one real read-only tool through the table, without side effects."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        ex = ToolExecutor(d)
        hasil = tool_dispatch.jalankan("list_files", ex, {"path": "."})
        assert isinstance(hasil, str) and hasil != ""
        assert inspect.isfunction(tool_dispatch.jalankan)
