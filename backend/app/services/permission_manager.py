"""
permission_manager.py

Lapisan proteksi tambahan (defense-in-depth) untuk memastikan operasi file
(write_file/edit_file) tidak pernah keluar dari batas project_path --
mencegah path traversal (mis. file_path="../../../etc/passwd", atau path
absolut ke lokasi lain) baik akibat kesalahan model maupun kemungkinan
prompt injection dari isi file yang dibaca sebelumnya.

Modul ini bersifat ADDITIVE murni: hanya menambah pemeriksaan BARU sebelum
tool dieksekusi. Untuk path yang valid (kasus normal, 99.9% penggunaan
sehari-hari), fungsi ini selalu mengembalikan (True, "OK") dan sama sekali
tidak mengubah perilaku yang sudah stabil.
"""

import os

# Pola nama file/folder sensitif yang sebaiknya tidak ditimpa otomatis oleh
# model tanpa konfirmasi eksplisit dari user, meskipun path-nya valid secara
# teknis (masih di dalam project). Ini heuristik SOFT -- tujuannya mencegah
# insiden tidak sengaja (misal model mencoba "membersihkan" .env), bukan
# proteksi keamanan yang absolut.
SENSITIVE_PATTERNS = (
    ".env",
    ".git/",
    "id_rsa",
    ".pem",
    ".key",
    ".pfx",
)


def is_path_safe(project_path: str, file_path: str):
    """
    Return (is_safe: bool, reason: str).

    is_safe True HANYA jika file_path, setelah di-resolve secara absolut,
    tetap berada di dalam project_path. False jika terdeteksi path
    traversal (../..), path absolut ke luar project, atau beda drive
    (khusus Windows).
    """
    if not project_path:
        return False, "project_path has not been set in the system."
    if not file_path or not str(file_path).strip():
        return False, "file_path is empty."

    try:
        project_root = os.path.realpath(project_path)
        target_path = os.path.realpath(os.path.join(project_path, file_path))
    except Exception as e:
        return False, f"Failed to resolve path: {e}"

    try:
        common = os.path.commonpath([project_root, target_path])
    except ValueError:
        # Terjadi kalau project & target di drive berbeda (khusus Windows,
        # misal project di D:\ tapi target ter-resolve ke C:\)
        return False, (
            f"Path '{file_path}' points to a different drive/location than the "
            f"project root ('{project_root}'). Operation denied for security."
        )

    if common != project_root:
        return False, (
            f"Path '{file_path}' resolves OUTSIDE the project folder "
            f"('{project_root}'). Operation denied to prevent path traversal."
        )

    return True, "OK"


def is_sensitive_file(file_path: str) -> bool:
    """Heuristik sederhana: apakah file ini kemungkinan berisi
    secret/credential yang sebaiknya tidak diedit otomatis tanpa konfirmasi
    eksplisit dari user."""
    if not file_path:
        return False
    normalized = str(file_path).replace("\\", "/").lower()
    return any(pattern in normalized for pattern in SENSITIVE_PATTERNS)