"""
stream_utils.py — the SSE stream end-guard (ORION-28, made testable in ORION-36)
=================================================================================
Before this module existed, the "make sure there is a done" wrapper was
written inline inside the /chat endpoint, so it could not be unit-tested
without running FastAPI. It is now a standalone generator function: given a
stream of SSE chunks, it forwards them as-is and GUARANTEES exactly one
'done' event at the end.

Guarded behaviour:
  - a stream that already sent done  -> no second done is added
  - a stream that ends without done  -> exactly one done is added
  - a stream that dies mid-way (exception) -> NO done is added, so the
    extension can still tell "ended normally" apart from "crashed"
"""

from __future__ import annotations

import json
from typing import AsyncIterator

PENANDA_DONE = '{"type": "done"}'


def buat_event_done() -> str:
    """The exact shape of the done event recognized by the extension."""
    return f"data: {json.dumps({'type': 'done'})}\n\n"


async def jamin_done(aliran: AsyncIterator[str], logger=None) -> AsyncIterator[str]:
    """Forward every chunk; guarantee exactly one 'done' when the stream ends normally."""
    sudah_done = False
    async for potongan in aliran:
        if PENANDA_DONE in potongan:
            sudah_done = True
        yield potongan
    if not sudah_done:
        if logger is not None:
            try:
                logger.info("[STREAM] Stream ended without done; the wrapper is sending it.")
            except Exception:  # noqa: BLE001 - logging must never break the stream
                pass
        yield buat_event_done()
