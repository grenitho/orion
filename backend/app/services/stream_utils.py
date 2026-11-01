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


# >>> UPDATE-025 commit 25: planning-prose stripper (InvoiceFlow transcript
# 2026-11-01: "Let's check git_diff … Wait, what was the user's prompt…"
# leaked into the user frame as the content of a TOOL-CALL message). The
# <thinking> stripper only removes PAIRED tags; this catches untagged
# monologue by SHAPE: a planning opener + a planning verb or a tool token,
# short, and carrying no user-facing markup. Deterministic on purpose —
# a vocabulary wish in the prompt is not a tooth. <<<
_OPENER_PERENCANAAN = (
    "let's", "let me", "i'll", "i will", "first,", "wait,", "wait —",
    "hmm,", "okay,", "ok,", "now,", "next,",
    "saya akan", "mari kita", "coba ", "oke,", "pertama,",
)
_VERB_RENCANA = (
    "check", "read", "run", "verify", "look", "search", "grep", "open",
    "inspect", "compare", "cek", "baca", "jalankan", "periksa", "cari",
    "lihat", "bandingkan",
)
_TOKEN_TOOL = (
    "git_diff", "git_status", "git_commit", "read_file", "edit_file",
    "write_file", "search", "grep", "terminal", "bash", "playwright",
    "mcp", "todo", "app.jsx",
)


def prose_perencanaan(teks: str) -> bool:
    """True when a tool-call message's content is pure planning monologue
    that must stay OUT of the user frame (the context keeps the raw text,
    so the model still remembers its own plan)."""
    t = (teks or "").strip()
    if not t or len(t) > 600:
        return False
    low = t.lower()
    if not low.startswith(_OPENER_PERENCANAAN):
        return False
    if any(m in t for m in ("```", "\n#", "\n-", "| ", "**")):
        return False
    return any(v in low for v in _VERB_RENCANA) or any(
        v in low for v in _TOKEN_TOOL
    )
