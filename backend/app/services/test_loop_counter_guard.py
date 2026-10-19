"""
test_loop_counter_guard.py — guardian of the `_` convention in the agent loop (ORION-33)
========================================================================================
REAL bug background (ORION-29 v1): the agent loop uses `_` as its counter
(`for _ in range(max_iter)`). A patch added
`_selesai, _ = await asyncio.wait(...)` -- using `_` as a throwaway variable.
`asyncio.wait` returns two SETs, so `_` became a set and the line
`if is_task_complete and _ > 0` raised
`TypeError: '>' not supported between instances of 'set' and 'int'`
in production, during a real conversation.

This test is cheap (it only reads the source with ast, no main import) and
guarantees: inside `_aliran_inti`, the name `_` MUST NOT be assigned except
as the target of the counter for-loop itself.

Run:
    python -m pytest backend/app/services/test_loop_counter_guard.py -v
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

    # all Name nodes that are for-loop targets
    target_for = set()
    for simpul in ast.walk(fungsi):
        if isinstance(simpul, (ast.For, ast.AsyncFor)):
            for n in ast.walk(simpul.target):
                if isinstance(n, ast.Name):
                    target_for.add(id(n))

    # all assignments to the name `_` outside for-loop targets
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
    """Simple sentinel: if the function is ever renamed, the test above must fail
    loudly instead of passing silently."""
    assert _fungsi_aliran() is not None
