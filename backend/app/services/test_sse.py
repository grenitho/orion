"""
test_sse.py — guardian of the SSE frame shape (Phase C step 1)
===============================================================
sse.py replaced 47 inline frame-assembly sites in main.py. Three things are
guarded here:

  1. ev() produces bytes IDENTICAL to the old inline shape
     (f"data: {json.dumps({'type': ...})}\\n\\n") for all seven events;
  2. ev('done') stays compatible with stream_utils: the "exactly one done"
     guarantee is detected via the PENANDA_DONE substring, so the two must
     never diverge;
  3. an AST guard over main.py: manually assembled SSE frames must never
     reappear, and every sse.ev(...) call may only use the seven frozen
     event names (FASE_C_PLAN.md section 1).

Style follows test_loop_counter_guard.py: read the source with ast, without
importing main.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.services import sse
from app.services.stream_utils import PENANDA_DONE, buat_event_done

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _bentuk_lama(event_type: str, **payload) -> str:
    """The exact inline shape from before Phase C step 1."""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def test_ev_identik_byte_dengan_bentuk_inline_lama():
    contoh = [
        ("status", {"text": "⏳ Sedang berpikir..."}),
        ("token", {"text": "halo\n\n"}),
        ("ask_user", {"question": "Lanjut?"}),
        ("confirm_terminal", {"command": "npm run build"}),
        ("todo_update", {"markdown": "✅ `[1]` x", "todos": [{"id": "1", "text": "x", "status": "pending"}]}),
        ("error", {"text": "boom 💥"}),
        ("done", {}),
    ]
    for nama, payload in contoh:
        assert sse.ev(nama, **payload) == _bentuk_lama(nama, **payload)


def test_type_selalu_kunci_pertama():
    """Key order affects the bytes; the extension parses JSON so the order is
    not functional, but byte-equality with the baseline transcripts requires
    'type' to stay first."""
    frame = sse.ev("todo_update", markdown="m", todos=[])
    payload = json.loads(frame[len("data: "): -2])
    assert next(iter(payload)) == "type"
    assert frame.startswith("data: ") and frame.endswith("\n\n")


def test_done_cocok_dengan_stream_utils():
    """jamin_done detects done via the PENANDA_DONE substring; if the shape of
    ev('done') diverges, the wrapper could send a second done (or none at
    all) — violating the 'exactly one done per run' guarantee."""
    assert sse.ev("done") == buat_event_done()
    assert PENANDA_DONE in sse.ev("done")


def test_nama_event_beku():
    assert sse.FROZEN_EVENTS == (
        "status", "token", "ask_user", "confirm_terminal",
        "todo_update", "error", "done",
    )


def _frame_manual_di_main() -> list[int]:
    """Lines in main.py that still assemble SSE frames via f-string
    json.dumps (the pattern replaced by sse.ev in Phase C step 1)."""
    pohon = ast.parse(MAIN.read_text(encoding="utf-8"))
    pelanggaran = []
    for node in ast.walk(pohon):
        if not isinstance(node, ast.JoinedStr):
            continue
        for bagian in node.values:
            if isinstance(bagian, ast.FormattedValue) and isinstance(bagian.value, ast.Call):
                fn = bagian.value.func
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == "dumps"
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "json"
                ):
                    pelanggaran.append(node.lineno)
    return pelanggaran


def test_main_py_hanya_pakai_sse_ev_dengan_nama_beku():
    pohon = ast.parse(MAIN.read_text(encoding="utf-8"))
    nama_dipakai = set()
    for node in ast.walk(pohon):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "ev"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sse"
        ):
            assert node.args and isinstance(node.args[0], ast.Constant), (
                f"sse.ev di baris {node.lineno} harus menerima nama event literal"
            )
            nama_dipakai.add(node.args[0].value)

    manual = _frame_manual_di_main()
    assert not manual, (
        f"main.py merakit frame SSE manual lagi di baris {manual}; "
        "gunakan sse.ev(...) supaya kontrak frame terpusat dan ter-test."
    )
    assert nama_dipakai, "tidak ada panggilan sse.ev di main.py — transformasi hilang?"
    assert nama_dipakai <= set(sse.FROZEN_EVENTS), (
        f"nama event tak dikenal: {sorted(nama_dipakai - set(sse.FROZEN_EVENTS))}. "
        "Tujuh nama SSE beku (FASE_C_PLAN.md bagian 1)."
    )
