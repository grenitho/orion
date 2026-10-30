"""
attachments.py — image & file attachment support for ORION
============================================================

WHY THIS FEATURE FITS THE STACK
--------------------------------
`gemini-3.5-flash-lite` supports IMAGE INPUT (Google documentation:
"Modalities: Text — input and output; Image — input only").
The limits:
  - maximum 3,000 images per prompt
  - maximum 7 MB per file (inline)
  - formats: image/png, image/jpeg, image/webp, image/heic, image/heif

litellm accepts the OpenAI format (`image_url` containing a base64 data URI)
and translates it to Gemini's `inline_data`. No extra SDK needed.

IMPORTANT DESIGN DECISION
--------------------------
Images are NOT stored in the history (`.orion/session_history.json`). Reason:
the base64 of a single 1 MB screenshot becomes ±1.4 million characters. If it
entered the history, every agent iteration would resend that image — your free
tier quota would be gone within a few messages, and the history file would
stay bloated permanently.

What this module does:
  - the history only stores a text PLACEHOLDER: "[ATTACHMENT: error.png (image/png)]"
  - the actual image is injected ONLY into the first request of that turn
  - text files (not images) have their contents read and pasted as plain text,
    because that is cheap and stays useful in the following iterations

USAGE (in main.py)
    from app.services.attachments import build_user_content, describe_attachments

    parts, ringkasan = build_user_content(request.message, request.attachments)
    context_manager.add_message("user", ringkasan)   # placeholder into history
    # `parts` is used for the first turn's api_messages
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# >>> UPDATE-021: pdf/docx/xlsx attachments are extracted with the SAME
# engine as the inbox drop-zone (soft-imported parsers, announced caps).
# A broken inbox_manager must not kill the whole attachment pipeline, so
# the import is guarded: without it, documents degrade to a per-file
# rejection note exactly like an unsupported type.
try:
    from app.services import inbox_manager as _inbox
except Exception:  # pragma: no cover - depends on env
    _inbox = None

# Limits from the Gemini documentation + a safe margin
MAX_GAMBAR = 4                    # per message (3,000 is the API limit, but wasteful of quota)
MAX_BYTE_GAMBAR = 7 * 1024 * 1024  # 7 MB — Gemini inline limit
MAX_BYTE_TEKS = 512 * 1024         # 512 KB for text files
MAX_CHAR_TEKS = 60_000             # cap text content so it cannot blow up the context
# >>> UPDATE-021: document families (pdf/docx/xlsx) ride the inbox caps:
# 20 MB per file; extraction caps (pages/tables/rows/chars) live in
# inbox_manager and announce themselves inside the extracted text.
EKSTENSI_DOKUMEN = {".pdf", ".docx", ".xlsx"}
MAX_BYTE_DOKUMEN = 20 * 1024 * 1024

MIME_GAMBAR = {
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/heic", "image/heif",
}
EKSTENSI_TEKS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".scss", ".sql", ".sh", ".bat",
    ".ps1", ".xml", ".csv", ".log", ".env", ".gitignore", ".dockerfile",
}

PESAN_GAGAL = (
    "[SYSTEM: the following attachments FAILED to process and are NOT visible to the model. "
    "Do not assume you can see them. Tell the user why.]\n"
)


@dataclass
class HasilLampiran:
    """Processing result of the attachments for one user message."""

    parts: list = field(default_factory=list)   # content parts for litellm
    ringkasan: str = ""                          # text placeholder for the history
    catatan_gagal: list = field(default_factory=list)


def _normalkan(lamp: dict) -> dict:
    """Accept the various payload shapes sent by the extension."""
    if not isinstance(lamp, dict):
        return {}
    nama = str(lamp.get("name") or lamp.get("filename") or "attachment")
    mime = str(lamp.get("mime") or lamp.get("mimeType") or lamp.get("type") or "").lower()
    data = lamp.get("data") or lamp.get("base64") or lamp.get("content") or ""

    # accept a full data URI ("data:image/png;base64,....") as well as plain base64
    if isinstance(data, str) and data.startswith("data:"):
        kepala, _, sisa = data.partition(",")
        if not mime and ";" in kepala:
            mime = kepala[5:].split(";")[0].lower()
        data = sisa

    return {"nama": nama, "mime": mime, "data": data}


def _decode(data) -> bytes | None:
    """Decode base64. Return None when invalid.

    IMPORTANT: `base64.b64decode(..., validate=False)` does NOT raise for
    input like "!!!" -- it discards invalid characters and returns EMPTY
    bytes. Without the check below, a broken attachment would slip through
    as a "0 KB image" and be sent to the model, which would then invent its
    contents.
    """
    if isinstance(data, bytes):
        return data if data else None
    if not isinstance(data, str) or not data.strip():
        return None
    try:
        hasil = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    return hasil or None      # empty bytes = broken base64, not an empty file


def _tebak_mime(nama: str) -> str:
    n = nama.lower()
    if n.endswith(".png"):
        return "image/png"
    if n.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if n.endswith(".webp"):
        return "image/webp"
    if n.endswith(".heic"):
        return "image/heic"
    if n.endswith(".heif"):
        return "image/heif"
    return ""


def _ekstrak_dokumen(nama: str, ekstensi: str, mentah: bytes) -> tuple[str | None, str]:
    """UPDATE-021: extract pdf/docx/xlsx bytes via the inbox engine.

    Returns (text, '') on success or (None, reason) on skip/error. The
    bytes go through a temp file because inbox_manager.extract_file works
    on paths (size checks, parser APIs); the file is removed afterwards.
    """
    if _inbox is None:
        return None, "document support unavailable (inbox_manager failed to import)"
    fd, path = tempfile.mkstemp(suffix=ekstensi, prefix="orion-lampiran-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(mentah)
        kind, payload = _inbox.extract_file(Path(path))
    except Exception as exc:  # noqa: BLE001 - never raise into the route
        return None, f"extraction crashed: {type(exc).__name__}: {str(exc)[:120]}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if kind == "text":
        return payload, ""
    return None, str(payload)


def _simpan_referensi(project_path: str | None, nama: str, mentah: bytes):
    """UPDATE-025 commit 11 (owner incident: invoiceflow.html "not on
    disk" two turns after attaching it): attachments used to die with
    the message that carried them — the inline copy is summarized/pruned
    away and no file ever existed, so a multi-turn implementation task
    lost its own reference and the model improvised a design. Persist
    text-ish attachments under ``.orion/attachments/`` so ANY later turn
    can read_file them again. Returns the project-relative path or None.
    """
    if not project_path:
        return None
    try:
        dirr = os.path.join(project_path, ".orion", "attachments")
        os.makedirs(dirr, exist_ok=True)
        aman = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(nama))[:80]
        if not aman:
            aman = "lampiran.txt"
        with open(os.path.join(dirr, aman), "wb") as fh:
            fh.write(mentah)
        return f".orion/attachments/{aman}"
    except OSError:
        return None


def build_user_content(message: str, attachments: list | None = None,
                       project_path: str | None = None) -> tuple[list, str]:
    """
    Turn the user message + attachments into (content_parts, history_summary).

    Return:
      parts     -> list of content parts for litellm. When there are no
                   attachments it still returns the plain string (safe for
                   all providers).
      ringkasan -> string for context_manager.add_message(). Contains the
                   user message + attachment placeholders, WITHOUT base64.
    """
    hasil = proses(message, attachments, project_path=project_path)
    if not hasil.parts:
        # no attachments: return plain text (not a list) so the old
        # behaviour stays 100% unchanged
        return hasil.ringkasan, hasil.ringkasan
    return hasil.parts, hasil.ringkasan


def proses(message: str, attachments: list | None = None,
           project_path: str | None = None) -> HasilLampiran:
    """The processing core. Split out so it is easy to unit-test."""
    out = HasilLampiran()
    pesan = (message or "").strip()
    lampiran = attachments or []

    baris_ringkasan: list[str] = []
    bagian_teks: list[str] = []
    jumlah_gambar = 0

    if pesan:
        bagian_teks.append(pesan)

    for item in lampiran:
        l = _normalkan(item)
        if not l:
            out.catatan_gagal.append("unrecognized attachment (not an object)")
            continue

        nama, mime = l["nama"], l["mime"] or _tebak_mime(l["nama"])
        mentah = _decode(l["data"])
        if mentah is None:
            out.catatan_gagal.append(f"{nama}: invalid base64 data")
            baris_ringkasan.append(f"[ATTACHMENT FAILED: {nama} — corrupted data]")
            continue

        # ---------------- IMAGE ----------------
        if mime in MIME_GAMBAR:
            if jumlah_gambar >= MAX_GAMBAR:
                out.catatan_gagal.append(f"{nama}: exceeds the limit of {MAX_GAMBAR} images/message")
                baris_ringkasan.append(f"[ATTACHMENT REJECTED: {nama} — too many images]")
                continue
            if len(mentah) > MAX_BYTE_GAMBAR:
                out.catatan_gagal.append(
                    f"{nama}: {len(mentah) / 1024 / 1024:.1f} MB > Gemini's 7 MB limit"
                )
                baris_ringkasan.append(f"[ATTACHMENT REJECTED: {nama} — larger than 7 MB]")
                continue

            out.parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(mentah).decode()}"},
            })
            jumlah_gambar += 1
            baris_ringkasan.append(
                f"[IMAGE ATTACHMENT: {nama} ({mime}, {len(mentah) // 1024} KB) "
                f"— this image is ONLY visible on this turn]"
            )
            continue

        # ---------------- DOCUMENT (UPDATE-021) ----------------
        ekstensi = ("." + nama.rsplit(".", 1)[-1].lower()) if "." in nama else ""
        if ekstensi in EKSTENSI_DOKUMEN:
            if len(mentah) > MAX_BYTE_DOKUMEN:
                out.catatan_gagal.append(
                    f"{nama}: document too large ({len(mentah) / 1024 / 1024:.1f} MB > 20 MB)"
                )
                baris_ringkasan.append(f"[ATTACHMENT REJECTED: {nama} — larger than 20 MB]")
                continue
            teks_dok, alasan = _ekstrak_dokumen(nama, ekstensi, mentah)
            if teks_dok is None:
                out.catatan_gagal.append(f"{nama}: {alasan}")
                baris_ringkasan.append(f"[ATTACHMENT REJECTED: {nama} — {alasan}]")
                continue
            bagian_teks.append(
                f"\n\n----- ATTACHED DOCUMENT CONTENTS: {nama} (extracted) -----\n{teks_dok}"
                + f"\n----- END OF {nama} -----"
            )
            _rel_doc = _simpan_referensi(project_path, nama, mentah)
            baris_ringkasan.append(
                f"[DOCUMENT ATTACHMENT: {nama} ({len(mentah) // 1024} KB, "
                f"{len(teks_dok)} chars extracted) — its text is in this message"
                + (
                    f"; a durable copy lives at {_rel_doc} — read_file it on any "
                    "later turn, it survives context summarization"
                    if _rel_doc else ""
                )
                + "]"
            )
            continue

        # ---------------- TEXT FILE ----------------
        if ekstensi in EKSTENSI_TEKS or mime.startswith("text/"):
            if len(mentah) > MAX_BYTE_TEKS:
                out.catatan_gagal.append(f"{nama}: text file too large ({len(mentah)} bytes)")
                baris_ringkasan.append(f"[ATTACHMENT REJECTED: {nama} — file too large]")
                continue
            try:
                isi = mentah.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                out.catatan_gagal.append(f"{nama}: failed to decode as text")
                continue

            terpotong = len(isi) > MAX_CHAR_TEKS
            if terpotong:
                isi = isi[:MAX_CHAR_TEKS]
            bagian_teks.append(
                f"\n\n----- ATTACHED FILE CONTENTS: {nama} -----\n{isi}"
                + ("\n----- [TRUNCATED, the file is longer than this] -----" if terpotong else "")
                + f"\n----- END OF {nama} -----"
            )
            _rel_teks = _simpan_referensi(project_path, nama, mentah)
            baris_ringkasan.append(
                f"[TEXT ATTACHMENT: {nama} ({len(mentah) // 1024} KB"
                + (", truncated" if terpotong else "")
                + ") — its contents are in this message"
                + (
                    f"; a durable copy lives at {_rel_teks} — read_file it on "
                    "any later turn, it survives context summarization"
                    if _rel_teks else ""
                )
                + "]"
            )
            continue

        # ---------------- UNSUPPORTED ----------------
        out.catatan_gagal.append(f"{nama}: type '{mime or ekstensi or 'unknown'}' is not supported")
        baris_ringkasan.append(
            f"[ATTACHMENT REJECTED: {nama} — unsupported type. "
            f"Supported: images (png/jpg/webp/heic) or text/code files]"
        )

    if out.catatan_gagal:
        bagian_teks.append(PESAN_GAGAL + "\n".join(f"- {c}" for c in out.catatan_gagal))

    teks_gabung = "".join(bagian_teks).strip()
    out.ringkasan = "\n".join([teks_gabung] + baris_ringkasan).strip() if baris_ringkasan else teks_gabung

    # When there are images, the text must be the first part in the content list
    if jumlah_gambar:
        out.parts.insert(0, {"type": "text", "text": teks_gabung or "(see the attached image)"})

    return out


def describe_attachments(attachments: list | None) -> str:
    """One-line summary for the log / UI status event."""
    if not attachments:
        return ""
    nama = []
    for a in attachments[:6]:
        l = _normalkan(a)
        if l:
            nama.append(l["nama"])
    ekstra = f" (+{len(attachments) - 6} more)" if len(attachments) > 6 else ""
    return f"{len(attachments)} attachments: {', '.join(nama)}{ekstra}"
