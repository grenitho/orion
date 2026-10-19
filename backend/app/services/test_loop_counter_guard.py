"""
test_loop_counter_guard.py — penjaga konvensi `_` di loop agent  (ORION-33)
===========================================================================
Latar belakang bug NYATA (ORION-29 v1): loop agent memakai `_` sebagai
penghitung (`for _ in range(max_iter)`). Sebuah patch menambahkan
`_selesai, _ = await asyncio.wait(...)` -- memakai `_` sebagai variabel buang.
`asyncio.wait` mengembalikan dua SET, sehingga `_` berubah menjadi set dan
baris `if is_task_complete and _ > 0` melempar
`TypeError: '>' not supported between instances of 'set' and 'int'`
di produksi, pada percakapan sungguhan.

Test ini murah (hanya membaca sumber dengan ast, tanpa import main) dan
menjamin: di dalam `_aliran_inti`, nama `_` TIDAK boleh di-assign kecuali
sebagai target for-loop penghitung itu sendiri.

Jalankan:
    python -m pytest backend\\app\\services\\test_loop_counter_guard.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _fungsi_aliran() -> ast.AsyncFunctionDef:
    pohon = ast.parse(MAIN.read_text(encoding="utf-8"))
    for simpul in ast.walk(pohon):
        if isinstance(simpul, ast.AsyncFunctionDef) and simpul.name == "_aliran_inti":
            return simpul
    raise AssertionError("_aliran_inti tidak ditemukan di main.py")


def test_penghitung_loop_tidak_ditimpa():
    fungsi = _fungsi_aliran()

    # semua node Name yang menjadi target for-loop
    target_for = set()
    for simpul in ast.walk(fungsi):
        if isinstance(simpul, (ast.For, ast.AsyncFor)):
            for n in ast.walk(simpul.target):
                if isinstance(n, ast.Name):
                    target_for.add(id(n))

    # semua assign ke nama `_` di luar target for-loop
    pelanggaran = []
    for simpul in ast.walk(fungsi):
        if isinstance(simpul, ast.Name) and simpul.id == "_" and isinstance(simpul.ctx, ast.Store):
            if id(simpul) not in target_for:
                pelanggaran.append(simpul.lineno)

    assert not pelanggaran, (
        f"Nama `_` di-assign sebagai variabel buang di baris {pelanggaran} dalam "
        "_aliran_inti. `_` adalah PENGHITUNG LOOP agent; menimpanya membuat "
        "`_ > 0` membandingkan tipe salah (bug produksi ORION-29 v1). "
        "Pakai nama lain, mis. `_tunggu_pending`."
    )




def test_fungsi_aliran_masih_ada():
    """Penjaga sederhana: kalau nama fungsinya diganti, test di atas harus gagal keras,
    bukan lolos diam-diam."""
    assert _fungsi_aliran() is not None
