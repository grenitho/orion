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
guarantees: inside `aliran_agent` (agent_loop.py since Phase C step 4; it was
`_aliran_inti` in main.py), the name `_` MUST NOT be assigned except as the
target of the counter for-loop itself.

Run:
    python -m pytest backend/app/services/test_loop_counter_guard.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_LOOP = Path(__file__).resolve().parent / "agent_loop.py"


def _fungsi_aliran() -> ast.AsyncFunctionDef:
    pohon = ast.parse(AGENT_LOOP.read_text(encoding="utf-8"))
    for simpul in ast.walk(pohon):
        if isinstance(simpul, ast.AsyncFunctionDef) and simpul.name == "aliran_agent":
            return simpul
    raise AssertionError("aliran_agent not found in agent_loop.py")


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
        f"The name `_` is assigned as a throwaway variable at lines {pelanggaran} inside "
        "aliran_agent. `_` is the agent LOOP COUNTER; overwriting it makes "
        "`_ > 0` compare the wrong types (production bug ORION-29 v1). "
        "Use another name, e.g. `_tunggu_pending`."
    )




def test_fungsi_aliran_masih_ada():
    """Simple sentinel: if the function is ever renamed, the test above must fail
    loudly instead of passing silently."""
    assert _fungsi_aliran() is not None
