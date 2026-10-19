"""
html_checker.py — pemeriksaan struktur HTML untuk ORION (PATCH ORION-25)
========================================================================
Dipisah dari tool_executor supaya pemeriksa HTML bisa dipakai bersama oleh
tool `verify_html` (lama) dan `verify_code` (baru) tanpa duplikasi logika.
"""

from __future__ import annotations

import re

from app.services import code_verifier as cv


def cek_html(teks: str) -> list[str]:
    """Kembalikan daftar temuan untuk satu dokumen HTML."""
    temuan: list[str] = []

    # ---- 1. sintaks JavaScript di dalam <script> ----
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", teks, re.S | re.I):
        awal = teks[:m.start()].count("\n") + 1
        kode = m.group(1)
        if not kode.strip():
            continue
        for t in cv.cek_js("inline.js", kode):
            # geser nomor baris dari "dalam script" menjadi "dalam file HTML"
            mm = re.match(r"JAVASCRIPT SYNTAX line (\d+): (.*)", t)
            if mm:
                baris_html = awal + int(mm.group(1)) - 1
                temuan.append(f"JS SYNTAX line {baris_html}: {mm.group(2)}")
                temuan.append(f"     -> {cv._kutipan(teks, baris_html)}")
            elif t.startswith("     ->"):
                continue
            else:
                temuan.append(t)

    # ---- 2. id duplikat ----
    ids = re.findall(r"""\sid=["']([^"']+)["']""", teks)
    dup = sorted({k for k in ids if ids.count(k) > 1})
    if dup:
        temuan.append("DUPLICATE IDs (getElementById only picks the first one, "
                      f"the rest are unreachable): {', '.join(dup[:10])}")

    # ---- 3. referensi DOM tanpa pasangan ----
    set_ids = set(ids)
    refs = set(re.findall(r"""getElementById\(["']([^"']+)["']\)""", teks))
    refs |= set(re.findall(r"""querySelector(?:All)?\(["']#([A-Za-z0-9_\-]+)["']\)""", teks))
    hilang = sorted(r for r in refs if r not in set_ids)
    if hilang:
        temuan.append("REFERENCED ELEMENTS MISSING FROM HTML: " + ", ".join(hilang[:10]))

    # ---- 4. tag pembuka tanpa penutup untuk tag struktural ----
    for tag in ("div", "section", "main", "body", "html", "script", "style", "table"):
        buka = len(re.findall(rf"<{tag}\b(?![^>]*\/>)", teks, re.I))
        tutup = len(re.findall(rf"</{tag}\s*>", teks, re.I))
        if buka != tutup:
            temuan.append(f"UNBALANCED <{tag}> TAGS: {buka} opening vs {tutup} closing")

    if temuan:
        temuan.append("NOTE: a JS syntax error kills the ENTIRE <script> block; "
                      "as long as it exists, none of the page's functions run.")
    return temuan
