"""UPDATE-026 commit 38 — opener-beats-marker, about:blank evidence
filter, sub-run soft limit.

Three defects from the owner's 2026-11-02 turn-uji and questions:

1. Monologue leak via the user-marker escape: the closing opened
   "Let's write a concise, direct, factual answer:" — the substring
   "answer:" is a user-address marker, so the paragraph escaped every
   stripper layer. Tooth: inside the CLOSING predicate, a paragraph
   OPENING with a self-referential planning opener plus a verb/tool
   token is monologue regardless of marker substrings (decline escape
   still outranks everything).
2. Blank-page evidence paid debts: browser_evaluate on about:blank
   (dev server down) returned rgba(0,0,0,0) and PAID the gaya and
   perintah_tool debts + printed a 📐 ledger of nothing. Tooth: an
   evaluate result containing "about:blank" is not evidence — no flag
   flip, no ledger append, no debt payment; the debt stays open until
   real values land or the model plainly declines.
3. Explorer died "capped, retries 0": capped = max_iter exhausted
   while holding tool_calls (the 3-part exploration task outran the
   10-iteration budget); retries only fire on cascade exceptions, so
   0 is correct and the pipeline "broke" by budget, not by crash.
   Tooth: one wrap-up notice at 80% of the sub-run budget (the main
   loop's soft-limit precedent), so a dying sub-run writes its final
   report instead of dying mid-chain.

SYSTEM_PROMPT untouched (3393/3400).
"""

import pathlib

from app.services import stream_utils as su

_BOCOR = (
    "Let's write a concise, direct, factual answer:\n"
    "`frontend/src/App.jsx` baris 35-38 mendefinisikan subtab dengan "
    "kelas DaisyUI `tabs tabs-boxed`.\n\n"
    "Fakta verifikasi: kode subtab ada di `frontend/src/App.jsx`."
)


def test_monolog_opener_mengalahkan_escape_marker():
    bersih = su.bersih_closing(_BOCOR, 0)
    assert "Let's write a concise" not in bersih
    assert bersih.startswith("Fakta verifikasi:")


def test_paragraf_user_asli_tetap_hidup():
    teks = (
        "Anda dapat memeriksa rekaman di log; here is the summary of "
        "the subtab styles."
    )
    assert su.bersih_closing(teks, 0) == teks


def test_penolakan_jujur_tetap_unggul():
    tolak = (
        "Maaf, saya tidak bisa membayar perintah ini: browser tidak "
        "terhubung, jadi getComputedStyle tidak dapat dijalankan."
    )
    assert su.prose_perencanaan_closing(tolak) is False


def test_wiring_about_blank_dan_soft_limit():
    loop = (pathlib.Path(su.__file__).parent / "agent_loop.py").read_text(
        encoding="utf-8")
    assert '"about:blank" not in str(result)' in loop
    sub = (pathlib.Path(su.__file__).parent / "subagent_manager.py")
    src = sub.read_text(encoding="utf-8")
    assert "iterations left:" in src
    assert "PREFIX_BUDGET" in src
