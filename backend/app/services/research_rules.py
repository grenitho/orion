"""
research_rules.py — aturan "riset otomatis saat mentok" (ORION-35)
==================================================================
Fungsi murni supaya bisa di-test tanpa jaringan.

Idenya: kalau sebuah tool gagal berulang, Orion dulu disuruh STOP dan lapor
user. Itu aman tapi membuang kesempatan belajar. Sekarang backend MELAKUKAN
pencarian web sendiri pada momen itu dan menyuntikkan temuannya, sehingga
"pengetahuan mentok" berubah menjadi "pengetahuan yang baru diambil".
"""

from __future__ import annotations

import re

AMBANG_GAGAL = 3
MAKS_CHAR_KUERI = 220

# potongan yang tidak berguna dalam kueri pencarian
_DERAU = re.compile(r"(traceback \(most recent call last\)|file \"[^\"]*\", line \d+|"
                    r"\^+|error:|warning:|\[orion[^\]]*\])", re.I)


def perlu_riset_otomatis(gagal_beruntun: int, ambang: int = AMBANG_GAGAL) -> bool:
    """True saat kegagalan beruntun sudah mencapai ambang riset."""
    return int(gagal_beruntun) >= int(ambang)


def rancang_kueri_riset(nama_tool: str, keluaran_error: str,
                        maks_char: int = MAKS_CHAR_KUERI) -> str:
    """Ubah error mentah jadi kueri pencarian yang masuk akal.

    Membersihkan derau (traceback, path file, caret), merapatkan spasi,
    dan membatasi panjang supaya mesin pencari tidak menerima tembakan acak.
    """
    teks = (keluaran_error or "").strip()
    if not teks:
        return f"{nama_tool} common errors and fixes"
    teks = _DERAU.sub(" ", teks)
    teks = re.sub(r"[ \t]+", " ", teks)
    teks = re.sub(r"\s*\n\s*", " ", teks).strip(" -:;|")
    if len(teks) > maks_char:
        teks = teks[:maks_char].rsplit(" ", 1)[0]
    return f"{nama_tool} error: {teks}" if teks else f"{nama_tool} common errors and fixes"
