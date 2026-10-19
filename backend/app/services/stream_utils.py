"""
stream_utils.py — penjaga akhir aliran SSE (ORION-28, dibuat ter-test di ORION-36)
==================================================================================
Sebelum modul ini ada, wrapper "jamin ada done" tertulis inline di dalam
endpoint /chat, sehingga tidak bisa di-unit-test tanpa menjalankan FastAPI.
Sekarang ia fungsi generator mandiri: diberi aliran potongan SSE, ia
meneruskannya apa adanya dan MENJAMIN tepat satu event 'done' di akhir.

Perilaku yang dijaga:
  - aliran yang sudah mengirim done  -> tidak menambah done kedua
  - aliran yang berakhir tanpa done  -> menambahkan tepat satu done
  - aliran yang mati di tengah (exception) -> TIDAK menambah done,
    supaya extension tetap bisa membedakan "berakhir normal" dari "crash"
"""

from __future__ import annotations

import json
from typing import AsyncIterator

PENANDA_DONE = '{"type": "done"}'


def buat_event_done() -> str:
    """Bentuk persis event done yang dikenali extension."""
    return f"data: {json.dumps({'type': 'done'})}\n\n"


async def jamin_done(aliran: AsyncIterator[str], logger=None) -> AsyncIterator[str]:
    """Teruskan semua potongan; jamin tepat satu 'done' bila aliran berakhir normal."""
    sudah_done = False
    async for potongan in aliran:
        if PENANDA_DONE in potongan:
            sudah_done = True
        yield potongan
    if not sudah_done:
        if logger is not None:
            try:
                logger.info("[STREAM] Aliran berakhir tanpa done; wrapper mengirimkannya.")
            except Exception:  # noqa: BLE001 - logging tidak boleh menggagalkan stream
                pass
        yield buat_event_done()
