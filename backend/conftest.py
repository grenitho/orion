"""
conftest.py — global test isolation for the backend suite (UPDATE-014 adendum)
================================================================================
Route-level tests drive the real FastAPI app through TestClient, whose
lifespan runs ``auto_restore_project()``. On a developer machine that file
(``~/.orion_last_project.json``) points at a REAL project, and since
UPDATE-014 a real project may carry a live ``.orion/mcp.json`` — without
this fixture, every TestClient test would spawn the developer's actual MCP
servers as subprocesses (slow, non-hermetic, and they could outlive a
crashed test run).

Pointing ``ORION_MCP_CONFIG`` at a non-existent file makes
``MCPManager.load_config`` find nothing, so no test ever spawns an MCP
server unless it deliberately overrides this env var itself (see
test_mcp_manager.test_override_lokasi_config_lewat_env).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolasi_mcp_config(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ORION_MCP_CONFIG", str(tmp_path / "mcp-config-tidak-ada.json")
    )
