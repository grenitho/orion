"""
test_dependency_scanner.py — pattern ignore + hard file cap (UPDATE-013 patch 0007)
=====================================================================================
Guards the two hardening promises made after a real incident in which a
renamed virtualenv folder (`venv-lama-20261021`) slipped past the
EXACT-name ignore list, produced a 45 MB dependency map and pushed every
chat to ~560k input tokens:

  1. folders whose name merely STARTS with a venv family prefix or
     CONTAINS a dependency-cache marker are ignored, not only the exact
     names in the blacklist;
  2. a scan never maps more than ``max_scanned_files`` files, and says so
     (``truncated`` flag + a loud notice inside the project map and the
     /set-project warnings) instead of silently shipping a partial map.
"""

from __future__ import annotations

from app.services.dependency_scanner import DependencyScanner


def _buat(proj, rel: str, content: str = "x = 1\n") -> None:
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_folder_venv_bernama_aneh_tetap_diabaikan(tmp_path):
    """The incident name and its cousins must never be mapped again."""
    _buat(tmp_path, "src/app.py", "def main():\n    ...\n")
    _buat(tmp_path, "venv-lama-20261021/Lib/site-packages/paket/mod.py")
    _buat(tmp_path, "venv-cadangan/mod.py")
    _buat(tmp_path, "VENV-HURUF-BESAR/mod.py")
    _buat(tmp_path, "backup_node_modules/paket/index.js", "export function f(){}\n")
    _buat(tmp_path, "cache__pycache__lama/mod.py")

    result = DependencyScanner(str(tmp_path)).scan()

    assert list(result["files"]) == ["src/app.py"]
    assert result["truncated"] is False


def test_folder_biasa_tetap_dipetakan(tmp_path):
    """The pattern rules must not swallow legitimate project folders."""
    _buat(tmp_path, "vendor/tool.py", "def jual():\n    ...\n")
    _buat(tmp_path, "docs/catatan.md", "# catatan\n")
    _buat(tmp_path, "environment.md", "isi\n")  # starts with 'env' BUT not 'venv'/'.venv'

    result = DependencyScanner(str(tmp_path)).scan()

    assert set(result["files"]) == {"vendor/tool.py", "docs/catatan.md", "environment.md"}


def test_cap_membatasi_dan_memberi_tanda(tmp_path):
    for i in range(12):
        _buat(tmp_path, f"pkg/mod{i:02d}.py", f"def f{i}():\n    ...\n")

    scanner = DependencyScanner(str(tmp_path))
    scanner.max_scanned_files = 5
    result = scanner.scan()

    assert result["truncated"] is True
    assert result["total_files"] == 5
    assert result["file_cap"] == 5


def test_di_bawah_cap_tidak_truncated(tmp_path):
    _buat(tmp_path, "a.py")
    _buat(tmp_path, "b.py")

    result = DependencyScanner(str(tmp_path)).scan()

    assert result["truncated"] is False
    assert result["total_files"] == 2


def test_set_project_melaporkan_truncation_ke_pengguna(monkeypatch, tmp_path):
    """Route-level: the truncation notice must reach the map AND the warnings."""
    from app.services import auth

    monkeypatch.setenv(auth.TOKEN_ENV, "s3cret")
    import app.services.dependency_scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "MAX_SCANNED_FILES", 3)
    # >>> UPDATE-014 adendum: this route calls save_last_project(), which used
    # to write the developer's REAL ~/.orion_last_project.json — so a pytest
    # run left a tmp_path behind that the next backend start auto-restored
    # (observed live: /health reported a pytest-of-* project_path). Redirect
    # the persistence file into tmp_path; the route behaviour is unchanged.
    import app.main as main_module

    monkeypatch.setattr(
        main_module, "LAST_PROJECT_FILE", str(tmp_path / "last_project.json")
    )
    for i in range(8):
        _buat(tmp_path, f"pkg/mod{i}.py", f"def f{i}():\n    ...\n")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/set-project",
            json={"path": str(tmp_path)},
            headers={auth.HEADER: "s3cret"},
        )
        body = r.json()
        assert body["success"] is True
        assert any("TRUNCATED" in w for w in body["structure_warnings"])
        assert body["project_map_preview"].startswith("⚠️")
