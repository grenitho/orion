"""
test_prompts.py — regression guard for the system prompt (UPDATE-008)
=====================================================================
The LANGUAGE RULE is the contract behind "Orion replies in the language the
user writes in". Field reports showed occasional drift to English, so the
rule was strengthened (mandatory block at the top + final reminder at the
bottom). These tests pin the strengthened wording so future prompt edits
cannot silently drop it, and keep prompts.py pure static text.
"""

from __future__ import annotations

from app.services.prompts import SYSTEM_PROMPT


def test_language_rule_wajib_dan_rinci():
    """The strengthened block must survive future prompt edits."""
    assert "LANGUAGE RULE (MANDATORY, APPLIES TO EVERY SINGLE MESSAGE)" in SYSTEM_PROMPT
    assert "Detect the language of the user's LATEST message" in SYSTEM_PROMPT
    assert "Never switch language mid-reply" in SYSTEM_PROMPT
    assert "If the user switches language, switch with them" in SYSTEM_PROMPT


def test_pengingat_akhir_menunjuk_ke_aturan_bahasa():
    """Recency bias helper: the reminder sits at the very end of the prompt."""
    assert "FINAL REMINDER (UPDATE-008)" in SYSTEM_PROMPT
    assert (
        SYSTEM_PROMPT.index("FINAL REMINDER (UPDATE-008)")
        > SYSTEM_PROMPT.index("## GIT & VERSION CONTROL RULES")
    )


def test_prompt_statik_tanpa_placeholder():
    """prompts.py is pure text: no stray braces that hint at f-string/format
    placeholders (a single accidental brace would crash .format() users or
    silently swallow literal text)."""
    assert "{" not in SYSTEM_PROMPT
    assert "}" not in SYSTEM_PROMPT


def test_delegasi_proaktif_update_022():
    """UPDATE-022 (owner directive): the policy must demand INITIATIVE.

    Under the old 'Delegate ONLY when it pays' rule Orion never delegated
    once during the whole InvoiceFlow M1 trial — it behaved as if waiting
    for the user to ask. The proactive posture is now a tested contract,
    and the old restrictive wording must never silently return.
    """
    assert "Delegate PROACTIVELY" in SYSTEM_PROMPT
    assert "never wait for the user to ask" in SYSTEM_PROMPT
    assert "Delegate ONLY when it pays" not in SYSTEM_PROMPT
    # the three triggers, the budget framing and the user-override all stay
    for frag in (
        "EXPLORER", "VERIFIER", "WORKERS",
        "already protect the user's quota",
        "the user's instruction wins",
    ):
        assert frag in SYSTEM_PROMPT
