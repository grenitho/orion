"""
test_models_endpoints.py — the /models/* surface (UPDATE-018)
=============================================================
Route-level guards for the three token-protected endpoints the setup
wizard's "Sub-agent models" card talks to:

  * GET  /models/options — groups+models derived from the backend env and
    the profiles saved on disk; key VALUES never appear in the body
    (security posture §6: env privacy, same rule as /mcp/status);
  * POST /models/test    — a real probe through the profile path; a group
    without credentials fails honestly; provider errors are sanitized of
    credential values and never become a 500;
  * POST /models/config  — validate -> atomic write -> live sync; invalid
    payloads are rejected BEFORE the file is touched; a corrupt config is
    never overwritten; other keys (mcp_enabled, max_iterations, ...) are
    preserved; without a project the route refuses with 400.

Hermeticity follows the test_health pattern: TestClient with the token in
env and the PID file redirected into tmp_path (conftest additionally keeps
MCP from spawning anything).
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.services import auth

_ENV_PREFIXES = ("PROVIDER_", "GEMINI_API_KEY_", "FALLBACK_", "CLOUDFLARE_")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient whose lifespan writes the PID file into tmp_path.

    Also fully hermetic against the developer machine: load_last_project is
    stubbed so the lifespan's auto_restore_project cannot pick up a real
    project from ~/.orion_last_project.json, and project_path starts None.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "orion_backend.pid"))
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    import app.main as main

    monkeypatch.setattr(main, "load_last_project", lambda: None)
    monkeypatch.setattr(main.context_manager, "project_path", None)

    with TestClient(main.app) as c:
        yield c


H = {"X-Orion-Token": "s3cret"}


def _set_project(monkeypatch, tmp_path):
    """Point the live context_manager at a temp project (auto-restored)."""
    from app.main import context_manager

    monkeypatch.setattr(context_manager, "project_path", str(tmp_path))
    orion = tmp_path / ".orion"
    orion.mkdir(exist_ok=True)
    return orion


# --------------------------------------------------------------------------
# auth + options
# --------------------------------------------------------------------------

def test_semua_route_models_butuh_token(client):
    assert client.get("/models/options").status_code == 401
    assert client.post("/models/test", json={"group": "gemini", "model": "m"}).status_code == 401
    assert client.post("/models/config", json={}).status_code == 401


def test_options_bentuk_dan_privasi_env(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_1", "AIza-rahasia")
    monkeypatch.setenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    monkeypatch.setenv("PROVIDER_1_NAME", "MockLLM")
    monkeypatch.setenv("PROVIDER_1_API_KEY", "sk-rahasia")
    monkeypatch.setenv("PROVIDER_1_MODELS", "mock-model")

    r = client.get("/models/options", headers=H)
    assert r.status_code == 200
    body = r.json()

    assert body["roles"] == ["explorer", "verifier", "workers"]
    groups = {g["group"]: g for g in body["groups"]}
    assert set(groups) == {"gemini", "provider_1"}
    assert groups["gemini"]["models"] == ["gemini/gemini-3.5-flash"]
    assert groups["provider_1"]["models"] == ["openai/mock-model"]
    assert body["subagent_models"] == {}  # no project set -> nothing saved

    blob = r.text
    assert "AIza-rahasia" not in blob and "sk-rahasia" not in blob


def test_options_membaca_profil_tersimpan_dari_disk(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    (orion / "orion_config.json").write_text(
        json.dumps(
            {
                "mcp_enabled": True,
                "subagent_models": {
                    "explorer": {"group": "gemini", "model": "gemini/gemini-3.5-flash"}
                },
            }
        ),
        encoding="utf-8",
    )
    body = client.get("/models/options", headers=H).json()
    assert body["subagent_models"] == {
        "explorer": {"group": "gemini", "model": "gemini/gemini-3.5-flash"}
    }


def test_options_config_rusak_tidak_menjatuhkan_route(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    (orion / "orion_config.json").write_text("{rusak", encoding="utf-8")
    r = client.get("/models/options", headers=H)
    assert r.status_code == 200
    assert r.json()["subagent_models"] == {}


# --------------------------------------------------------------------------
# POST /models/test
# --------------------------------------------------------------------------

def test_test_grup_tanpa_kredensial_jujur_tanpa_500(client):
    r = client.post("/models/test", headers=H, json={"group": "provider_7", "model": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "credentials" in body["error"]


def test_test_profil_kosong_ditolak_halus(client):
    r = client.post("/models/test", headers=H, json={"group": "", "model": ""})
    assert r.status_code == 200 and r.json()["success"] is False


def test_test_sukses_lewat_jalur_profil(client, monkeypatch):
    monkeypatch.setenv("PROVIDER_1_API_KEY", "sk-p1")
    monkeypatch.setenv("PROVIDER_1_MODELS", "mock-model")

    terkirim = {}

    async def fake_get_ai_response(messages, tools=None, profile=None):
        terkirim.update(messages=messages, tools=tools, profile=profile)

        class M:
            content = "pong"

        class C:
            message = M()

        class R:
            choices = [C()]

        return R()

    import app.main as main

    monkeypatch.setattr(main, "get_ai_response", fake_get_ai_response)

    r = client.post(
        "/models/test", headers=H, json={"group": "provider_1", "model": "mock-model"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["resolved_model"] == "openai/mock-model"
    assert body["reply_preview"] == "pong"
    assert terkirim["profile"] == {"group": "provider_1", "model": "mock-model"}
    assert terkirim["tools"] is None


def test_test_gagal_llm_tidak_membocorkan_kunci(client, monkeypatch):
    monkeypatch.setenv("PROVIDER_1_API_KEY", "sk-super-rahasia")
    monkeypatch.setenv("PROVIDER_1_MODELS", "mock-model")

    async def boom(messages, tools=None, profile=None):
        raise Exception(
            "AuthenticationError: invalid key sk-super-rahasia for provider "
            + "x" * 400
        )

    import app.main as main

    monkeypatch.setattr(main, "get_ai_response", boom)

    r = client.post(
        "/models/test", headers=H, json={"group": "provider_1", "model": "mock-model"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "sk-super-rahasia" not in body["error"]
    assert "***" in body["error"]
    assert len(body["error"]) <= 300


# --------------------------------------------------------------------------
# POST /models/config
# --------------------------------------------------------------------------

def test_config_tanpa_proyek_400(client, monkeypatch):
    from app.main import context_manager

    monkeypatch.setattr(context_manager, "project_path", None)
    r = client.post("/models/config", headers=H, json={})
    assert r.status_code == 400
    assert "No project" in r.json()["error"]


def test_config_payload_invalid_ditolak_sebelum_file_disentuh(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text(json.dumps({"mcp_enabled": True}), encoding="utf-8")
    sebelum = cfg.read_bytes()

    for payload, fragmen in [
        ({"role_asing": None}, "unknown role"),
        ({"explorer": {"group": "cloudflare", "model": "x"}}, "group must be"),
        ({"explorer": {"group": "provider_1", "model": ""}}, "non-empty"),
        ({"explorer": {"group": "provider_1", "model": "m", "api_key": "x"}}, "unexpected field"),
        ({"explorer": "gemini"}, "null or an object"),
    ]:
        r = client.post("/models/config", headers=H, json={"subagent_models": payload})
        assert r.status_code == 400, payload
        assert fragmen in r.json()["error"], payload
    assert cfg.read_bytes() == sebelum  # never touched


def test_config_valid_tulis_atomik_merge_dan_live_sync(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text(
        json.dumps({"mcp_enabled": True, "max_iterations": 7, "subagent_models": {}}),
        encoding="utf-8",
    )

    import app.main as main

    monkeypatch.setitem(main.orion_config, "subagent_models", {})

    payload = {
        "explorer": {"group": "gemini", "model": "gemini/gemini-3.5-flash"},
        "verifier": None,
        "workers": {"group": "provider_1", "model": "mock-model"},
    }
    r = client.post("/models/config", headers=H, json={"subagent_models": payload})
    assert r.status_code == 200 and r.json()["success"] is True

    disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert disk["subagent_models"] == payload
    assert disk["mcp_enabled"] is True and disk["max_iterations"] == 7  # preserved
    assert not (orion / "orion_config.json.tmp").exists()  # atomic rename done

    assert main.orion_config["subagent_models"] == payload  # live, no restart

    # reset: an empty body clears every profile back to the main router
    r = client.post("/models/config", headers=H, json={})
    assert r.status_code == 200
    assert json.loads(cfg.read_text(encoding="utf-8"))["subagent_models"] == {}


def test_config_file_korup_tidak_pernah_ditimpa(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text("{rusak json", encoding="utf-8")
    sebelum = cfg.read_bytes()

    r = client.post(
        "/models/config",
        headers=H,
        json={"subagent_models": {"explorer": None}},
    )
    assert r.status_code == 400
    assert "unreadable" in r.json()["error"] or "corrupt" in r.json()["error"]
    assert cfg.read_bytes() == sebelum


def test_config_bom_windows_tetap_terbaca(client, monkeypatch, tmp_path):
    """Windows Notepad saves UTF-8 with a BOM; the merge must survive it
    (same quirk as mcp.json — test_mcp_manager pins that one)."""
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcp_enabled": True}).encode())

    r = client.post(
        "/models/config",
        headers=H,
        json={"subagent_models": {"workers": {"group": "fallback", "model": "gpt-5"}}},
    )
    assert r.status_code == 200
    disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert disk["mcp_enabled"] is True
    assert disk["subagent_models"]["workers"]["group"] == "fallback"
