import re
import json

def fix_over_escaped_text(text) -> str:
    """Some models (especially the OpenRouter/Cloudflare fallback tiers)
    sometimes double-escape newlines when generating the JSON 'arguments'
    of a tool call containing multi-line text (e.g. the 'question' in
    ask_user) — writing '\\\\n' instead of '\\n' inside the JSON. After
    json.loads(), this shows up on the user's screen as the two-character
    literal '\\n' (not a real newline). This function normalizes that
    without breaking strings that were already correct.

    >>> FIX: the old version only did .replace("\\n", "\n") in a single
    pass. That is enough for 1 layer of over-escaping, but when the model
    over-escapes 2x or more (e.g. raw JSON containing many consecutive
    backslashes before 'n'), a single replace pass — or even repeated
    replaces — still leaves stray backslashes in front of the newline (e.g.
    it becomes '\\\\n' instead of a clean newline) because replace()
    matches exactly 1 backslash + 'n', not a RUN of backslashes + 'n'.
    It now uses the regex \\+n / \\+t / \\+r which sweeps the ENTIRE
    run of backslashes in front of n/t/r into a single real whitespace
    character, however many escape layers there are, in 1 pass — no
    leftover backslashes."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\\+n", "\n", text)
    text = re.sub(r"\\+t", "\t", text)
    text = re.sub(r"\\+r", "\r", text)
    return text


def strip_thinking_blocks(text: str) -> str:
    """Strip <thinking>...</thinking> blocks before showing text to the user in the frontend.
    The RAW version (with thinking) is still saved to context_manager for Orion's
    internal memory — only what is yielded to the stream gets cleaned."""
    if not text:
        return text
    return _THINKING_BLOCK_RE.sub("", text).strip()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_token_encoder.encode(text))
    except Exception:
        return len(text) // 3