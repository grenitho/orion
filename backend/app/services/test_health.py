"""
test_health.py — GET /health + the PID-file lifecycle (UPDATE-013)
==================================================================
The health endpoint is the extension's defence against talking to a stale
orphan or a foreign app on port 8000. These tests pin down:

  * /health is token-guarded like every route except GET / (the middleware
    in main.py must NOT gain another exemption);
  * the payload carries the identity fields backendManager.ts parses;
  * GET / and /health report the SAME version (one source of truth:
    runtime_id.BACKEND_VERSION);
  * the PID file appears while the app is running (lifespan startup) and
    disappears on shutdown — and only when it still belongs to us.
"""

from __future__ import annotations

import json
import os
import time

from fastapi.testclient import TestClient

from app import runtime_id
from app.services import auth


def _client_with_token(monkeypatch, tmp_path, token: str = "s3cret") -> TestClient:
    """TestClient whose lifespan writes the PID file into tmp_path, not the repo."""
    monkeypatch.setenv(auth.TOKEN_ENV, token)
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "orion_backend.pid"))
    from app.main import app

    return TestClient(app)


def test_health_butuh_token(monkeypatch, tmp_path):
    client = _client_with_token(monkeypatch, tmp_path)
    with client:
        # the bare probe stays open (monitoring / backendManager liveness)
        assert client.get("/").status_code == 200
        # ...but the fingerprint does not: without the token, no identity leak
        r = client.get("/health")
        assert r.status_code == 401
        assert "unauthorized" in r.json()["error"]


def test_health_payload_lengkap_dan_konsisten(monkeypatch, tmp_path):
    client = _client_with_token(monkeypatch, tmp_path)
    before = time.time()
    with client:
        r = client.get("/health", headers={"X-Orion-Token": "s3cret"})
        assert r.status_code == 200
        body = r.json()

        assert body["app"] == "orion-backend"
        assert body["version"] == runtime_id.BACKEND_VERSION
        assert body["pid"] == os.getpid()  # TestClient runs in-process
        assert isinstance(body["started_at"], float)
        assert 0 < body["started_at"] <= before + 1
        assert body["backend_path"].endswith("backend")
        assert body["project_path"] is None or isinstance(body["project_path"], str)

        # one source of truth: the bare probe reports the same version
        root_body = client.get("/").json()
        assert root_body["version"] == body["version"]


def test_pid_file_ditulis_saat_hidup_dan_dibersihkan_saat_mati(monkeypatch, tmp_path):
    client = _client_with_token(monkeypatch, tmp_path)
    pid_file = tmp_path / "orion_backend.pid"

    assert not pid_file.exists()
    with client:
        assert pid_file.exists(), "lifespan startup must announce the PID file"
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["version"] == runtime_id.BACKEND_VERSION
        assert isinstance(data["started_at"], float)
    assert not pid_file.exists(), "lifespan shutdown must remove our own PID file"


def test_pid_file_milik_proses_lain_tidak_dihapus(monkeypatch, tmp_path):
    """A newer process may have replaced the announcement; shutdown must not
    delete a PID file that no longer belongs to us."""
    client = _client_with_token(monkeypatch, tmp_path)
    pid_file = tmp_path / "orion_backend.pid"
    with client:
        # simulate another process taking over the file while we run
        pid_file.write_text(json.dumps({"pid": 999999, "started_at": 1.0}), encoding="utf-8")
    assert pid_file.exists(), "clear_pid_file must only remove OUR own file"
    assert json.loads(pid_file.read_text(encoding="utf-8"))["pid"] == 999999
