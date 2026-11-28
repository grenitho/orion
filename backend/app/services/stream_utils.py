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
                logger.info(
                    "[STREAM] Stream ended without done; the wrapper is sending it."
                )
            except Exception:  # noqa: BLE001 - logging must never break the stream
                pass
        yield buat_event_done()


_OPENER_PERENCANAAN = (
    "let's",
    "let me",
    "i'll",
    "i will",
    "i ",
    "i'",
    "we ",
    "we'",
    "first,",
    "first ",
    "wait,",
    "wait ",
    "wait —",
    "hmm,",
    "hmm ",
    "okay,",
    "okay ",
    "ok,",
    "ok ",
    "now,",
    "now ",
    "next,",
    "next ",
    "ah,",
    "ah ",
    "aha",
    "yes,",
    "yes ",
    "so,",
    "so ",
    "right,",
    "right ",
    "good,",
    "good ",
    "nice,",
    "nice ",
    "here ",
    "there ",
    "since",
    "because",
    "since ",
    "saya akan",
    "saya ",
    "kami ",
    "mari kita",
    "coba ",
    "oke,",
    "oke ",
    "pertama,",
    "pertama ",
    "tunggu,",
    "tunggu ",
    "hmm,",
    "baik,",
    "baik ",
)
_VERB_RENCANA = (
    "check",
    "read",
    "run",
    "verify",
    "look",
    "search",
    "grep",
    "open",
    "inspect",
    "compare",
    "call",
    "navigate",
    "snapshot",
    "close",
    "cek",
    "baca",
    "jalankan",
    "periksa",
    "cari",
    "lihat",
    "bandingkan",
    "panggil",
    "tutup",
    "review",
    "format",
    "present",
    "update",
    "summarize",
    "ensure",
    "mark",
    "complete",
    "susun",
    "rapikan",
    "pastikan",
    "tandai",
    "ingat",
    "make",
    "use",
    "try",
    "write",
    "rewrite",
    "start",
    "serve",
    "delete",
    "remove",
    "buat",
    "tulis",
    "hapus",
    "mulai",
    "coba",
)
_TOKEN_TOOL = (
    "git_diff",
    "git_status",
    "git_commit",
    "read_file",
    "edit_file",
    "write_file",
    "search",
    "grep",
    "terminal",
    "bash",
    "playwright",
    "mcp",
    "todo",
    "app.jsx",
    "uvicorn",
    "npm",
    "pytest",
    "browser_",
)
# Boundary-aware user-addressed markers so substrings inside words like
# "standar" / "standard" / "menandai" never escape as user-addressed.
_RE_PENANDA_USER = re.compile(
    r"(?:\b(?:anda|kamu|you|your)\b|jawab:|answer:)",
    re.IGNORECASE,
)
_PENANDA_AWAL = (
    "here is",
    "here are",
    "berikut",
    "ini adalah",
    "ringkasan",
    "summary",
)


def _ada_penanda_user(teks: str) -> bool:
    return bool(_RE_PENANDA_USER.search(teks or ""))


def prose_perencanaan(teks: str) -> bool:
    """True when a tool-call message's content is planning monologue that
    must stay OUT of the user frame (the context keeps the raw text, so
    the model still remembers its own plan)."""
    t = (teks or "").strip()
    if not t or len(t) > 600:
        return False
    low = t.lower()
    if guard_rules.ada_penolakan_jujur(t):
        return False
    if _ada_penanda_user(t):
        return False
    if low.startswith(_PENANDA_AWAL):
        return False
    if not (any(v in low for v in _VERB_RENCANA) or any(v in low for v in _TOKEN_TOOL)):
        return False
    kalimat = [s.strip().lower() for s in re.split(r"[\n;]+|\.\s", t) if s.strip()]
    return any(k.startswith(_OPENER_PERENCANAAN) for k in kalimat)


def catatan_untuk_user(teks: str) -> bool:
    """UPDATE-026 P2: Filter monolog bahasa Inggris & instruksi sistem."""
    t = (teks or "").strip()
    if not t:
        return False
    if guard_rules.ada_penolakan_jujur(t):
        return True
    low = t.lower()

    if any(m.lower() in low for m in MARKER_SUNTIKAN_TANTANGAN):
        return False

    _kata_terlarang = (
        "let's verify",
        "let's check",
        "the user's instruction",
        "system injection",
        "let's craft",
        "the user asked",
        "according to policy",
        "let's respond",
        "my analysis",
    )
    if any(k in low for k in _kata_terlarang):
        return False

    if prose_perencanaan(t):
        return False

    if low.startswith(_PENANDA_AWAL):
        return True
    if _ada_penanda_user(t):
        return True
    return t.rstrip().endswith("?")


def buang_paragraf_perencanaan_awal(teks: str) -> str:
    """UPDATE-025 commit 29: strip LEADING planning paragraphs from a
    closing text without stopping on extra blank lines."""
    if not teks:
        return teks
    par = [p for p in re.split(r"\n{2,}", teks) if p.strip()]
    if not par:
        return teks
    i = 0
    while i < len(par) and prose_perencanaan_closing(par[i]):
        i += 1
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


MARKER_SUNTIKAN_TANTANGAN = (
    "[SISTEM - PEMERIKSAAN BUKTI]",
    "[SYSTEM - EVIDENCE CHECK]",
)
_OPENER_KUTIPAN_MONOLOG = (
    "the user's prompt",
    "user's prompt in this turn",
    "the system check says",
    "and the system check says",
)
_OPENER_TAMBAH_CLOSING = (
    "look at",
    "look ",
    "look,",
    "the user's",
    "the system check",
    "and the system",
)
_META_JELAS = (
    "follow the rule",
    "craft the response",
    "double-check all requirements",
    "first sentence",
    "no completion claim",
    "no victory/completion words",
    "answer the question that was actually asked",
)
_BULLET_LEADER = "-•* \t"


def prose_perencanaan_closing(teks: str) -> bool:
    """Closing-only superset of ``prose_perencanaan``."""
    if prose_perencanaan(teks):
        return True
    t = (teks or "").strip()
    if not t or len(t) > 600:
        return False
    low = t.lower()
    if guard_rules.ada_penolakan_jujur(t):
        return False
    if any(m in low for m in _META_JELAS):
        return True
    _kepala = low.lstrip(_BULLET_LEADER)
    if _kepala.startswith(_OPENER_PERENCANAAN + _OPENER_TAMBAH_CLOSING) and (
        any(v in low for v in _VERB_RENCANA) or any(v in low for v in _TOKEN_TOOL)
    ):
        return True
    if _ada_penanda_user(t):
        return False
    if low.startswith(_PENANDA_AWAL):
        return False
    if not (any(v in low for v in _VERB_RENCANA) or any(v in low for v in _TOKEN_TOOL)):
        return False
    kalimat = [s.strip().lower() for s in re.split(r"[\n;]+|\.\s", t)]
    kalimat = [s for s in (k.strip(_BULLET_LEADER) for k in kalimat) if s]
    return any(
        k.startswith(_OPENER_TAMBAH_CLOSING) or k.startswith(_OPENER_PERENCANAAN)
        for k in kalimat
    )


def _paragraf_gema_suntikan(teks: str) -> bool:
    low = (teks or "").lower()
    if any(m.lower() in low for m in MARKER_SUNTIKAN_TANTANGAN):
        return True
    return low.strip().startswith(_OPENER_KUTIPAN_MONOLOG)


def buang_gema_suntikan(teks: str) -> str:
    """Drop paragraphs that echo the mid-turn challenge injection or quote
    the turn's prompt/system-check back at the user."""
    if not teks:
        return teks
    par = re.split(r"\n{2,}", teks)
    sisa = [p for p in par if p.strip() and not _paragraf_gema_suntikan(p)]
    if not sisa:
        return teks
    return "\n\n".join(sisa).strip()


def bersih_closing(teks: str, file_berubah: int | None = None) -> str:
    """One entry point for both closing sites."""
    teks = buang_gema_suntikan(teks)
    if file_berubah is not None:
        teks = buang_klaim_file_bila_nihil(teks, file_berubah)
    return buang_paragraf_perencanaan_awal(teks)


_MARKER_KLAIM_FILE = (
    "files changed",
    "file berubah",
    "file yang berubah",
    "saya ubah",
    "saya sunting",
    "saya mengubah",
    "mengubah file",
    "mengubah `",
    "berhasil mengubah",
    "changed `",
    "successfully wrote",
    "saya perbarui",
)
_NEGASI_KLAIM = ("belum", "tidak", "bukan", "not", "never", "no")
_AWAL_KUTIPAN = (">", '"', "`", "'")
_POLA_PATH = re.compile(r"[\w./\\-]+\.(?:py|jsx|js|tsx|ts|html|css|json|md)\b")


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
    says ZERO files changed this turn."""
    if jumlah_file_berubah or not teks:
        return teks
    par = re.split(r"\n{2,}", teks)
    sisa = [p for p in par if p.strip() and not _paragraf_klaim_file(p)]
    if not sisa:
        return teks
    return "\n\n".join(sisa).strip()


def rekam_respon_mentah(mentah: str, bersih: str, logger=None) -> None:
    """Owner directive 2026-11-02: record the RAW backend closing to
    ``.orion/logs/respon_orion.log`` so the chat panel carries only the
    cleaned text while forensics stay recoverable."""
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
            logger.info("[RESPON] closing clean, no strip (%d chars)", len(mentah))
        else:
            logger.info(
                "[RESPON] raw closing recorded (%d chars, %d stripped):"
                "\n----- RAW -----\n%s\n----- CLEAN -----\n%s",
                len(mentah),
                len(mentah) - len(bersih),
                mentah,
                bersih,
            )
    except Exception:  # noqa: BLE001 - logging must never break a closing
        pass


def tambah_ledger_gaya(teks: str, bukti) -> str:
    """UPDATE-025 commit 30: append the code-captured read values to a
    closing text."""
    sisa = [b for b in (bukti or []) if b and b not in (teks or "")]
    if not sisa:
        return teks or ""
    blok = (
        "\n\n📐 NILAI TERBACA dari browser turn ini (ledger gaya, "
        "ditangkap kode — bukan klaim model):\n" + "\n".join(f"- {b}" for b in sisa[:6])
    )
    return (teks or "") + blok
