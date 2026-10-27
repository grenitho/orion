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


# ---------------------------------------------------------------- UPDATE-023

def test_arsitektur_tiga_lapis_update_023():
    """The prompt is a contract-shaped tool, not a junk drawer.

    UPDATE-023 rebuilt 22 intimidation-heavy sections (~6.1k tokens, 41 MUST,
    16 MANDATORY, 7 STRICTLY, and ZERO rules about answering the user) into
    three layers: conversation contract, non-negotiable floor, working style.
    These ratchets stop the old rot from creeping back one incident-rule at
    a time; the anchors pin the behaviours the owner's InvoiceFlow
    transcripts proved missing.
    """
    import re

    import tiktoken

    # --- anti-bloat ratchets (measured at rewrite: 2941 tok / 5 sections) ---
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(SYSTEM_PROMPT)) <= 3400, (
        "system prompt re-bloated past the UPDATE-023 budget — every new "
        "rule needs a deleted one, or belongs in code, not in prose"
    )
    assert len(re.findall(r"^## ", SYSTEM_PROMPT, re.M)) <= 7
    assert len(re.findall(r"\bMUST\b", SYSTEM_PROMPT)) <= 6
    assert "STRICTLY" not in SYSTEM_PROMPT
    # the old intimidation vocabulary and stale project residue stay out
    for gone in ("MOST CRITICAL", "STRICTLY MANDATORY", "TIE-BREAKER",
                 "bob-assistant"):
        assert gone not in SYSTEM_PROMPT

    # --- layer 1: the conversation contract (the layer that was missing) ---
    for anchor in (
        "CONVERSATION CONTRACT",
        "Answer the question that was actually asked",
        "2-3 concrete options",           # absence + options + recommendation
        "enumerates EVERY item",          # audits list per-item status
        "Keep internal machinery internal",  # no ritual dumps at the user
        "Translate the user's everyday language",  # vibe-coder clause (024)
        "I read this as:",  # interpretation stated, then acted on
    ):
        assert anchor in SYSTEM_PROMPT

    # --- layer 2: the honesty floor keeps its teeth ---
    for anchor in (
        "Never claim a scope wider than your evidence",
        "delegate the VERIFIER sub-agent and act on its verdict",
        "PORT 8000 IS OCCUPIED BY THE ORION BACKEND ITSELF",
        "AUTO SYNTAX CHECK",
        "run the parser, not your eyes",
        "Database loans (Supabase or similar via MCP) are borrowed, never owned",
        "believe the user",
        "first build the todo list from the remaining plan",
        "VERIFIED FACT",
    ):
        assert anchor in SYSTEM_PROMPT
