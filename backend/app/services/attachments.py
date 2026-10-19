"""
attachments.py — dukungan lampiran gambar & file untuk ORION
============================================================
FILE BARU. Letakkan di: backend/app/services/attachments.py

KENAPA FITUR INI COCOK UNTUK STACK ANDA
---------------------------------------
`gemini-3.5-flash-lite` mendukung INPUT GAMBAR (dokumentasi Google:
"Modalities: Text — input and output; Image — input only").
Batasnya:
  - maksimum 3.000 gambar per prompt
  - maksimum 7 MB per file (inline)
  - format: image/png, image/jpeg, image/webp, image/heic, image/heif

litellm menerima format OpenAI (`image_url` berisi data URI base64) dan
menerjemahkannya ke `inline_data` Gemini. Jadi tidak perlu SDK tambahan.

KEPUTUSAN DESAIN PENTING
------------------------
Gambar TIDAK disimpan ke riwayat (`.orion/session_history.json`). Alasannya:
base64 satu screenshot 1 MB menjadi ±1,4 juta karakter. Kalau masuk riwayat,
setiap iterasi agent akan mengirim ulang gambar itu — kuota free tier Anda
habis dalam beberapa pesan, dan file riwayat membengkak permanen.

Yang dilakukan modul ini:
  - riwayat hanya menyimpan PLACEHOLDER teks: "[LAMPIRAN: error.png (image/png)]"
  - gambar asli disuntikkan HANYA ke permintaan pertama pada giliran itu
  - file teks (bukan gambar) dibaca isinya dan ditempel sebagai teks biasa,
    karena itu murah dan tetap berguna di iterasi berikutnya

CARA PAKAI (di main.py) — lihat PANDUAN_FITUR_ATTACHMENT.md
    from app.services.attachments import build_user_content, describe_attachments

    parts, ringkasan = build_user_content(request.message, request.attachments)
    context_manager.add_message("user", ringkasan)   # placeholder ke riwayat
    # `parts` dipakai untuk api_messages giliran pertama
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field

# Batas dari dokumentasi Gemini + margin aman
MAX_GAMBAR = 4                    # per pesan (3.000 itu batas API, tapi boros kuota)
MAX_BYTE_GAMBAR = 7 * 1024 * 1024  # 7 MB — batas inline Gemini
MAX_BYTE_TEKS = 512 * 1024         # 512 KB untuk file teks
MAX_CHAR_TEKS = 60_000             # potong isi teks supaya tidak meledakkan konteks

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
    """Hasil pemrosesan lampiran untuk satu pesan user."""

    parts: list = field(default_factory=list)   # content parts untuk litellm
    ringkasan: str = ""                          # placeholder teks untuk riwayat
    catatan_gagal: list = field(default_factory=list)


def _normalkan(lamp: dict) -> dict:
    """Terima berbagai bentuk payload dari extension."""
    if not isinstance(lamp, dict):
        return {}
    nama = str(lamp.get("name") or lamp.get("filename") or "lampiran")
    mime = str(lamp.get("mime") or lamp.get("mimeType") or lamp.get("type") or "").lower()
    data = lamp.get("data") or lamp.get("base64") or lamp.get("content") or ""

    # terima data URI penuh ("data:image/png;base64,....") maupun base64 polos
    if isinstance(data, str) and data.startswith("data:"):
        kepala, _, sisa = data.partition(",")
        if not mime and ";" in kepala:
            mime = kepala[5:].split(";")[0].lower()
        data = sisa

    return {"nama": nama, "mime": mime, "data": data}


def _decode(data) -> bytes | None:
    """Decode base64. Return None kalau tidak valid.

    PENTING: `base64.b64decode(..., validate=False)` TIDAK melempar error untuk
    input seperti "!!!" -- ia membuang karakter tak valid lalu mengembalikan
    byte KOSONG. Tanpa pemeriksaan di bawah, lampiran rusak akan lolos sebagai
    "gambar 0 KB" dan dikirim ke model, yang lalu mengarang isinya.
    """
    if isinstance(data, bytes):
        return data if data else None
    if not isinstance(data, str) or not data.strip():
        return None
    try:
        hasil = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    return hasil or None      # byte kosong = base64 rusak, bukan file kosong


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
    Ubah pesan user + lampiran jadi (content_parts, ringkasan_untuk_riwayat).

    Return:
      parts     -> list content part untuk litellm. Kalau tidak ada lampiran,
                   isinya tetap list 1 elemen (aman untuk semua provider).
      ringkasan -> string untuk context_manager.add_message(). Berisi pesan user
                   + placeholder lampiran, TANPA base64.
    """
    hasil = proses(message, attachments)
    if not hasil.parts:
        # tidak ada lampiran: kembalikan teks polos (bukan list) supaya
        # perilaku lama 100% tidak berubah
        return hasil.ringkasan, hasil.ringkasan
    return hasil.parts, hasil.ringkasan


def proses(message: str, attachments: list | None = None) -> HasilLampiran:
    """Inti pemrosesan. Dipisah supaya mudah di-unit-test."""
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

        # ---------------- GAMBAR ----------------
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

        # ---------------- FILE TEKS ----------------
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

        # ---------------- TIDAK DIDUKUNG ----------------
        out.catatan_gagal.append(f"{nama}: tipe '{mime or ekstensi or 'tidak dikenal'}' tidak didukung")
        baris_ringkasan.append(
            f"[LAMPIRAN DITOLAK: {nama} — tipe tidak didukung. "
            f"Yang bisa: gambar (png/jpg/webp/heic) atau file teks/kode]"
        )

    if out.catatan_gagal:
        bagian_teks.append(PESAN_GAGAL + "\n".join(f"- {c}" for c in out.catatan_gagal))

    teks_gabung = "".join(bagian_teks).strip()
    out.ringkasan = "\n".join([teks_gabung] + baris_ringkasan).strip() if baris_ringkasan else teks_gabung

    # Kalau ada gambar, teks harus jadi part pertama dalam list content
    if jumlah_gambar:
        out.parts.insert(0, {"type": "text", "text": teks_gabung or "(lihat gambar terlampir)"})

    return out


def describe_attachments(attachments: list | None) -> str:
    """Ringkasan satu baris untuk log / event status ke UI."""
    if not attachments:
        return ""
    nama = []
    for a in attachments[:6]:
        l = _normalkan(a)
        if l:
            nama.append(l["nama"])
    ekstra = f" (+{len(attachments) - 6} more)" if len(attachments) > 6 else ""
    return f"{len(attachments)} attachments: {', '.join(nama)}{ekstra}"
