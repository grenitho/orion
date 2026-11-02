"""UPDATE-026 commit 36 — pure-request anchor (attachment split).

Evidence (owner transcript 2026-11-02, turn "periksa file yang ini."
with HANDOFF_ORION_UPDATE_025_CLOSED.md attached): the debt block
showed `[perintah_tool]` quoting a HANDOFF fragment ("Playwright MCP
config) — no fabrication, no mutation, debt block intact.") — the
order classifiers read the inline attachment block as if it were the
user's order, because attachments.py pastes the file INTO the same
user message and `permintaan_turn_ini` kept the whole blob. Debt
quotes and challenge quotes also dumped the attachment into the panel.

Tooth (SYSTEM_PROMPT untouched): guard_rules.pisahkan_lampiran splits
the message at the anchor point — `permintaan_turn_ini` keeps the
user's OWN words (every classifier, debt quote, challenge quote,
language detector and tool probe downstream becomes attachment-free);
the block rides `state.lampiran_blok` and still drives the commit-35
📎 note. Exams pasted verbatim into the message (no marker) are
untouched by design.
"""

import pathlib

from app.services import guard_rules
from app.services import session_state
from app.services import stream_utils as su

_PESAN = (
    "periksa file yang ini.\n\n"
    "----- ATTACHED FILE CONTENTS: HANDOFF_ORION_UPDATE_025_CLOSED.md "
    "-----\n# HANDOFF — ORION UPDATE-025 SERIES CLOSED\nExam 10 closed "
    "the series with an HONEST DECLINE (browser_take_screenshot "
    "unavailable in the owner's Playwright MCP config) — no "
    "fabrication, no mutation, debt block intact."
)


def test_pisahkan_lampiran():
    murni, blok = guard_rules.pisahkan_lampiran(_PESAN)
    assert murni == "periksa file yang ini."
    assert blok.startswith("----- ATTACHED FILE CONTENT")
    assert guard_rules.pisahkan_lampiran("tanpa lampiran") == (
        "tanpa lampiran", "")
    assert guard_rules.pisahkan_lampiran("") == ("", "")


def test_perintah_tool_hantu_hilang():
    murni, _ = guard_rules.pisahkan_lampiran(_PESAN)
    # bentuk bug: teks gabungan menambatkan perintah tool dari dokumen
    assert guard_rules.kalimat_perintah_tool(_PESAN)
    # sesudah pisah: permintaan murni tidak menyebut tool apa pun
    assert not guard_rules.kalimat_perintah_tool(murni)


def test_klaim_dan_gaya_tidak_terpancing_dokumen():
    murni, _ = guard_rules.pisahkan_lampiran(_PESAN)
    assert not guard_rules.kalimat_klaim_implementasi(murni)
    # blok lampiran tetap terdeteksi untuk note 📎 commit 35
    assert guard_rules.ada_blok_lampiran(_PESAN)


def test_state_lampiran_blok_reset():
    st = session_state.SessionState()
    st.lampiran_blok = "x"
    st.reset_per_turn()
    assert st.lampiran_blok == ""


def test_wiring_anchor_murni():
    src = (pathlib.Path(su.__file__).parent / "agent_loop.py").read_text(
        encoding="utf-8")
    assert "guard_rules.pisahkan_lampiran(" in src
    assert "state.lampiran_blok" in src
    assert "if state.lampiran_blok and not state.lampiran_note_turn:" in src
