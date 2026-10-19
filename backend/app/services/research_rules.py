"""
research_rules.py — the "automatic research when stuck" rules (ORION-35)
=========================================================================
Pure functions so they can be tested without a network.

The idea: when a tool failed repeatedly, Orion used to be told to STOP and
report to the user. That is safe but throws away a learning opportunity.
Now the backend performs the web search ITSELF at that moment and injects
the findings, turning "stuck knowledge" into "freshly fetched knowledge".
"""

from __future__ import annotations

import re

AMBANG_GAGAL = 3
MAKS_CHAR_KUERI = 220

# fragments that are useless inside a search query
_DERAU = re.compile(r"(traceback \(most recent call last\)|file \"[^\"]*\", line \d+|"
                    r"\^+|error:|warning:|\[orion[^\]]*\])", re.I)


def perlu_riset_otomatis(gagal_beruntun: int, ambang: int = AMBANG_GAGAL) -> bool:
    """True once consecutive failures reach the research threshold."""
    return int(gagal_beruntun) >= int(ambang)


def rancang_kueri_riset(nama_tool: str, keluaran_error: str,
                        maks_char: int = MAKS_CHAR_KUERI) -> str:
    """Turn a raw error into a sensible search query.

    Strips noise (tracebacks, file paths, carets), collapses whitespace, and
    caps the length so the search engine does not receive random shotgun text.
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
