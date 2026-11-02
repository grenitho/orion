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
import re
from typing import AsyncIterator

from app.services import guard_rules

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


# >>> UPDATE-025 commit 25+26: planning-prose stripper (InvoiceFlow
# transcripts 2026-11-01: "Let's check git_diff … Wait, what was the user's
# prompt…" AND, on the 0.10.29 re-exam, "Ah, read_file … was truncated.
# Let's use search_code…"). The <thinking> stripper only removes PAIRED
# tags; this catches untagged monologue by SHAPE. Commit 26 INVERTS the
# default for tool-call message content: the user frame gets the work,
# not the mumbling — content is suppressed unless it clearly addresses
# the user (question, second-person, summary markers, user-facing
# markup). Deterministic on purpose — a vocabulary wish in the prompt is
# not a tooth. <<<
_OPENER_PERENCANAAN = (
    "let's", "let me", "i'll", "i will", "i ", "i'", "we ", "we'", "first,",
    "first ", "wait,", "wait ", "wait —", "hmm,", "hmm ", "okay,", "okay ",
    "ok,", "ok ", "now,", "now ", "next,", "next ", "ah,", "ah ", "aha",
    "yes,", "yes ", "so,", "so ", "right,", "right ", "good,", "good ",
    "nice,", "nice ", "here ", "there ", "since", "because", "since ",
    "saya akan", "saya ", "kami ", "mari kita", "coba ", "oke,", "oke ",
    "pertama,", "pertama ", "tunggu,", "tunggu ", "hmm,", "baik,", "baik ",
)
_VERB_RENCANA = (
    "check", "read", "run", "verify", "look", "search", "grep", "open",
    "inspect", "compare", "call", "navigate", "snapshot", "close",
    "cek", "baca", "jalankan", "periksa", "cari", "lihat", "bandingkan",
    "panggil", "tutup", "review", "format", "present", "update",
    "summarize", "ensure", "mark", "complete", "susun", "rapikan",
    "pastikan", "tandai", "ingat", "make", "use", "try", "write",
    "rewrite", "start", "serve", "delete", "remove", "buat", "tulis",
    "hapus", "mulai", "coba",
)
_TOKEN_TOOL = (
    "git_diff", "git_status", "git_commit", "read_file", "edit_file",
    "write_file", "search", "grep", "terminal", "bash", "playwright",
    "mcp", "todo", "app.jsx", "uvicorn", "npm", "pytest", "browser_",
)
# Markers that mean the text IS user-facing: keep it in the chat frame.
# >>> Commit 27: boundary-aware — the bare substrings "here are"/"berikut"
# false-positived inside "there are any pending todos" and "langkah
# berikutnya", letting monologue escape as user-addressed. <<<
# >>> UPDATE-025 commit 29: the presentational markers (" here is",
# " here are", "ini adalah", "ringkasan", "summary") were substring-
# matched and false-positived mid-sentence — the 0.10.32 exam's planning
# paragraph said "live total summary row" (an InvoiceFlow UI element
# name, not a user address) and escaped the stripper. They still escape
# at TEXT START via _PENANDA_AWAL; only genuinely user-addressed markers
# remain in the substring scan. <<<
_PENANDA_UNTUK_USER = (
    "anda", "kamu", " you", " your", "jawab:", "answer:",
)
_PENANDA_AWAL = (
    "here is", "here are", "berikut", "ini adalah", "ringkasan", "summary",
)


def prose_perencanaan(teks: str) -> bool:
    """True when a tool-call message's content is planning monologue that
    must stay OUT of the user frame (the context keeps the raw text, so
    the model still remembers its own plan). Commit 26: shape-based with
    an escape hatch for genuinely user-addressed notes; a single planning
    sentence anywhere in a short, markup-free, non-addressed text marks
    the whole blob (the leak shape: observation sentence followed by
    "Let's …")."""
    t = (teks or "").strip()
    if not t or len(t) > 600:
        return False
    low = t.lower()
    # >>> The honest OR branch outranks the stripper: a plain decline must
    # ALWAYS reach the user frame, never be rerouted as mumbling. <<<
    if guard_rules.ada_penolakan_jujur(t):
        return False
    if any(m in low for m in _PENANDA_UNTUK_USER):
        return False
    if low.startswith(_PENANDA_AWAL):
        return False
    # >>> UPDATE-025 commit 27: the markup/numbered-list escape was a
    # hole — the 0.10.30 exam leaked a markdown-structured planning blob
    # ("Let's check code implementation … 1. **Revisi 1a** …") straight
    # through it. User-addressed markers and the honest-decline escape
    # remain the ONLY escapes. <<<
    if not (any(v in low for v in _VERB_RENCANA)
            or any(v in low for v in _TOKEN_TOOL)):
        return False
    kalimat = [
        s.strip().lower()
        for s in re.split(r"[\n;]+|\.\s", t)
        if s.strip()
    ]
    return any(k.startswith(_OPENER_PERENCANAAN) for k in kalimat)


def catatan_untuk_user(teks: str) -> bool:
    """UPDATE-025 commit 28: the ONLY gate for tool-call message content
    reaching the user frame. Three exams proved vocabulary classifiers
    are whack-a-mole ("Ah, …", markdown blobs, "let's make sure …"), so
    the default is now FULLY inverted: suppress everything except the
    three escape shapes — an honest decline (the OR branch must always
    reach the user), a user-addressed note (second person / summary
    markers), or a direct question to the user. The model's own context
    keeps the raw text either way."""
    t = (teks or "").strip()
    if not t:
        return False
    if guard_rules.ada_penolakan_jujur(t):
        return True
    low = t.lower()
    if low.startswith(_PENANDA_AWAL):
        return True
    if any(m in low for m in _PENANDA_UNTUK_USER):
        return True
    return t.rstrip().endswith("?")


def buang_paragraf_perencanaan_awal(teks: str) -> str:
    """UPDATE-025 commit 29: strip LEADING planning paragraphs from a
    closing text. The 0.10.32 exam's final answer opened with four
    "Let's …" monologue paragraphs before the real report; the tool-call
    gate does not apply to closings and the reroute predicate caps at 600
    chars per blob. Paragraph-wise stripping keeps every existing escape
    (decline / user-addressed / question) intact; when EVERY paragraph
    would be stripped the original is returned untouched — an empty
    answer is never emitted."""
    if not teks:
        return teks
    par = teks.split("\n\n")
    i = 0
    while i < len(par) and prose_perencanaan(par[i]):
        i += 1
    if i == 0 or i == len(par):
        return teks
    return "\n\n".join(par[i:]).lstrip()


def tambah_ledger_gaya(teks: str, bukti) -> str:
    """UPDATE-025 commit 30: append the code-captured read values to a
    closing text. The 0.10.33 exam proved the model's closing can lose
    the values (five 'Memory full, compressing context' events between
    evaluate and closing); state survives compression, so the ledger
    section is appended deterministically by CODE. Snippets already
    present are not duplicated; max six entries; empty ledger is a
    no-op."""
    sisa = [b for b in (bukti or []) if b and b not in (teks or "")]
    if not sisa:
        return teks or ""
    blok = (
        "\n\n📐 NILAI TERBACA dari browser turn ini (ledger gaya, "
        "ditangkap kode — bukan klaim model):\n"
        + "\n".join(f"- {b}" for b in sisa[:6])
    )
    return (teks or "") + blok
