"""UPDATE-026 commit 35 — meta-monologue strip + attachment awareness.

Evidence (owner transcript 2026-11-02, turn "orion, pelajari file ini,
dan beritahu tentang apa?" with a text attachment):

1. The closing opened with a NEW monologue shape that escaped every
   existing stripper layer: "Let's follow the rule: 1. Answer the
   question … in your FIRST sentence …", "Let's craft the response …",
   "Let's double-check all requirements …". It carried no verb/tool
   token from the base lists, and the self-referential "your" bought
   the user-addressed escape.
2. The answer itself claimed "File … tidak ditemukan di dalam direktori
   proyek" while attachments.py had pasted the file's FULL contents
   into the user message (design: text attachments inline, cheap and
   useful across iterations) — the model went looking on disk instead
   of reading its own context.

Teeth (SYSTEM_PROMPT untouched, 3393/3400):

1. ``_META_JELAS``: exact self-referential meta phrases ("follow the
   rule", "craft the response", "first sentence", …) outrank the
   user-addressed escape inside the CLOSING predicate only; bullet
   leaders ("- First sentence: …") are unlabeled planning.
2. ``guard_rules.ada_blok_lampiran`` + a turn-start note (once per
   turn): when the user message carries the "ATTACHED FILE CONTENT"
   block, the context pins "the block IS the file — never answer
   'file tidak ada di disk'", with a 📎 status frame for the user.
"""

import pathlib

from app.services import guard_rules
from app.services import session_state
from app.services import stream_utils as su

_MONOLOG_META = (
    "Let's follow the rule:\n1. Answer the question that was actually "
    "asked in your FIRST sentence. A question gets an answer.\n2. When "
    "something the user asks about does not exist, say so plainly.\n"
    "3. Language: Indonesian.\n4. No completion claim words.\n\n"
    "Let's craft the response carefully in Indonesian:\n- First "
    "sentence: File `PROMPT_FRONTEND_INVOICEFLOW.md` tidak ada di "
    "direktori proyek.\n- Options: (1) tempel isinya, (2) cek path "
    "lain, (3) lanjut frontend.\n\n"
    "Let's double-check all requirements:\n- Language: Indonesian.\n"
    "- First sentence answers directly.\n- Brief, dense, direct.\n\n"
    "Isi file lampiran tersebut adalah spesifikasi tema terang dan "
    "gelap untuk frontend InvoiceFlow: palet warna, kontras subtab "
    "aktif, dan baris Total grand live."
)


def test_monolog_meta_terbuang_jawaban_tetap():
    bersih = su.bersih_closing(_MONOLOG_META, 0)
    assert "Let's follow the rule" not in bersih
    assert "Let's craft the response" not in bersih
    assert "Let's double-check all requirements" not in bersih
    assert bersih.startswith("Isi file lampiran tersebut adalah")


def test_prosa_user_asli_tidak_termakan_meta():
    teks = (
        "Your requirement is noted in the spec; here is the summary of "
        "what exists on disk today."
    )
    # tanpa frasa meta diri yang eksak, paragraf user-facing bertahan
    assert su.bersih_closing(teks, 0) == teks


def test_penolakan_jujur_unggul_atas_meta():
    tolak = (
        "Maaf, saya tidak bisa membacanya: follow the rule tidak "
        "berlaku karena tool baca tidak tersedia di konfigurasi Anda."
    )
    assert su.prose_perencanaan_closing(tolak) is False


def test_ada_blok_lampiran():
    pesan = (
        "orion, pelajari file ini, dan beritahu tentang apa?\n\n"
        "----- ATTACHED FILE CONTENTS: PROMPT_FRONTEND_INVOICEFLOW.md "
        "-----\n# PROMPT UNTUK ASISTEN CODING"
    )
    assert guard_rules.ada_blok_lampiran(pesan) is True
    assert guard_rules.ada_blok_lampiran("ubah warna tombol") is False
    assert guard_rules.ada_blok_lampiran("") is False


def test_state_lampiran_reset_per_turn():
    st = session_state.SessionState()
    st.lampiran_note_turn = 1
    st.reset_per_turn()
    assert st.lampiran_note_turn == 0


def test_wiring_note_lampiran():
    src = (pathlib.Path(su.__file__).parent / "agent_loop.py").read_text(
        encoding="utf-8")
    # commit 36 memindah sumber kebenaran ke state.lampiran_blok
    assert "state.lampiran_blok and not state.lampiran_note_turn:" in src
    assert "state.lampiran_note_turn" in src
    assert "SISTEM - LAMPIRAN" in src and "SYSTEM - ATTACHMENT" in src
