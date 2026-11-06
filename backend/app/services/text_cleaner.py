import re
import json
import tiktoken

_THINKING_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_token_encoder = tiktoken.get_encoding("cl100k_base")

def fix_over_escaped_text(text) -> str:
    if not isinstance(text, str):
        return text
    text = re.sub(r"\\+n", "\n", text)
    text = re.sub(r"\\+t", "\t", text)
    text = re.sub(r"\\+r", "\r", text)
    return text


def strip_thinking_blocks(text: str) -> str:
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