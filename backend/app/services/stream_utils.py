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
    # >>> Commit 33: a tool-call blob quoting the challenge injection is
    # scaffolding, not a user note — the "anda" inside the quote must not
    # buy it a user-addressed escape. The decline escape above still
    # outranks this suppression. <<<
    if any(m.lower() in low for m in MARKER_SUNTIKAN_TANTANGAN):
        return False
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
    while i < len(par) and prose_perencanaan_closing(par[i]):
        i += 1
    # >>> Commit 34 (handoff §6 residual 2): a LEADING quote paragraph
    # (the user's own words quoted back) froze the chain and let a
    # one-line monologue survive as paragraph 2. One quote-skip: when
    # paragraph 0 is a quote and a planning run starts at paragraph 1,
    # strip the run and keep the quote plus the real answer. When
    # NOTHING survives besides the quote the original is returned —
    # an empty answer is never emitted. <<<
    if i == 0 and len(par) > 1 and par[0].lstrip().startswith(_AWAL_KUTIPAN):
        j = 1
        while j < len(par) and prose_perencanaan_closing(par[j]):
            j += 1
        if 1 < j < len(par):
            return "\n\n".join([par[0]] + par[j:]).lstrip()
        return teks
    if i == 0 or i == len(par):
        return teks
    return "\n\n".join(par[i:]).lstrip()


# >>> UPDATE-026 commit 33 (owner incident 2026-11-02: a confirmation-only
# turn's closing opened with six English monologue paragraphs AND quoted
# the mid-turn challenge verbatim — "[SISTEM - PEMERIKSAAN BUKTI] …" —
# into the user frame; the owner could not find the real answer inside
# it). Three deterministic holes, three code fixes: (1) monologue openers
# the tool-call gate never needed ("Look at …", "The user's prompt …",
# "And the system check says …") join a CLOSING-only predicate superset;
# (2) paragraphs that echo the challenge injection or quote the turn's
# prompt/system-check are scaffolding, never user prose — removed anywhere
# in the closing; (3) the RAW closing is recorded to
# .orion/logs/respon_orion.log so forensics survive outside the chat panel
# (owner directive: keep the panel clean, keep a record). The honest-decline
# escape and the debt block are untouched — disclosure still outranks every
# suppressor. <<<
MARKER_SUNTIKAN_TANTANGAN = (
    "[SISTEM - PEMERIKSAAN BUKTI]",
    "[SYSTEM - EVIDENCE CHECK]",
)
_OPENER_KUTIPAN_MONOLOG = (
    "the user's prompt", "user's prompt in this turn",
    "the system check says", "and the system check says",
)
_OPENER_TAMBAH_CLOSING = (
    "look at", "look ", "look,", "the user's", "the system check",
    "and the system",
)
# >>> Commit 35 (owner transcript 2026-11-02, turn "pelajari file ini"):
# a NEW monologue shape escaped — "Let's follow the rule: 1. Answer the
# question … in your FIRST sentence …" — because (a) it carries no
# verb/tool token from the base lists and (b) the self-referential
# "your" bought the user-addressed escape. These exact self-referential
# meta phrases are planning, never user prose: they outrank the
# user-addressed escape inside the CLOSING predicate only. Bullet
# leaders ("- First sentence: …") are unlabeled planning too. <<<
_META_JELAS = (
    "follow the rule", "craft the response", "double-check all requirements",
    "first sentence", "no completion claim", "no victory/completion words",
    "answer the question that was actually asked",
)
_BULLET_LEADER = "-•* \t"


def prose_perencanaan_closing(teks: str) -> bool:
    """Closing-only superset of ``prose_perencanaan``: same shape rules,
    plus the monologue openers observed in the wild on 2026-11-02 that the
    tool-call gate never sees (closings bypass that gate). Every escape
    (decline / user-addressed / presentational start) is shared with the
    base predicate, so a genuine answer paragraph still stops the chain."""
    if prose_perencanaan(teks):
        return True
    t = (teks or "").strip()
    if not t or len(t) > 600:
        return False
    low = t.lower()
    if guard_rules.ada_penolakan_jujur(t):
        return False
    # >>> Commit 35: self-referential meta planning outranks the
    # user-addressed escape ("… in your FIRST sentence …" is the model
    # talking to itself, not to the user). <<<
    if any(m in low for m in _META_JELAS):
        return True
    if any(m in low for m in _PENANDA_UNTUK_USER):
        return False
    if low.startswith(_PENANDA_AWAL):
        return False
    if not (any(v in low for v in _VERB_RENCANA)
            or any(v in low for v in _TOKEN_TOOL)):
        return False
    kalimat = [
        s.strip().lower()
        for s in re.split(r"[\n;]+|\.\s", t)
    ]
    kalimat = [s for s in (k.strip(_BULLET_LEADER) for k in kalimat) if s]
    return any(k.startswith(_OPENER_TAMBAH_CLOSING)
               or k.startswith(_OPENER_PERENCANAAN) for k in kalimat)


def _paragraf_gema_suntikan(teks: str) -> bool:
    low = (teks or "").lower()
    if any(m.lower() in low for m in MARKER_SUNTIKAN_TANTANGAN):
        return True
    return low.strip().startswith(_OPENER_KUTIPAN_MONOLOG)


def buang_gema_suntikan(teks: str) -> str:
    """Drop paragraphs that echo the mid-turn challenge injection or quote
    the turn's prompt/system-check back at the user. The challenge already
    reaches the user frame through the structured debt block; the raw
    injection text inside a closing is scaffolding leak. When EVERY
    paragraph is echo the original is returned untouched — an empty answer
    is never emitted."""
    if not teks:
        return teks
    par = teks.split("\n\n")
    sisa = [p for p in par if not _paragraf_gema_suntikan(p)]
    if not sisa:
        return teks
    return "\n\n".join(sisa).strip()


def bersih_closing(teks: str, file_berubah: int | None = None) -> str:
    """One entry point for both closing sites: echo removal first (anywhere
    in the text), then zero-change claim removal, then the leading
    planning-paragraph chain. ``file_berubah`` = code-captured count of
    files changed this turn; None skips the claim tooth (unknown)."""
    teks = buang_gema_suntikan(teks)
    if file_berubah is not None:
        teks = buang_klaim_file_bila_nihil(teks, file_berubah)
    return buang_paragraf_perencanaan_awal(teks)


# >>> Commit 34 (handoff §6 residual 5, exam 6 shape): the checkpoint's
# file list is code-captured, but the MODEL'S OWN PROSE above it could
# still claim "changed `app/main.py`" on a turn with ZERO tracked
# changes — two contradictory lists in one message. On a zero-change
# turn, non-negated file-change claim paragraphs that name a path are
# scaffolding-free fiction: stripped. Negated sentences ("tidak ada
# file yang berubah", "no files changed") and honest declines stay —
# disclosure outranks every suppressor. <<<
_MARKER_KLAIM_FILE = (
    "files changed", "file berubah", "file yang berubah", "saya ubah",
    "saya sunting", "saya mengubah", "mengubah file", "mengubah `",
    "berhasil mengubah", "changed `", "successfully wrote",
    "saya perbarui",
)
_NEGASI_KLAIM = ("belum", "tidak", "bukan", "not", "never", "no")
_AWAL_KUTIPAN = (">", '"', "`", "'")
_POLA_PATH = re.compile(
    r"[\w./\\-]+\.(?:py|jsx|js|tsx|ts|html|css|json|md)\b"
)


def _paragraf_klaim_file(teks: str) -> bool:
    low = (teks or "").lower()
    if not low or guard_rules.ada_penolakan_jujur(teks):
        return False
    if not _POLA_PATH.search(low):
        return False
    for m in _MARKER_KLAIM_FILE:
        idx = low.find(m)
        while idx != -1:
            sebelum = low[:idx].split()
            if not any(w in _NEGASI_KLAIM for w in sebelum[-3:]):
                return True
            idx = low.find(m, idx + len(m))
    return False


def buang_klaim_file_bila_nihil(teks: str, jumlah_file_berubah: int) -> str:
    """Drop non-negated file-change claim paragraphs when the code ledger
    says ZERO files changed this turn. Any non-zero count returns the
    text untouched — real work keeps its prose."""
    if jumlah_file_berubah or not teks:
        return teks
    par = teks.split("\n\n")
    sisa = [p for p in par if not _paragraf_klaim_file(p)]
    if not sisa:
        return teks
    return "\n\n".join(sisa).strip()


def rekam_respon_mentah(mentah: str, bersih: str, logger=None) -> None:
    """Owner directive 2026-11-02: record the RAW backend closing to
    ``.orion/logs/respon_orion.log`` so the chat panel carries only the
    cleaned text while forensics stay recoverable. Logging must never
    break a closing — every failure is swallowed."""
    if logger is None:
        try:
            from app import logging_setup
            logger = logging_setup.get_logger("orion.respon")
        except Exception:  # noqa: BLE001 - no logger, no record, no crash
            return
    try:
        mentah = mentah or ""
        bersih = bersih or ""
        if mentah == bersih:
            logger.info("[RESPON] closing clean, no strip (%d chars)",
                        len(mentah))
        else:
            logger.info(
                "[RESPON] raw closing recorded (%d chars, %d stripped):"
                "\n----- RAW -----\n%s\n----- CLEAN -----\n%s",
                len(mentah), len(mentah) - len(bersih), mentah, bersih,
            )
    except Exception:  # noqa: BLE001 - logging must never break a closing
        pass


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
