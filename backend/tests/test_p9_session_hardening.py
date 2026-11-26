"""
test_p9_session_hardening.py — regression tests for P9.0 Multi-Session Hardening
"""

from __future__ import annotations

import asyncio

from app import main as orion_main
from app.services.session_state import SessionState


def test_checkpoint_persist_dan_pulih_lintas_restart(tmp_path):
    """State tetap pulih dari .orion/session_checkpoint.json setelah restart."""
    proj = str(tmp_path)
    state1 = SessionState()
    state1.analyzed_files.add("BLUEPRINT.md")
    state1.analyzed_files.add("app/core.py")
    state1.catat_file_berubah("app/core.py")
    state1.catat_debt_verifikasi("perintah_tool", "Jalankan verifikasi")
    state1.turn_ke = 3
    state1.simpan_checkpoint(proj, catatan_terakhir="edit_file path='app/core.py'")

    # Simulasi backend restart: muat dari disk
    state2 = SessionState.muat_checkpoint(proj)
    assert "BLUEPRINT.md" in state2.analyzed_files
    assert "app/core.py" in state2.analyzed_files
    assert state2.turn_ke == 3
    assert len(state2.debt_verifikasi) == 1
    assert getattr(state2, "_checkpoint_file_terakhir") == ["app/core.py"]

    # Hapus checkpoint saat clear-chat
    SessionState.hapus_checkpoint(proj)
    state3 = SessionState.muat_checkpoint(proj)
    assert state3.analyzed_files == set()
    assert state3.turn_ke == 0


def test_get_or_restore_state_mempertahankan_analyzed_files_antar_turn(tmp_path):
    """Turn kedua ('lanjut') tidak mereset analyzed_files milik sesi aktif."""
    proj = str(tmp_path)
    orion_main._project_states.clear()

    s_turn1 = orion_main._get_or_restore_state(proj)
    s_turn1.analyzed_files.add("BLUEPRINT.md")
    s_turn1.catat_file_berubah("modul_a.py")

    s_turn2 = orion_main._get_or_restore_state(proj)
    assert s_turn2 is s_turn1
    assert "BLUEPRINT.md" in s_turn2.analyzed_files
    assert s_turn2.files_changed_this_turn == []
    assert s_turn2.turn_ke == 1


def test_project_lock_terisolasi_per_proyek(tmp_path):
    """Proyek berbeda mendapat lock berbeda; proyek sama berbagi satu lock."""
    p1 = str(tmp_path / "proj_a")
    p2 = str(tmp_path / "proj_b")
    lock_a1 = orion_main._get_project_lock(p1)
    lock_a2 = orion_main._get_project_lock(p1)
    lock_b = orion_main._get_project_lock(p2)

    assert isinstance(lock_a1, asyncio.Lock)
    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b