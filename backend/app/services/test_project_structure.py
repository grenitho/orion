"""
test_project_structure.py — guards for the UPDATE-019 structure checker
=======================================================================
``validate_project_structure`` runs on every /set-project and reports
nested `assets` folders (the classic duplicated-assets accident). Before
019 it skipped roots whose path merely CONTAINED "venv"/"target"/".git":

  * false POSITIVES: `node_modules/pkg/assets` and
    `__pycache__/…/assets` warned as duplicates (seen again on the
    owner's machine in 017/018);
  * false NEGATIVES: a legitimate folder like `target-practice` or
    `venv-materials` was skipped entirely because its name contains a
    magic substring;
  * no walk cap: a 100k-folder project stalled set-project for nothing.

The checker now prunes by the scanner's pattern family and caps the walk
with a LOUD truncation warning. These tests pin all three behaviours.
"""

from __future__ import annotations

import pytest

import app.main as main
from app.main import validate_project_structure


def _buat(roots, tmp_path):
    """Create one assets folder under each given relative dir."""
    for rel in roots:
        (tmp_path / rel / "assets").mkdir(parents=True, exist_ok=True)


def test_assets_di_root_bukan_duplikat(tmp_path):
    _buat(["."], tmp_path)
    assert validate_project_structure(str(tmp_path)) == []


def test_assets_bersarang_di_folder_proyek_tetap_duplikat(tmp_path):
    _buat(["src", "src/ui"], tmp_path)
    warnings = validate_project_structure(str(tmp_path))
    assert len(warnings) == 2
    assert all("Duplicate folder detected" in w for w in warnings)


# --------------------------------------------------------------------------
# the 019 fix: dependency/tooling trees are pruned by pattern
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "node_modules/pkg",
    "node_modules",
    "node_modules_old/pkg",          # contains-marker, like the scanner
    "__pycache__/deep",
    "venv/lib/site-packages/pkg",
    ".venv/lib/pkg",
    "venv-lama-20260101/lib/pkg",    # renamed virtualenv family
    "env/lib",
    "target/release/pkg",
    ".git/hooks",
    ".orion/tmp",
    ".vscode/x",
    ".pytest_cache/x",
])
def test_assets_di_folder_dependensi_tidak_diperingatkan(tmp_path, rel):
    """The owner's recurring false positive, in every pruned flavour."""
    _buat([rel], tmp_path)
    assert validate_project_structure(str(tmp_path)) == []


@pytest.mark.parametrize("rel", [
    "target-practice",               # substring victims of the OLD code:
    "mytarget/app",                  # legitimate folders the old substring
])                                   # skip silently missed
def test_folder_sah_bukan_keluarga_venv_tetap_diperiksa(tmp_path, rel):
    _buat([rel], tmp_path)
    warnings = validate_project_structure(str(tmp_path))
    assert len(warnings) == 1 and "Duplicate folder detected" in warnings[0]


def test_false_positive_dokumentasi_keluarga_venv(tmp_path):
    """Same accepted trade-off as dependency_scanner._dir_ignored: a real
    folder whose name STARTS with the venv prefix is not checked (cheap
    price for never walking a renamed virtualenv again)."""
    _buat(["venv-materials"], tmp_path)
    assert validate_project_structure(str(tmp_path)) == []


def test_prune_juga_menghemat_walk(tmp_path, monkeypatch):
    """A deep assets tree inside node_modules must never even be visited:
    cap the walk at 3 dirs and prove the prune keeps the scan under it."""
    _buat(["node_modules/a/b/c/d/e/assets"], tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(main, "VALIDATE_MAX_DIRS", 3)
    warnings = validate_project_structure(str(tmp_path))
    assert warnings == []  # root+docs+src = 3 visits; node_modules pruned


# --------------------------------------------------------------------------
# the walk cap (honest truncation)
# --------------------------------------------------------------------------

def test_cap_walk_berhenti_dengan_peringatan_keras(tmp_path, monkeypatch):
    for i in range(12):
        (tmp_path / f"folder{i}" / "sub").mkdir(parents=True)
    _buat(["folder7/sub"], tmp_path)  # a real duplicate BELOW the cap line

    monkeypatch.setattr(main, "VALIDATE_MAX_DIRS", 5)
    warnings = validate_project_structure(str(tmp_path))
    assert any("STRUCTURE SCAN CAPPED" in w for w in warnings)

    monkeypatch.setattr(main, "VALIDATE_MAX_DIRS", 20_000)
    warnings = validate_project_structure(str(tmp_path))
    assert not any("CAPPED" in w for w in warnings)
    assert any("Duplicate folder detected" in w for w in warnings)


def test_proyek_kosong_dan_tidak_ada(tmp_path):
    assert validate_project_structure(str(tmp_path)) == []
    assert validate_project_structure(str(tmp_path / "tidak-ada")) == []
