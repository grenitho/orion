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
  - the history only stores a text PLACEHOLDER: "[LAMPIRAN: error.png (image/png)]"
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
from dataclasses import dataclass, field

# Limits from the Gemini documentation + a safe margin
MAX_GAMBAR = 4                    # per message (3,000 is the API limit, but wasteful of quota)
MAX_BYTE_GAMBAR = 7 * 1024 * 1024  # 7 MB — Gemini inline limit
MAX_BYTE_TEKS = 512 * 1024         # 512 KB for text files
MAX_CHAR_TEKS = 60_000             # cap text content so it cannot blow up the context

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
    "[SISTEM: lampiran berikut GAGAL diproses dan TIDAK terlihat oleh model. "
    "Jangan berasumsi Anda bisa melihatnya. Beri tahu user alasannya.]\n"
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
    nama = str(lamp.get("name") or lamp.get("filename") or "lampiran")
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


def build_user_content(message: str, attachments: list | None = None) -> tuple[list, str]:
    """
    Turn the user message + attachments into (content_parts, history_summary).

    Return:
      parts     -> list of content parts for litellm. When there are no
                   attachments it still returns the plain string (safe for
                   all providers).
      ringkasan -> string for context_manager.add_message(). Contains the
                   user message + attachment placeholders, WITHOUT base64.
    """
    hasil = proses(message, attachments)
    if not hasil.parts:
        # no attachments: return plain text (not a list) so the old
        # behaviour stays 100% unchanged
        return hasil.ringkasan, hasil.ringkasan
    return hasil.parts, hasil.ringkasan


def proses(message: str, attachments: list | None = None) -> HasilLampiran:
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
            out.catatan_gagal.append("lampiran tidak dikenali (bukan objek)")
            continue

        nama, mime = l["nama"], l["mime"] or _tebak_mime(l["nama"])
        mentah = _decode(l["data"])
        if mentah is None:
            out.catatan_gagal.append(f"{nama}: data base64 tidak valid")
            baris_ringkasan.append(f"[LAMPIRAN GAGAL: {nama} — data rusak]")
            continue

        # ---------------- IMAGE ----------------
        if mime in MIME_GAMBAR:
            if jumlah_gambar >= MAX_GAMBAR:
                out.catatan_gagal.append(f"{nama}: melebihi batas {MAX_GAMBAR} gambar/pesan")
                baris_ringkasan.append(f"[LAMPIRAN DITOLAK: {nama} — terlalu banyak gambar]")
                continue
            if len(mentah) > MAX_BYTE_GAMBAR:
                out.catatan_gagal.append(
                    f"{nama}: {len(mentah) / 1024 / 1024:.1f} MB > batas 7 MB Gemini"
                )
                baris_ringkasan.append(f"[LAMPIRAN DITOLAK: {nama} — lebih dari 7 MB]")
                continue

            out.parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(mentah).decode()}"},
            })
            jumlah_gambar += 1
            baris_ringkasan.append(
                f"[LAMPIRAN GAMBAR: {nama} ({mime}, {len(mentah) // 1024} KB) "
                f"— gambar ini HANYA terlihat pada giliran ini]"
            )
            continue

        # ---------------- TEXT FILE ----------------
        ekstensi = ("." + nama.rsplit(".", 1)[-1].lower()) if "." in nama else ""
        if ekstensi in EKSTENSI_TEKS or mime.startswith("text/"):
            if len(mentah) > MAX_BYTE_TEKS:
                out.catatan_gagal.append(f"{nama}: file teks terlalu besar ({len(mentah)} byte)")
                baris_ringkasan.append(f"[LAMPIRAN DITOLAK: {nama} — file terlalu besar]")
                continue
            try:
                isi = mentah.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                out.catatan_gagal.append(f"{nama}: gagal decode sebagai teks")
                continue

            terpotong = len(isi) > MAX_CHAR_TEKS
            if terpotong:
                isi = isi[:MAX_CHAR_TEKS]
            bagian_teks.append(
                f"\n\n----- ISI FILE LAMPIRAN: {nama} -----\n{isi}"
                + ("\n----- [TERPOTONG, file lebih panjang dari ini] -----" if terpotong else "")
                + f"\n----- AKHIR {nama} -----"
            )
            baris_ringkasan.append(
                f"[LAMPIRAN TEKS: {nama} ({len(mentah) // 1024} KB"
                + (", terpotong" if terpotong else "") + ") — isinya ada di pesan ini]"
            )
            continue

        # ---------------- UNSUPPORTED ----------------
        out.catatan_gagal.append(f"{nama}: tipe '{mime or ekstensi or 'tidak dikenal'}' tidak didukung")
        baris_ringkasan.append(
            f"[LAMPIRAN DITOLAK: {nama} — tipe tidak didukung. "
            f"Yang bisa: gambar (png/jpg/webp/heic) atau file teks/kode]"
        )

    if out.catatan_gagal:
        bagian_teks.append(PESAN_GAGAL + "\n".join(f"- {c}" for c in out.catatan_gagal))

    teks_gabung = "".join(bagian_teks).strip()
    out.ringkasan = "\n".join([teks_gabung] + baris_ringkasan).strip() if baris_ringkasan else teks_gabung

    # When there are images, the text must be the first part in the content list
    if jumlah_gambar:
        out.parts.insert(0, {"type": "text", "text": teks_gabung or "(lihat gambar terlampir)"})

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
