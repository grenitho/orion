"""
test_subagent_profiles.py — per-role profile plumbing (UPDATE-018)
==================================================================
Complements test_subagent_manager.py: these tests pin ONLY the Feature-B
plumbing — orion_config ``subagent_models`` -> read_model_profiles ->
dispatch -> llm_call(profile=...). The router side of the contract lives
in test_llm_router_profiles.py.

Pinned rules:
  1. read_model_profiles keeps well-shaped {group, model} entries for the
     three owner-facing role keys (explorer / verifier / workers) and
     DROPS everything else (null, garbage types, unknown roles) — silently,
     never raising;
  2. dispatch passes the role's profile as a ``profile=`` kwarg on every
     LLM call of the sub-run; workers of one delegation share theirs;
  3. WITHOUT subagent_models the original llm_call passes through
     untouched (no extra kwargs) — bit-for-bit the pre-018 behaviour that
     the existing fakes in test_subagent_manager.py rely on.
"""

from __future__ import annotations

import asyncio

from app.services import subagent_manager as sm


# --------------------------------------------------------------------------
# read_model_profiles
# --------------------------------------------------------------------------

def test_read_model_profiles_validasi_dan_drop():
    config = {
        "subagent_models": {
            "explorer": {"group": " provider_1 ", "model": " mock-model "},
            "verifier": None,                        # explicit main router
            "workers": {"group": "gemini", "model": "gemini/gemini-3.5-flash"},
        }
    }
    # keyed by the INTERNAL role names dispatch() looks up ('worker' comes
    # from the owner-facing config key 'workers' — ROLE_PROFILE_KEYS).
    assert sm.read_model_profiles(config) == {
        "explorer": {"group": "provider_1", "model": "mock-model"},  # trimmed
        "worker": {"group": "gemini", "model": "gemini/gemini-3.5-flash"},
    }


def test_read_model_profiles_drop_entri_rusak_tanpa_raise():
    config = {
        "subagent_models": {
            "explorer": {"group": 42, "model": "x"},        # wrong type
            "verifier": {"group": "gemini"},                # missing model
            "workers": {"group": "", "model": "x"},         # empty group
            "hacker": {"group": "gemini", "model": "y"},    # unknown role
            "broken": "gemini",                             # not an object
        }
    }
    assert sm.read_model_profiles(config) == {}


def test_read_model_profiles_config_absen_atau_rusak():
    assert sm.read_model_profiles(None) == {}
    assert sm.read_model_profiles({}) == {}
    assert sm.read_model_profiles({"subagent_models": "gemini"}) == {}
    assert sm.read_model_profiles({"subagent_models": []}) == {}
    assert sm.read_model_profiles("bukan-dict") == {}


# --------------------------------------------------------------------------
# _llm_with_profile
# --------------------------------------------------------------------------

def test_llm_with_profile_transparan_bila_kosong():
    async def llm(messages, tools=None):
        return None

    assert sm._llm_with_profile(llm, None) is llm
    assert sm._llm_with_profile(llm, {}) is llm


def test_llm_with_profile_meneruskan_kwarg():
    terkirim = {}

    async def llm(messages, tools=None, **kwargs):
        terkirim.update(kwargs)
        return "resp"

    wrapped = sm._llm_with_profile(llm, {"group": "gemini", "model": "m"})
    hasil = asyncio.run(wrapped(messages=[{"role": "user", "content": "x"}], tools=[]))
    assert hasil == "resp"
    assert terkirim == {"profile": {"group": "gemini", "model": "m"}}


# --------------------------------------------------------------------------
# dispatch plumbing
# --------------------------------------------------------------------------

class _Pesan:
    content = (
        "Laporan sub-agent: kedua file seed ada di tempatnya; tidak ada "
        "temuan lain di proyek ini."
    )
    tool_calls = None


class _Choice:
    message = _Pesan()


class _Response:
    choices = [_Choice()]


class _State:
    subagent_calls = 0


def _fake_llm_perekam(rekam: list):
    async def llm(messages, tools=None, **kwargs):
        rekam.append(kwargs)
        return _Response()

    return llm


def test_dispatch_explorer_memakai_profil_peran():
    rekam = []
    hasil = asyncio.run(
        sm.dispatch(
            "delegate_explorer",
            {"task": "survei file seed"},
            state=_State(),
            llm_call=_fake_llm_perekam(rekam),
            tool_executor=None,
            config={
                "subagent_models": {
                    "explorer": {"group": "provider_1", "model": "mock-model"}
                }
            },
        )
    )
    assert "[SUBAGENT REPORT" in hasil
    assert rekam and all(
        r.get("profile") == {"group": "provider_1", "model": "mock-model"}
        for r in rekam
    )


def test_dispatch_verifier_null_berarti_router_utama():
    rekam = []
    asyncio.run(
        sm.dispatch(
            "delegate_verifier",
            {"task": "cek file seed"},
            state=_State(),
            llm_call=_fake_llm_perekam(rekam),
            tool_executor=None,
            config={"subagent_models": {"verifier": None}},
        )
    )
    assert rekam and all("profile" not in r for r in rekam)


def test_dispatch_workers_berbagi_satu_profil():
    rekam = []
    hasil = asyncio.run(
        sm.dispatch(
            "delegate_workers",
            {
                "tasks": [
                    {"task": "buat file pertama", "files": ["a.txt"]},
                    {"task": "buat file kedua", "files": ["b.txt"]},
                ]
            },
            state=_State(),
            llm_call=_fake_llm_perekam(rekam),
            tool_executor=None,
            config={
                "subagent_models": {
                    "workers": {"group": "gemini", "model": "gemini/gemini-3.5-flash"}
                }
            },
        )
    )
    assert "worker 1" in hasil and "worker 2" in hasil
    assert len(rekam) == 2  # both workers answered in one round each
    assert all(
        r.get("profile") == {"group": "gemini", "model": "gemini/gemini-3.5-flash"}
        for r in rekam
    )


def test_dispatch_tanpa_subagent_models_persis_pra_018():
    """No config key at all -> the fake llm_call must never see a profile
    kwarg (the existing test_subagent_manager fakes do not accept one)."""
    rekam = []

    async def llm_strict(messages, tools=None):
        rekam.append(True)
        return _Response()

    hasil = asyncio.run(
        sm.dispatch(
            "delegate_explorer",
            {"task": "survei"},
            state=_State(),
            llm_call=llm_strict,
            tool_executor=None,
            config={},
        )
    )
    assert "[SUBAGENT REPORT" in hasil and rekam
