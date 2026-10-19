"""
post_edit_verifier.py

Verifikasi otomatis pasca-edit: setelah write_file/edit_file dieksekusi,
baca ULANG file dari disk untuk membuktikan perubahan benar-benar tersimpan
sesuai ekspektasi -- bukan cuma percaya string return value dari tool tanpa
bukti nyata.

Didesain sebagai LAPISAN TAMBAHAN yang di-append ke hasil tool yang sudah
ada (lihat integrasi di main.py). Tidak mengubah format lama, tidak mengubah
perilaku write_file/edit_file itu sendiri -- kalau modul ini gagal impor atau
error, tool asli tetap bekerja seperti biasa (lihat try/except di sisi
pemanggil pada main.py).
"""

import os
import re

_REPLACE_BLOCK_RE = re.compile(r"=======\s*\n(.*?)\n>>>>>>> REPLACE", re.DOTALL)


def extract_replace_snippet(diff_text: str, max_len: int = 200) -> str:
    """
    Ambil satu baris pertama yang tidak kosong dari blok REPLACE pada format
    SEARCH/REPLACE (<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE), untuk
    dipakai sebagai bukti verifikasi ringan. Sengaja tidak mencocokkan seluruh
    blok supaya tidak false-negative akibat perbedaan whitespace/indentasi
    minor yang tidak signifikan.
    """
    if not diff_text:
        return ""
    match = _REPLACE_BLOCK_RE.search(diff_text)
    if not match:
        return ""
    replace_content = match.group(1).strip()
    lines = [ln for ln in replace_content.split("\n") if ln.strip()]
    if not lines:
        return ""
    return lines[0].strip()[:max_len]


def verify_file_write(full_path: str, expected_content: str = None) -> str:
    """
    Verifikasi setelah write_file: pastikan file benar-benar ada di disk,
    dan (kalau expected_content diberikan) isinya persis cocok.
    """
    if not os.path.exists(full_path):
        return (
            "⚠️ [VERIFICATION FAILED] File not found on disk after "
            "write_file was called. The write likely failed silently "
            "(permissions, full disk, or wrong path)."
        )

    try:
        actual_size = os.path.getsize(full_path)
    except Exception as e:
        return f"⚠️ [VERIFICATION FAILED] File exists but its size could not be read: {e}"

    if expected_content is not None:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                actual_content = f.read()
        except Exception as e:
            return f"⚠️ [VERIFICATION FAILED] File exists but could not be re-read: {e}"

        if actual_content != expected_content:
            return (
                "⚠️ [VERIFICATION MISMATCH] File saved, but its contents differ "
                "from the content that was sent (possibly line-ending normalization, or "
                "another process overwrote this file afterwards). You MUST re-read this "
                "file before continuing."
            )

    return f"✅ [VERIFICATION OK] File confirmed saved on disk ({actual_size} bytes)."


def verify_file_edit(full_path: str, expected_snippet: str) -> str:
    """
    Verifikasi setelah edit_file: pastikan potongan dari blok REPLACE
    benar-benar muncul di file hasil akhir.
    """
    if not os.path.exists(full_path):
        return "⚠️ [VERIFICATION FAILED] File not found on disk after edit_file was called."

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            actual_content = f.read()
    except Exception as e:
        return f"⚠️ [VERIFICATION FAILED] File exists but could not be re-read: {e}"

    snippet = (expected_snippet or "").strip()
    if not snippet:
        # Tidak ada snippet spesifik untuk dicocokkan (format diff tidak standar) --
        # tetap laporkan file ada, tapi jangan klaim isi sudah benar.
        return "ℹ️ [VERIFICATION PARTIAL] File exists on disk, but there is no specific snippet to match automatically. A manual re-read is recommended if in doubt."

    if snippet in actual_content:
        return "✅ [VERIFICATION OK] REPLACE snippet confirmed present in the final file."

    return (
        "⚠️ [VERIFICATION MISMATCH] REPLACE snippet NOT found in the "
        "final file. The edit may have failed silently, or the SEARCH/REPLACE format "
        "did not match the file contents exactly. You MUST re-read this file "
        "(read_file) before continuing or claiming the task is complete."
    )