"""
test_llm_router_profiles.py — guards for the UPDATE-018 sub-agent profiles
==========================================================================
Feature B pins one model per sub-agent role through
``get_ai_response(..., profile={"group","model"})``. These tests pin the
contract from HANDOFF_UPDATE_018 §8:

  1. a profile is a SINGLE-TARGET queue built from the credential group
     that ALREADY exists in the environment — no new secrets;
  2. group naming: ``gemini`` (rotates the group's N existing keys),
     ``provider_<N>`` (that group's key/base_url/temperature), ``fallback``
     (legacy FALLBACK_* endpoint); model names follow the same prefix rules
     as build_execution_queue ("/" verbatim, else the group's default
     prefix);
  3. a broken profile (malformed shape, unknown group, group without
     credentials) NEVER fails the call: get_ai_response logs a warning and
     falls back to the full main router;
  4. profile=None keeps the pre-018 behaviour bit-for-bit;
  5. model_options() exposes group ids, labels and model NAMES for the
     wizard — key VALUES must never appear.

No network: the queue builders are pure; the end-to-end test swaps
litellm.acompletion for a recorder (same technique as
test_llm_router_providers.py).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from app.services import llm_router

_ENV_PREFIXES = ("PROVIDER_", "GEMINI_API_KEY_", "FALLBACK_", "CLOUDFLARE_")


@pytest.fixture()
def env(monkeypatch):
    """A blank LLM slate: every router-relevant env var removed."""
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TEMPERATURE", raising=False)
    return monkeypatch


# --------------------------------------------------------------------------
# build_profile_queue: shape + group resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rusak", [
    None,
    "gemini",
    42,
    {},
    {"group": "gemini"},
    {"model": "x"},
    {"group": "", "model": "x"},
    {"group": "gemini", "model": ""},
    {"group": "  ", "model": "  "},
    {"group": "cloudflare", "model": "@cf/llama"},   # not a profile group
    {"group": "provider_0", "model": "x"},           # N starts at 1
    {"group": "provider_99999", "model": "x"},       # beyond the 2-digit cap
])
def test_profil_rusak_menghasilkan_none(env, rusak):
    assert llm_router.build_profile_queue(rusak) is None


def test_profil_gemini_merotasikan_key_yang_ada(env):
    env.setenv("GEMINI_API_KEY_1", "k1")
    env.setenv("GEMINI_API_KEY_3", "k3")
    queue = llm_router.build_profile_queue(
        {"group": "gemini", "model": "gemini-3.5-flash"}
    )
    assert queue is not None and len(queue) == 2
    assert all(t["model"] == "gemini/gemini-3.5-flash" for t in queue)
    assert {t["api_key"] for t in queue} == {"k1", "k3"}
    assert all(t["base_url"] is None for t in queue)
    assert all(t["temperature"] == 1.0 for t in queue)  # GEMINI_TEMPERATURE default


def test_profil_gemini_tanpa_key_menjadi_none(env):
    assert llm_router.build_profile_queue({"group": "gemini", "model": "x"}) is None


def test_profil_provider_memakai_kredensial_grup_yang_ada(env):
    env.setenv("PROVIDER_2_NAME", "DeepSeek")
    env.setenv("PROVIDER_2_API_KEY", "sk-ds")
    env.setenv("PROVIDER_2_BASE_URL", "https://api.deepseek.com/v1")
    env.setenv("PROVIDER_2_MODELS", "deepseek/deepseek-chat")
    env.setenv("PROVIDER_2_TEMPERATURE", "0.2")

    queue = llm_router.build_profile_queue(
        {"group": "provider_2", "model": "deepseek-coder"}
    )
    assert queue == [
        {
            "model": "openai/deepseek-coder",  # no "/" -> openai/ prefix
            "api_key": "sk-ds",
            "base_url": "https://api.deepseek.com/v1",
            "provider": "DeepSeek (Provider 2) (profile)",
            "temperature": 0.2,
        }
    ]

    verbatim = llm_router.build_profile_queue(
        {"group": "provider_2", "model": "deepseek/deepseek-reasoner"}
    )
    assert verbatim[0]["model"] == "deepseek/deepseek-reasoner"


def test_profil_provider_tanpa_kredensial_menjadi_none(env):
    env.setenv("PROVIDER_1_API_KEY", "sk-1")
    env.setenv("PROVIDER_1_MODELS", "m1")
    assert llm_router.build_profile_queue({"group": "provider_2", "model": "x"}) is None


def test_profil_fallback_ikut_aturan_prefix_tier3(env):
    env.setenv("FALLBACK_API_KEY", "sk-or")
    env.setenv("FALLBACK_MODELS", "gpt-5")

    queue = llm_router.build_profile_queue({"group": "fallback", "model": "gpt-5-mini"})
    assert queue[0]["model"] == "openai/gpt-5-mini"
    assert queue[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert queue[0]["temperature"] == 0.4

    verbatim = llm_router.build_profile_queue(
        {"group": "fallback", "model": "anthropic/claude-sonnet-4-5"}
    )
    assert verbatim[0]["model"] == "anthropic/claude-sonnet-4-5"

    env.delenv("FALLBACK_API_KEY")
    assert llm_router.build_profile_queue({"group": "fallback", "model": "x"}) is None


def test_profil_tidak_memasukkan_rahasia_baru(env):
    """A profile only re-points at EXISTING credentials: whatever it
    resolves to, the api_key must equal one already configured in env."""
    env.setenv("PROVIDER_1_API_KEY", "sk-1")
    env.setenv("PROVIDER_1_MODELS", "m1")
    env.setenv("GEMINI_API_KEY_1", "g1")
    queue = llm_router.build_profile_queue({"group": "provider_1", "model": "bebas"})
    assert queue[0]["api_key"] == os.environ["PROVIDER_1_API_KEY"]


# --------------------------------------------------------------------------
# model_options: wizard surface, env privacy
# --------------------------------------------------------------------------

def test_model_options_cerminkan_env_tanpa_nilai_kunci(env):
    env.setenv("GEMINI_API_KEY_1", "AIza-rahasia-1")
    env.setenv("GEMINI_API_KEY_2", "AIza-rahasia-2")
    env.setenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    env.setenv("PROVIDER_1_NAME", "MockLLM")
    env.setenv("PROVIDER_1_API_KEY", "sk-rahasia")
    env.setenv("PROVIDER_1_MODELS", "mock-model, other/model")
    env.setenv("FALLBACK_API_KEY", "sk-or-rahasia")
    env.setenv("FALLBACK_MODELS", "gpt-5")

    options = llm_router.model_options()
    groups = {o["group"]: o for o in options}

    assert set(groups) == {"gemini", "provider_1", "fallback"}
    assert groups["gemini"]["models"] == ["gemini/gemini-3.5-flash"]
    assert "2 key(s)" in groups["gemini"]["label"]
    assert groups["provider_1"]["models"] == ["openai/mock-model", "other/model"]
    assert groups["fallback"]["models"] == ["gpt-5"]

    blob = json.dumps(options)
    for rahasia in ("AIza-rahasia-1", "AIza-rahasia-2", "sk-rahasia", "sk-or-rahasia"):
        assert rahasia not in blob


def test_model_options_kosong_bila_env_kosong(env):
    assert llm_router.model_options() == []


# --------------------------------------------------------------------------
# get_ai_response(profile=...): routing + fallback
# --------------------------------------------------------------------------

def _rekam_acompletion(monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "model": kwargs["model"]}

    monkeypatch.setattr(llm_router.litellm, "acompletion", fake_acompletion)
    return calls


def test_get_ai_response_dengan_profil_valid_hanya_satu_target(env, monkeypatch):
    calls = _rekam_acompletion(monkeypatch)
    env.setenv("PROVIDER_1_API_KEY", "sk-p1")
    env.setenv("PROVIDER_1_MODELS", "main/model")       # main-router model
    env.setenv("PROVIDER_2_API_KEY", "sk-p2")
    env.setenv("PROVIDER_2_MODELS", "pinned/model")

    response = asyncio.run(
        llm_router.get_ai_response(
            [{"role": "user", "content": "halo"}],
            profile={"group": "provider_2", "model": "pinned/model"},
        )
    )
    assert response == {"ok": True, "model": "pinned/model"}
    assert len(calls) == 1                      # single target, no main queue
    assert calls[0]["model"] == "pinned/model"
    assert llm_router.litellm.api_key == "sk-p2"


def test_get_ai_response_profil_rusak_jatuh_ke_router_utama(env, monkeypatch):
    calls = _rekam_acompletion(monkeypatch)
    env.setenv("PROVIDER_1_API_KEY", "sk-p1")
    env.setenv("PROVIDER_1_MODELS", "main/model")

    response = asyncio.run(
        llm_router.get_ai_response(
            [{"role": "user", "content": "halo"}],
            profile={"group": "provider_9", "model": "x"},  # no credentials
        )
    )
    assert response == {"ok": True, "model": "main/model"}  # main router answered
    assert [c["model"] for c in calls] == ["main/model"]


def test_get_ai_response_tanpa_profil_tidak_berubah(env, monkeypatch):
    calls = _rekam_acompletion(monkeypatch)
    env.setenv("PROVIDER_1_API_KEY", "sk-p1")
    env.setenv("PROVIDER_1_MODELS", "main/model")

    response = asyncio.run(
        llm_router.get_ai_response([{"role": "user", "content": "halo"}])
    )
    assert response == {"ok": True, "model": "main/model"}
    assert len(calls) == 1


def test_get_ai_response_profil_gemini_mengirim_reasoning_effort(env, monkeypatch):
    """Gemini targets keep the PATCH ORION-6 kwargs even via a profile."""
    calls = _rekam_acompletion(monkeypatch)
    env.setenv("GEMINI_API_KEY_1", "g1")

    asyncio.run(
        llm_router.get_ai_response(
            [{"role": "user", "content": "halo"}],
            profile={"group": "gemini", "model": "gemini/gemini-3.5-flash"},
        )
    )
    assert calls[0]["model"] == "gemini/gemini-3.5-flash"
    assert calls[0]["reasoning_effort"] == llm_router.REASONING_EFFORT
    assert "temperature" not in calls[0]  # not sent unless LLM_SEND_TEMPERATURE
