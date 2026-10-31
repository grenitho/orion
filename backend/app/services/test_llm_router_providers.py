"""
test_llm_router_providers.py — guards for the UPDATE-011 multi-provider tiers
=============================================================================
UPDATE-011 turned the LLM router from a 3-tier hardcoded pipeline into a
4-tier one whose second tier is a generic, first-class provider list read
from PROVIDER_<N>_{NAME,API_KEY,BASE_URL,MODELS,TEMPERATURE} env groups.

These tests pin the contract a .env author relies on:

  1. a group needs API_KEY + MODELS, anything else is skipped with a loud
     warning instead of crashing the backend at startup;
  2. model names without "/" become openai/... (every OpenAI-compatible
     endpoint), names with "/" go to LiteLLM verbatim (anthropic/...,
     deepseek/..., groq/...);
  3. priority is explicit and stable: Gemini keys first, then provider
     groups in numeric order (models in written order, NO shuffle), then
     Cloudflare, then the legacy FALLBACK_* tier (UPDATE-025 commit 18
     promoted Cloudflare above the legacy glm fallback so the
     Cloudflare accounts actually get traffic); ROUTER_FALLBACK
     (provider_<n> | cloudflare | none) moves one group to the very
     end, labelled "(Last Resort)"; GEMINI_DISABLED skips tier 1;
  4. per-group temperature is honoured, garbage falls back to the default;
  5. legacy-only configurations behave exactly like before UPDATE-011.

No network is touched: the queue builder is pure, and the one end-to-end
test swaps litellm.acompletion for a recorder.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.services import llm_router

_ENV_PREFIXES = ("PROVIDER_", "GEMINI_API_KEY_", "FALLBACK_", "CLOUDFLARE_",
               "GEMINI_DISABLED", "ROUTER_FALLBACK")


@pytest.fixture()
def env(monkeypatch):
    """A blank LLM slate: every router-relevant env var removed."""
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_provider_group_basic(env):
    env.setenv("PROVIDER_1_API_KEY", "sk-test")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat, deepseek-coder")

    queue = llm_router.build_execution_queue()

    assert [t["model"] for t in queue] == [
        "deepseek/deepseek-chat",
        "openai/deepseek-coder",  # no "/" -> openai/ prefix
    ]
    assert all(t["api_key"] == "sk-test" for t in queue)
    assert all(t["base_url"] is None for t in queue)
    assert all(t["provider"] == "Provider 1" for t in queue)
    assert all(t["temperature"] == 0.4 for t in queue)


def test_base_url_keeps_openai_compatible_prefix(env):
    env.setenv("PROVIDER_2_API_KEY", "sk-qwen")
    env.setenv(
        "PROVIDER_2_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )
    env.setenv("PROVIDER_2_MODELS", "qwen3-coder-plus")

    queue = llm_router.build_execution_queue()

    assert queue[0]["model"] == "openai/qwen3-coder-plus"
    assert queue[0]["base_url"] == (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    assert queue[0]["provider"] == "Provider 2"


def test_name_becomes_log_label(env):
    env.setenv("PROVIDER_1_NAME", "DeepSeek Produksi")
    env.setenv("PROVIDER_1_API_KEY", "sk-x")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat")

    queue = llm_router.build_execution_queue()

    assert queue[0]["provider"] == "DeepSeek Produksi (Provider 1)"


def test_incomplete_groups_are_skipped_with_warning(env, capsys):
    env.setenv("PROVIDER_1_API_KEY", "sk-only-key")  # no MODELS
    env.setenv("PROVIDER_2_MODELS", "gpt-5")  # no API_KEY

    with pytest.raises(Exception, match="No models configured"):
        llm_router.build_execution_queue()

    out = capsys.readouterr().out
    assert "missing PROVIDER_1_MODELS" in out
    assert "missing PROVIDER_2_API_KEY" in out


def test_unparsable_temperature_falls_back_to_default(env, capsys):
    env.setenv("PROVIDER_1_API_KEY", "sk-x")
    env.setenv("PROVIDER_1_MODELS", "gpt-5")
    env.setenv("PROVIDER_1_TEMPERATURE", "panas")

    queue = llm_router.build_execution_queue()

    assert queue[0]["temperature"] == 0.4
    assert "PROVIDER_1_TEMPERATURE" in capsys.readouterr().out


def test_temperature_is_per_group(env):
    env.setenv("PROVIDER_1_API_KEY", "sk-1")
    env.setenv("PROVIDER_1_MODELS", "gpt-5")
    env.setenv("PROVIDER_1_TEMPERATURE", "0.7")
    env.setenv("PROVIDER_2_API_KEY", "sk-2")
    env.setenv("PROVIDER_2_MODELS", "anthropic/claude-sonnet-4-5")

    queue = llm_router.build_execution_queue()

    assert queue[0]["temperature"] == 0.7
    assert queue[1]["temperature"] == 0.4  # untouched default


def test_groups_sorted_numerically_not_alphabetically(env):
    env.setenv("PROVIDER_10_API_KEY", "sk-10")
    env.setenv("PROVIDER_10_MODELS", "openai/gpt-5")
    env.setenv("PROVIDER_2_API_KEY", "sk-2")
    env.setenv("PROVIDER_2_MODELS", "openai/gpt-5-mini")

    queue = llm_router.build_execution_queue()

    assert [t["provider"] for t in queue] == ["Provider 2", "Provider 10"]


def test_tier_priority_gemini_providers_fallback_cloudflare(env):
    env.setenv("GEMINI_API_KEY_1", "AIza1")
    env.setenv("PROVIDER_1_NAME", "DeepSeek")
    env.setenv("PROVIDER_1_API_KEY", "sk-ds")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-reasoner")
    env.setenv("PROVIDER_2_NAME", "Claude")
    env.setenv("PROVIDER_2_API_KEY", "sk-ant")
    env.setenv("PROVIDER_2_MODELS", "anthropic/claude-sonnet-4-5")
    env.setenv("FALLBACK_API_KEY", "sk-or")
    env.setenv("FALLBACK_MODELS", "openai/gpt-5")
    env.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    env.setenv("CLOUDFLARE_API_TOKEN", "tok")
    env.setenv("CLOUDFLARE_MODELS", "@cf/llama")

    queue = llm_router.build_execution_queue()
    labels = [t["provider"] for t in queue]

    assert labels[0] == "Gemini Key 1 (Primary)"
    assert labels[1:3] == ["DeepSeek (Provider 1)", "Claude (Provider 2)"]
    # UPDATE-025 commit 18: Cloudflare promoted ABOVE the legacy tier
    assert labels[3] == "Cloudflare (Fallback 2)"
    assert labels[4] == "OpenRouter (Fallback 1)"


def test_legacy_fallback_only_unchanged(env):
    env.setenv("FALLBACK_API_KEY", "sk-or")
    env.setenv("FALLBACK_MODELS", "gpt-5, anthropic/claude-sonnet-4-5")

    queue = llm_router.build_execution_queue()

    assert {t["model"] for t in queue} == {
        "openai/gpt-5",
        "anthropic/claude-sonnet-4-5",
    }
    assert all(t["provider"] == "OpenRouter (Fallback 1)" for t in queue)
    assert all(t["base_url"] == "https://openrouter.ai/api/v1" for t in queue)


def test_empty_config_still_raises(env):
    with pytest.raises(Exception, match="No models configured"):
        llm_router.build_execution_queue()


def test_get_ai_response_routes_through_provider_queue(env, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "model": kwargs["model"]}

    monkeypatch.setattr(llm_router.litellm, "acompletion", fake_acompletion)
    env.setenv("PROVIDER_1_API_KEY", "sk-p1")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat")
    env.setenv("PROVIDER_1_TEMPERATURE", "0.2")

    response = asyncio.run(
        llm_router.get_ai_response([{"role": "user", "content": "halo"}])
    )

    assert response == {"ok": True, "model": "deepseek/deepseek-chat"}
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.2
    assert llm_router.litellm.api_key == "sk-p1"


# --------------------------------------- UPDATE-025 commit 18: router wizard


def test_gemini_disabled_melewati_tier_satu(env):
    env.setenv("GEMINI_API_KEY_1", "k1")
    env.setenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    env.setenv("GEMINI_DISABLED", "true")
    env.setenv("PROVIDER_1_NAME", "DeepSeek")
    env.setenv("PROVIDER_1_API_KEY", "sk-ds")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat")

    queue = llm_router.build_execution_queue()
    labels = [t["provider"] for t in queue]
    assert labels == ["DeepSeek (Provider 1)"]

    # checkbox off -> tier 1 returns, byte-identical contract
    env.setenv("GEMINI_DISABLED", "")
    queue = llm_router.build_execution_queue()
    assert queue[0]["provider"] == "Gemini Key 1 (Primary)"


def test_router_fallback_memindah_provider_ke_ekor(env):
    env.setenv("PROVIDER_1_NAME", "DeepSeek")
    env.setenv("PROVIDER_1_API_KEY", "sk-ds")
    env.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat")
    env.setenv("PROVIDER_2_NAME", "Claude")
    env.setenv("PROVIDER_2_API_KEY", "sk-ant")
    env.setenv("PROVIDER_2_MODELS", "anthropic/claude-sonnet-4-5")
    env.setenv("ROUTER_FALLBACK", "provider_1")

    queue = llm_router.build_execution_queue()
    labels = [t["provider"] for t in queue]
    assert labels == [
        "Claude (Provider 2)",
        "DeepSeek (Provider 1) (Last Resort)",
    ]


def test_router_fallback_cloudflare_ke_ekor(env):
    env.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    env.setenv("CLOUDFLARE_API_TOKEN", "tok")
    env.setenv("CLOUDFLARE_MODELS", "@cf/llama")
    env.setenv("FALLBACK_API_KEY", "sk-or")
    env.setenv("FALLBACK_MODELS", "openai/gpt-5")
    env.setenv("ROUTER_FALLBACK", "cloudflare")

    queue = llm_router.build_execution_queue()
    labels = [t["provider"] for t in queue]
    assert labels == [
        "OpenRouter (Fallback 1)",
        "Cloudflare (Fallback 2) (Last Resort)",
    ]


def test_router_fallback_tak_dikenal_atau_kosong_diiaskan(env):
    base = dict(
        PROVIDER_1_NAME="DeepSeek",
        PROVIDER_1_API_KEY="sk-ds",
        PROVIDER_1_MODELS="deepseek/deepseek-chat",
        CLOUDFLARE_ACCOUNT_ID="acc",
        CLOUDFLARE_API_TOKEN="tok",
        CLOUDFLARE_MODELS="@cf/llama",
    )
    for k, v in base.items():
        env.setenv(k, v)
    default_labels = [
        t["provider"] for t in llm_router.build_execution_queue()
    ]
    assert default_labels == [
        "DeepSeek (Provider 1)",
        "Cloudflare (Fallback 2)",
    ]
    # garbage, unknown group and "none" all degrade to the default order
    for junk in ("provider_9", "blah", "none", ""):
        env.setenv("ROUTER_FALLBACK", junk)
        labels = [t["provider"] for t in llm_router.build_execution_queue()]
        assert labels == default_labels, junk
