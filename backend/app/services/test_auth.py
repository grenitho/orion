"""
test_auth.py — the shared-secret token guard (UPDATE-003)
==========================================================
Covers token resolution (env override, file mint/re-read, empty file),
the constant-time header comparison, and the HTTP middleware in main.py:
health probe open, everything else 401 without the token, 200 with it.
"""

from __future__ import annotations

import pytest

from app.services import auth


def test_token_dari_env_menang_atas_file(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, "env-token")
    monkeypatch.setenv(auth.TOKEN_FILE_ENV, str(tmp_path / "unused"))
    assert auth.get_shared_token() == "env-token"
    assert not (tmp_path / "unused").exists()


def test_token_dibuat_sekali_lalu_stabil(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    f = tmp_path / "secret"
    monkeypatch.setenv(auth.TOKEN_FILE_ENV, str(f))
    first = auth.get_shared_token()
    assert len(first) == 64  # token_hex(32)
    assert f.read_text(encoding="utf-8").strip() == first
    assert auth.get_shared_token() == first


def test_file_token_kosong_diisi_baru(tmp_path, monkeypatch):
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    f = tmp_path / "secret"
    f.write_text("\n", encoding="utf-8")
    monkeypatch.setenv(auth.TOKEN_FILE_ENV, str(f))
    tok = auth.get_shared_token()
    assert tok.strip()
    assert f.read_text(encoding="utf-8").strip() == tok


def test_header_salah_atau_kosong_ditolak(monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    assert auth.request_is_authorized({auth.HEADER: "s3cret"}) is True
    assert auth.request_is_authorized({auth.HEADER: "s3creT"}) is False
    assert auth.request_is_authorized({auth.HEADER: ""}) is False
    assert auth.request_is_authorized({}) is False


def test_middleware_membuka_health_dan_menutup_sisanya(monkeypatch):
    """End-to-end through the real FastAPI app: GET / stays open (backend
    manager and monitoring probe it), everything else demands the token."""
    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/").status_code == 200

        r = client.get("/performance")
        assert r.status_code == 401
        assert "unauthorized" in r.json()["error"]

        r = client.post("/chat", json={"message": "hai"})
        assert r.status_code == 401

        r = client.get("/performance", headers={"X-Orion-Token": "s3cret"})
        assert r.status_code == 200

        r = client.get("/performance", headers={"X-Orion-Token": "salah"})
        assert r.status_code == 401


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
