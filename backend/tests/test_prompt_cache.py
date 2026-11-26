"""
test_prompt_cache.py — unit tests untuk optimasi prompt caching Orion
"""

from __future__ import annotations

from app.services.prompt_cache import (
    ekstrak_token_cache,
    susun_pesan_cache_friendly,
    terapkan_cache_control_claude,
)


def test_susun_pesan_menjaga_prefix_history_stabil():
    sys_msg = {"role": "system", "content": "SYSTEM_PROMPT"}
    history = [
        {"role": "user", "content": "buat file a.py"},
        {"role": "assistant", "content": "selesai"},
    ]
    user_msg = {"role": "user", "content": "lanjut"}

    # Turn 1 vs Turn 2 dengan dynamic context yang berubah
    m1 = susun_pesan_cache_friendly(sys_msg, history, "todo: [1] open", user_msg)
    m2 = susun_pesan_cache_friendly(sys_msg, history, "todo: [1] done", user_msg)

    # 3 pesan pertama (system + history) tetap identik 100% -> cache hit!
    assert m1[:3] == m2[:3]
    assert m1[3]["content"].startswith("[DYNAMIC WORKSPACE CONTEXT]")
    assert m1[4] == user_msg


def test_cache_control_aktif_hanya_untuk_claude():
    msgs = [
        {"role": "system", "content": "Anda adalah Orion."},
        {"role": "user", "content": "Halo"},
    ]
    tools = [
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        {"type": "function", "function": {"name": "edit_file", "parameters": {}}},
    ]

    # Non-Claude tidak dimodifikasi
    m_ds, t_ds = terapkan_cache_control_claude("deepseek-v4-flash", msgs, tools)
    assert isinstance(m_ds[0]["content"], str)
    assert "cache_control" not in t_ds[-1]["function"]

    # Claude mendapatkan ephemeral cache_control pada system & tool terakhir
    m_cl, t_cl = terapkan_cache_control_claude("claude-haiku-5-5", msgs, tools)
    assert isinstance(m_cl[0]["content"], list)
    assert m_cl[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert t_cl[-1]["function"]["cache_control"] == {"type": "ephemeral"}


def test_ekstrak_token_cache_openai_dan_anthropic():
    # Format OpenAI / Aggregator
    u_oai = {
        "prompt_tokens": 15000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 12000},
    }
    res_oai = ekstrak_token_cache(u_oai)
    assert res_oai == {
        "uncached_input_tokens": 3000,
        "cached_tokens": 12000,
        "output_tokens": 200,
    }

    # Format Anthropic Native
    u_ant = {
        "input_tokens": 15000,
        "output_tokens": 200,
        "cache_read_input_tokens": 12000,
    }
    res_ant = ekstrak_token_cache(u_ant)
    assert res_ant["cached_tokens"] == 12000
    assert res_ant["uncached_input_tokens"] == 3000