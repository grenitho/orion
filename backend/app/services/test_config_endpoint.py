"""
test_config_endpoint.py — P7.1 Configuration Center endpoint
=============================================================
Guards the contract the ConfigCenterPanel will rely on:

  * GET /config returns project_path, config_path, orion_config,
    defaults, ranges and env_summary;
  * env_summary NEVER contains a key VALUE (only counts + names);
  * POST /config validates BEFORE touching the file (unknown keys,
    wrong types, out-of-range numbers all rejected, file untouched);
  * POST /config merges (not replaces) and live-syncs orion_config so
    the next /chat turn reads the saved values;
  * both routes sit behind the shared-secret middleware like every
    route except GET /.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.services import auth


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """TestClient dengan PID + last-project + project_path terisolasi.

    Hermeticity: TestClient menjalankan lifespan startup yang memanggil
    auto_restore_project(). Tanpa isolasi, file ~/.orion_last_project.json
    milik mesin pengembang akan men-set context_manager.project_path ke
    proyek nyata, dan test berikutnya akan melihat sisa itu.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "pid"))
    import app.main as main

    monkeypatch.setattr(main, "LAST_PROJECT_FILE", str(tmp_path / "last.json"))
    monkeypatch.setattr(main, "load_last_project", lambda: None)
    from app.main import context_manager as _cm

    monkeypatch.setattr(_cm, "project_path", None)
    with TestClient(main.app) as c:
        # Lifespan bisa saja men-set nilai; pastikan bersih SEBELUM yield.
        monkeypatch.setattr(_cm, "project_path", None)
        yield c


H = {"X-Orion-Token": "s3cret"}


def _set_project(monkeypatch, tmp_path):
    from app.main import context_manager

    monkeypatch.setattr(context_manager, "project_path", str(tmp_path))
    orion = tmp_path / ".orion"
    orion.mkdir(exist_ok=True)
    return orion


# ----------------------------------------------------------------- auth


def test_config_routes_butuh_token(client):
    assert client.get("/config").status_code == 401
    assert client.post("/config", json={"orion_config": {}}).status_code == 401


# ----------------------------------------------------------------- GET


def test_get_config_tanpa_proyek_tetap_200(client, monkeypatch):
    """GET must answer without a project (panel opened before Set Project)."""
    from app.main import context_manager

    monkeypatch.setattr(context_manager, "project_path", None)

    r = client.get("/config", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["project_path"] is None
    assert body["config_path"] is None
    assert body["orion_config"] == {}
    assert "max_iterations" in body["defaults"]
    assert body["ranges"]["max_iterations"] == [1, 200]
    assert body["ranges"]["inbox_max_files"] == [1, 20]


def test_get_config_env_summary_tanpa_nilai_rahasia(client, monkeypatch):
    # Isolasi: hapus semua var Gemini/provider yang mungkin sudah dimuat
    # dari .env oleh load_dotenv() di main.py, agar hitungan tidak
    # terkontaminasi nilai mesin pengembang.
    for name in list(os.environ):
        if name.startswith(
            (
                "GEMINI_API_KEY_",
                "PROVIDER_",
                "FALLBACK_",
                "CLOUDFLARE_",
                "GEMINI_DISABLED",
                "ROUTER_FALLBACK",
            )
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)

    monkeypatch.setenv("GEMINI_API_KEY_1", "AIza-rahasia-1")
    monkeypatch.setenv("GEMINI_API_KEY_2", "AIza-rahasia-2")
    monkeypatch.setenv("DEFAULT_MODEL", "gemini/gemini-3.5-flash")
    monkeypatch.setenv("PROVIDER_1_NAME", "DeepSeek")
    monkeypatch.setenv("PROVIDER_1_API_KEY", "sk-super-rahasia")
    monkeypatch.setenv("PROVIDER_1_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("PROVIDER_1_MODELS", "deepseek/deepseek-chat")
    monkeypatch.setenv("GEMINI_DISABLED", "true")
    monkeypatch.setenv("ROUTER_FALLBACK", "provider_1")

    r = client.get("/config", headers=H)
    body = r.json()
    es = body["env_summary"]
    assert es["gemini_keys_count"] == 2
    assert es["default_model"] == "gemini/gemini-3.5-flash"
    assert es["gemini_disabled"] is True
    assert es["router_fallback"] == "provider_1"
    assert es["providers"] == [
        {
            "n": 1,
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "models": ["deepseek/deepseek-chat"],
        }
    ]
    blob = r.text
    assert "AIza-rahasia" not in blob
    assert "sk-super-rahasia" not in blob


def test_get_config_menampilkan_isi_file_disk(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    (orion / "orion_config.json").write_text(
        json.dumps({"max_iterations": 42, "token_budget_threshold": 5000}),
        encoding="utf-8",
    )
    body = client.get("/config", headers=H).json()
    assert body["orion_config"]["max_iterations"] == 42
    assert body["orion_config"]["token_budget_threshold"] == 5000
    assert body["config_path"].endswith("orion_config.json")


# ----------------------------------------------------------------- POST


def test_post_config_tanpa_proyek_400(client, monkeypatch):
    from app.main import context_manager

    monkeypatch.setattr(context_manager, "project_path", None)
    r = client.post("/config", headers=H, json={"orion_config": {}})
    assert r.status_code == 400
    assert "No project" in r.json()["error"]


def test_post_config_payload_invalid_tidak_menyentuh_file(
    client, monkeypatch, tmp_path
):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text(json.dumps({"max_iterations": 20}), encoding="utf-8")
    before = cfg.read_bytes()

    cases = [
        ({"max_iterations": 0}, "between 1 and 200"),
        ({"max_iterations": 9999}, "between 1 and 200"),
        ({"max_iterations": "lima"}, "must be an integer"),
        ({"max_iterations": True}, "must be an integer"),
        ({"inbox_max_files": 99}, "between 1 and 20"),
        ({"subagents_enabled": "yes"}, "must be true or false"),
        ({"unknown_key": 1}, "unknown key"),
        (
            {"subagent_models": {"explorer": {"group": "cloudflare", "model": "x"}}},
            "group must be",
        ),
    ]
    for payload, frag in cases:
        r = client.post("/config", headers=H, json={"orion_config": payload})
        assert r.status_code == 400, (payload, r.text)
        assert frag in r.json()["error"], (payload, r.text)
    assert cfg.read_bytes() == before


def test_post_config_valid_menulis_merge_dan_live_sync(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text(
        json.dumps({"max_iterations": 20, "extra_custom_key": "keep me"}),
        encoding="utf-8",
    )

    import app.main as main

    main.orion_config["max_iterations"] = 20

    payload = {
        "max_iterations": 50,
        "token_budget_threshold": 30000,
        "subagents_enabled": False,
        "inbox_max_files": 10,
    }
    r = client.post("/config", headers=H, json={"orion_config": payload})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["orion_config"]["max_iterations"] == 50

    disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert disk["max_iterations"] == 50
    assert disk["token_budget_threshold"] == 30000
    assert disk["subagents_enabled"] is False
    assert disk["inbox_max_files"] == 10
    assert disk["extra_custom_key"] == "keep me"  # merged, not replaced
    assert not (orion / "orion_config.json.tmp").exists()

    assert main.orion_config["max_iterations"] == 50  # live


def test_post_config_file_korup_ditolak_utuh(client, monkeypatch, tmp_path):
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_text("{rusak json", encoding="utf-8")
    before = cfg.read_bytes()
    r = client.post("/config", headers=H, json={"orion_config": {"max_iterations": 30}})
    assert r.status_code == 400
    assert "unreadable" in r.json()["error"] or "corrupt" in r.json()["error"]
    assert cfg.read_bytes() == before


def test_post_config_bom_windows(client, monkeypatch, tmp_path):
    """Windows Notepad saves UTF-8 with a BOM; the merge must survive it."""
    orion = _set_project(monkeypatch, tmp_path)
    cfg = orion / "orion_config.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"max_iterations": 20}).encode())

    r = client.post("/config", headers=H, json={"orion_config": {"max_iterations": 30}})
    assert r.status_code == 200
    disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert disk["max_iterations"] == 30
