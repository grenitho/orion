"""
test_sse.py — penjaga bentuk frame SSE (Fase C langkah 1)
==========================================================
sse.py menggantikan 47 titik perakitan frame inline di main.py. Tiga hal
dijaga di sini:

  1. ev() menghasilkan byte yang IDENTIK dengan bentuk inline lama
     (f"data: {json.dumps({'type': ...})}\\n\\n") untuk ketujuh event;
  2. ev('done') tetap kompatibel dengan stream_utils: jaminan "tepat satu
     done" dideteksi lewat substring PENANDA_DONE, jadi keduanya tidak
     boleh menyimpang;
  3. guard AST atas main.py: tidak boleh ada frame SSE rakitan manual yang
     kembali muncul, dan setiap panggilan sse.ev(...) hanya boleh memakai
     tujuh nama event beku (FASE_C_PLAN.md bagian 1).

Gaya mengikuti test_loop_counter_guard.py: baca sumber dengan ast, tanpa
meng-import main.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.services import sse
from app.services.stream_utils import PENANDA_DONE, buat_event_done

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _bentuk_lama(event_type: str, **payload) -> str:
    """Bentuk inline persis seperti sebelum Fase C langkah 1."""
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
    """Urutan kunci memengaruhi byte; extension parse JSON jadi urutan tidak
    fungsional, tetapi kesetaraan byte dengan transkrip baseline mensyaratkan
    'type' tetap pertama."""
    frame = sse.ev("todo_update", markdown="m", todos=[])
    payload = json.loads(frame[len("data: "): -2])
    assert next(iter(payload)) == "type"
    assert frame.startswith("data: ") and frame.endswith("\n\n")


def test_done_cocok_dengan_stream_utils():
    """jamin_done mendeteksi done lewat substring PENANDA_DONE; kalau bentuk
    ev('done') menyimpang, wrapper bisa mengirim done kedua (atau tidak sama
    sekali) — pelanggaran jaminan 'tepat satu done per run'."""
    assert sse.ev("done") == buat_event_done()
    assert PENANDA_DONE in sse.ev("done")


def test_nama_event_beku():
    assert sse.FROZEN_EVENTS == (
        "status", "token", "ask_user", "confirm_terminal",
        "todo_update", "error", "done",
    )


def _frame_manual_di_main() -> list[int]:
    """Baris-baris di main.py yang masih merakit frame SSE via f-string
    json.dumps (pola yang digantikan sse.ev pada Fase C langkah 1)."""
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
