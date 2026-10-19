"""
guard_rules.py — aturan penjaga klaim & stream, dipisah agar BISA DI-TEST (ORION-34)
====================================================================================
Sebelum modul ini ada, dua mekanisme terpenting Orion tersembunyi di dalam
fungsi stream sepanjang 1.200 baris, sehingga tidak mungkin di-unit-test:

  1. pendeteksian "klaim selesai" dari teks jawaban model, dan
  2. keputusan apakah klaim itu harus DITANTANG karena tidak ada file berubah.

Keduanya sekarang fungsi murni: input teks/angka, output boolean. Tidak ada
import FastAPI, tidak ada state. Test-nya ada di test_guard_rules.py dan
memakai kalimat halusinasi SUNGGUHAN yang pernah dihasilkan Orion.
"""

from __future__ import annotations

# Kata/frasa yang menandai model MENGKLAIM pekerjaan selesai.
# Diperluas pada ORION-24 setelah terbukti daftar 8 kata melewatkan klaim
# berbahasa Indonesia seperti "sudah tervalidasi" dan "terimplementasi dengan baik".
KATA_KLAIM = (
    "selesai",
    "done",
    "completed",
    "finished",
    "sudah saya",
    "telah saya",
    "berhasil",
    "task selesai",
    "terimplementasi",
    "sudah terpasang",
    "telah terpasang",
    "sudah terintegrasi",
    "telah terintegrasi",
    "terhubung dengan baik",
    "berjalan dengan baik",
    "siap digunakan",
    "siap pakai",
    "tervalidasi",
    "sudah tervalidasi",
    "dengan sukses",
    "tanpa error",
    "bebas error",
    "tidak ada error",
)


def kata_klaim(teks: str) -> bool:
    """True bila teks jawaban mengandung klaim bahwa pekerjaan selesai."""
    if not teks:
        return False
    rendah = teks.lower()
    return any(k in rendah for k in KATA_KLAIM)


def klaim_perlu_ditantang(
    is_task_complete: bool,
    ada_file_berubah: bool,
    sudah_ditantang: int,
    maks_tantangan: int,
) -> bool:
    """Keputusan gerbang verifikasi (ORION-17/24).

    Klaim ditantang HANYA kalau: model mengklaim selesai, TIDAK ada file yang
    berubah pada giliran ini, dan jatah tantangan belum habis. Batas jatah
    ada supaya gerbang ini tidak bisa menjadi loop baru.
    """
    return (
        bool(is_task_complete)
        and not ada_file_berubah
        and sudah_ditantang < maks_tantangan
    )


def stream_perlu_done_susulan(sudah_mengirim_done: bool) -> bool:
    """Keputusan wrapper ORION-28: kirim 'done' hanya bila aliran berakhir tanpanya."""
    return not sudah_mengirim_done
