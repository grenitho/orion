"""UPDATE-026 commit 37 — pure-text re-anchor scan (phantom-debt killer).

Owner question 2026-11-02: "Kenapa 'Utang verifikasi masih terbuka'
itu SELALU ada?" Two mechanisms, one design and one bug:

* design: the debt ledger is session-scoped and re-shown until paid or
  plainly declined — disclosure over rejection;
* bug: the turn-start re-anchor scan (agent_loop, reversed history
  walk) classified the RAW latest user message — inline attachment
  block included — so EVERY attachment turn re-birthed phantom orders
  from the DOCUMENT's own sentences. The owner's informational turn
  with RELEASE_NOTES_BACKEND.md attached produced
  `[perintah_tool] "berkata "Playwright MCP"."` — a quote from the
  release notes describing the very bug. Commit 36 split only the
  first anchor path; this closes the second.

Tooth: the scan now classifies `pisahkan_lampiran(_teks)[0]` for both
perintah_verifikasi_gaya and zero_mutasi_diperintah. SYSTEM_PROMPT
untouched (3393/3400).
"""

import pathlib
import re as _re

from app.services import guard_rules
from app.services import stream_utils as su

_RAW = (
    "apa bisa anda jelaskan ini file apa?\n\n"
    "----- ATTACHED FILE CONTENTS: RELEASE_NOTES_BACKEND.md -----\n"
    "# RELEASE NOTES 0.10.40\nUtang hantu: transcript menyebut "
    "\"Playwright MCP\".\nVERIFIKASI WAJIB via Playwright MCP: baca "
    "getComputedStyle warna subtab aktif.\nDILARANG membuat atau "
    "mengubah file apa pun turn ini."
)


def test_classifier_dokumen_tidak_menambat_bila_dipisah():
    murni, blok = guard_rules.pisahkan_lampiran(_RAW)
    assert blok
    # bentuk bug: teks RAW menambatkan perintah gaya + zero-mutasi
    assert guard_rules.perintah_verifikasi_gaya(_RAW)[1] is True
    assert guard_rules.zero_mutasi_diperintah(_RAW)[0] is True
    # sesudah pisah: permintaan informasional murni tidak menambatkan
    assert guard_rules.perintah_verifikasi_gaya(murni)[1] is False
    assert guard_rules.zero_mutasi_diperintah(murni)[0] is False


def test_scan_reanchor_memakai_teks_murni():
    src = (pathlib.Path(su.__file__).parent / "agent_loop.py").read_text(
        encoding="utf-8")
    assert "guard_rules.pisahkan_lampiran(_teks)[0]" in src
    assert _re.search(
        r"perintah_verifikasi_gaya\(\s*_teks_murni\)", src)
    assert _re.search(
        r"zero_mutasi_diperintah\(\s*_teks_murni\)", src)
    # tidak ada lagi pemanggilan classifier atas teks RAW di scan itu
    assert not _re.search(
        r"perintah_verifikasi_gaya\(\s*_teks\)", src)
    assert not _re.search(
        r"zero_mutasi_diperintah\(\s*_teks\)", src)
