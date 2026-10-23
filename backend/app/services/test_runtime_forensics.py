"""
test_runtime_forensics.py — guards for the UPDATE-019 twin-process diagnosis
============================================================================
Incident 013 ("two backends alive at once") was undiagnosable from the
outside: the PID file, the port-8000 listener and the code mtime can each
disagree, and nothing exposed all three. UPDATE-019 adds
``runtime_id.pid_alive`` / ``read_pid_file`` / ``forensics_payload`` plus
the token-guarded ``GET /runtime/forensics`` route and a startup log line
that reads the OLD PID claim before overwriting it.

Pinned here:
  * liveness probing (own pid alive, dead pid dead, garbage safe);
  * read_pid_file never raises (missing / corrupt / non-object JSON);
  * forensics verdicts: clean install, own claim, TWIN SUSPECTED (another
    LIVE pid claims the file), STALE PID FILE (dead pid), PID FILE
    UNREADABLE, STALE CODE (booted before main.py mtime);
  * the route sits behind the shared-secret token like every route except
    GET / (security posture §6).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import runtime_id
from app.services import auth


# --------------------------------------------------------------------------
# pid_alive
# --------------------------------------------------------------------------

def test_pid_alive_diri_sendiri():
    assert runtime_id.pid_alive(os.getpid()) is True


def test_pid_alive_proses_mati():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    assert runtime_id.pid_alive(proc.pid) is False


@pytest.mark.parametrize("gabungan", [None, 0, -5, "123", True, 3.7])
def test_pid_alive_input_sampah_aman(gabungan):
    assert runtime_id.pid_alive(gabungan) is False


# --------------------------------------------------------------------------
# read_pid_file
# --------------------------------------------------------------------------

@pytest.fixture()
def pid_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "orion_backend.pid"))
    return tmp_path / "orion_backend.pid"


def test_read_pid_file_tidak_ada(pid_env):
    out = runtime_id.read_pid_file()
    assert out["exists"] is False and out["pid"] is None and out["error"] is None


def test_read_pid_file_korup_tidak_raise(pid_env):
    pid_env.write_text("{bukan json", encoding="utf-8")
    out = runtime_id.read_pid_file()
    assert out["exists"] is True and out["pid"] is None and out["error"]


def test_read_pid_file_bukan_objek(pid_env):
    pid_env.write_text("[1, 2]", encoding="utf-8")
    out = runtime_id.read_pid_file()
    assert out["exists"] is True and out["error"] == "corrupt: not a JSON object"


def test_read_pid_file_valid(pid_env):
    pid_env.write_text(json.dumps({"pid": 1234, "started_at": 1.0, "version": "x"}), encoding="utf-8")
    out = runtime_id.read_pid_file()
    assert (out["pid"], out["started_at"], out["version"]) == (1234, 1.0, "x")
    assert out["error"] is None


# --------------------------------------------------------------------------
# forensics_payload verdicts
# --------------------------------------------------------------------------

def _tulis_klaim(pid_env, pid):
    pid_env.write_text(
        json.dumps({"pid": pid, "started_at": time.time(), "version": runtime_id.BACKEND_VERSION}),
        encoding="utf-8",
    )


def test_forensics_bersih_tanpa_file(pid_env):
    out = runtime_id.forensics_payload(None)
    assert out["verdicts"] == []
    assert out["pid_file"]["exists"] is False
    assert out["self"]["pid"] == os.getpid()


def test_forensics_klaim_sendiri_bersih(pid_env):
    runtime_id.write_pid_file()
    out = runtime_id.forensics_payload("proj")
    assert out["verdicts"] == []
    assert out["pid_file"]["pid"] == os.getpid()
    assert out["project_path"] == "proj"


def test_forensics_twin_suspected(pid_env):
    """Another LIVE pid claiming the file = the incident-013 signature."""
    _tulis_klaim(pid_env, os.getppid())  # the parent process is alive
    out = runtime_id.forensics_payload(None)
    assert len(out["verdicts"]) == 1
    assert "TWIN SUSPECTED" in out["verdicts"][0]
    assert out["pid_file_claim_alive"] is True


def test_forensics_stale_pid_file(pid_env):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    _tulis_klaim(pid_env, proc.pid)
    out = runtime_id.forensics_payload(None)
    assert len(out["verdicts"]) == 1
    assert "STALE PID FILE" in out["verdicts"][0]
    assert out["pid_file_claim_alive"] is False


def test_forensics_file_unreadable(pid_env):
    pid_env.write_text("", encoding="utf-8")
    out = runtime_id.forensics_payload(None)
    assert any("PID FILE UNREADABLE" in v for v in out["verdicts"])


def test_forensics_stale_code(pid_env, monkeypatch):
    """Booted before the code's last modification = running old code."""
    main_py = runtime_id.BACKEND_DIR / "app" / "main.py"
    monkeypatch.setattr(runtime_id, "STARTED_AT", main_py.stat().st_mtime - 3600)
    out = runtime_id.forensics_payload(None)
    assert any("STALE CODE" in v for v in out["verdicts"])


# --------------------------------------------------------------------------
# route
# --------------------------------------------------------------------------

def test_route_forensics_butuh_token(monkeypatch, tmp_path):
    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "pid"))
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/runtime/forensics").status_code == 401
        r = client.get("/runtime/forensics", headers={"X-Orion-Token": "s3cret"})
        assert r.status_code == 200
        body = r.json()
        assert {"app", "version", "self", "pid_file", "verdicts"} <= set(body)
        assert body["version"] == runtime_id.BACKEND_VERSION
