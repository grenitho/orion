"""
prompt_cache.py — Anthropic / Claude Prompt Caching helper for Orion
=====================================================================
Menyuntikkan penanda ``cache_control: {"type": "ephemeral"}`` pada
breakpoint statis (system prompt, definisi tools, dan ujung prefix
riwayat chat sebelum pesan user terbaru) khusus untuk model Claude,
serta mengekstrak jumlah token cache-read dari objek ``usage`` LiteLLM.
"""

from __future__ import annotations

import copy
from typing import Any


def adalah_model_claude(model_name: str | None) -> bool:
    """True jika nama model merujuk ke keluarga Anthropic Claude."""
    nama = (model_name or "").lower()
    return "claude" in nama or "anthropic" in nama


def _tandai_ephemeral_pada_pesan(msg: dict) -> dict:
    """Kembalikan salinan pesan dengan cache_control ephemeral di blok akhir."""
    salinan = dict(msg)
    content = salinan.get("content")

    if isinstance(content, str):
        if not content:
            return salinan
        salinan["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif isinstance(content, list) and content:
        blok_baru = [dict(b) if isinstance(b, dict) else b for b in content]
        for idx in range(len(blok_baru) - 1, -1, -1):
            if isinstance(blok_baru[idx], dict):
                blok_baru[idx]["cache_control"] = {"type": "ephemeral"}
                break
        salinan["content"] = blok_baru

    return salinan


def terapkan_cache_control_claude(
    model_name: str | None,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> tuple[list[dict], list[dict] | None]:
    """Terapkan breakpoint ephemeral prompt caching untuk model Claude.

    Untuk model non-Claude, mengembalikan objek asli apa adanya
    (``req_messages is messages``) agar perilaku provider lain tidak berubah.
    """
    if not adalah_model_claude(model_name):
        return messages, tools

    req_messages = [dict(m) for m in (messages or [])]

    # 1. Breakpoint pada system prompt (indeks 0 jika role == "system")
    for i, m in enumerate(req_messages):
        if m.get("role") == "system":
            req_messages[i] = _tandai_ephemeral_pada_pesan(m)
            break

    # 2. Breakpoint pada ujung prefix riwayat (pesan terakhir sebelum user terbaru)
    idx_user_terakhir = -1
    for i in range(len(req_messages) - 1, -1, -1):
        if req_messages[i].get("role") == "user":
            idx_user_terakhir = i
            break

    if idx_user_terakhir > 1:
        for i in range(idx_user_terakhir - 1, 0, -1):
            if req_messages[i].get("content"):
                req_messages[i] = _tandai_ephemeral_pada_pesan(req_messages[i])
                break

    # 3. Breakpoint pada definisi tool terakhir
    req_tools = tools
    if tools:
        req_tools = [copy.deepcopy(t) for t in tools]
        if isinstance(req_tools[-1], dict):
            if isinstance(req_tools[-1].get("function"), dict):
                req_tools[-1]["function"]["cache_control"] = {"type": "ephemeral"}
            else:
                req_tools[-1]["cache_control"] = {"type": "ephemeral"}

    return req_messages, req_tools


def ekstrak_cached_tokens(usage: Any) -> int:
    """Ambil jumlah token yang terbaca dari cache (Anthropic / LiteLLM format)."""
    if usage is None:
        return 0

    def _ambil(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    # 1. Field native Anthropic / LiteLLM: cache_read_input_tokens
    cache_read = _ambil(usage, "cache_read_input_tokens")
    if isinstance(cache_read, (int, float)) and cache_read > 0:
        return int(cache_read)

    # 2. Field normalized OpenAI/LiteLLM: prompt_tokens_details.cached_tokens
    details = _ambil(usage, "prompt_tokens_details")
    if details is not None:
        cached = _ambil(details, "cached_tokens")
        if isinstance(cached, (int, float)) and cached > 0:
            return int(cached)

    return 0


def ekstrak_token_cache(usage: Any) -> dict[str, int]:
    """Ekstrak rincian token uncached, cached, dan output untuk pengujian/telemetri."""
    if usage is None:
        return {"uncached_input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}

    def _ambil(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    prompt_tokens = _ambil(usage, "prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = _ambil(usage, "input_tokens")
    prompt_tokens = int(prompt_tokens or 0)

    output_tokens = _ambil(usage, "completion_tokens")
    if output_tokens is None:
        output_tokens = _ambil(usage, "output_tokens")
    output_tokens = int(output_tokens or 0)

    cached_tokens = ekstrak_cached_tokens(usage)

    return {
        "uncached_input_tokens": max(0, prompt_tokens - cached_tokens),
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
    }


def susun_pesan_cache_friendly(
    sys_msg: dict | str,
    history: list[dict] | None,
    dynamic_context: dict | str | None = None,
    user_msg: dict | str | None = None,
) -> list[dict]:
    """Susun payload pesan agar prefix system + riwayat tetap stabil untuk caching.

    Urutan: ``[sys_msg] + history + [dynamic_context] + [user_msg]``.
    """
    sys_obj = (
        {"role": "system", "content": sys_msg}
        if isinstance(sys_msg, str)
        else dict(sys_msg)
    )
    hasil: list[dict] = [sys_obj] + list(history or [])

    if dynamic_context:
        if isinstance(dynamic_context, dict):
            ctx_obj = dict(dynamic_context)
            isi = str(ctx_obj.get("content") or "")
            if not isi.startswith("[DYNAMIC WORKSPACE CONTEXT]"):
                ctx_obj["content"] = f"[DYNAMIC WORKSPACE CONTEXT]\n{isi}".strip()
            hasil.append(ctx_obj)
        else:
            teks = str(dynamic_context).strip()
            if teks:
                if not teks.startswith("[DYNAMIC WORKSPACE CONTEXT]"):
                    teks = f"[DYNAMIC WORKSPACE CONTEXT]\n{teks}"
                hasil.append({"role": "user", "content": teks})

    if user_msg is not None:
        user_obj = (
            {"role": "user", "content": user_msg}
            if isinstance(user_msg, str)
            else dict(user_msg)
        )
        hasil.append(user_obj)

    return hasil
