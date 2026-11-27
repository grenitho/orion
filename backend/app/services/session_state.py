"""
session_state.py — the per-turn agent state (Phase C step 2 + P9.0 Multi-Session)
=================================================================================
Before this module existed, the agent loop in main.py carried ~9 loose mutable
locals initialized at the top of the ~1,200-line generator (analyzed_files,
files_changed_this_turn, pending_system_notices, the nudge/verification
counters, the single-use attachment image parts, current_iter). That made the
loop's bookkeeping impossible to unit-test and easy to break.

SessionState bundles those names into one dataclass with small explicit
methods. In P9.0, SessionState also carries session_id isolation, activity
timestamps for idle eviction, and atomic per-session checkpoint persistence.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

_SESSION_ID_INVALID = re.compile(r"[^a-zA-Z0-9_-]")
MAX_SESSION_ID_LEN = 64


def normalisasi_session_id(session_id: str | None) -> str:
    """Normalize a client-provided session_id into a safe filesystem token.

    Returns 'default' when empty/None so legacy single-session clients map
    1-to-1 onto the historical .orion/ files.
    """
    if not session_id or not isinstance(session_id, str):
        return "default"
    cleaned = _SESSION_ID_INVALID.sub("_", session_id.strip()).strip("._-")
    if not cleaned or cleaned == "default":
        return "default"
    return cleaned[:MAX_SESSION_ID_LEN]


def jalur_file_sesi(
    project_path: str, filename: str, session_id: str | None = "default"
) -> str:
    """Resolve the storage path for a session-scoped JSON file.

    - session_id == 'default' -> <project>/.orion/<filename> (100% backward compatible)
    - session_id != 'default' -> <project>/.orion/sessions/<session_id>/<filename>
    """
    sid = normalisasi_session_id(session_id)
    orion_dir = os.path.join(project_path, ".orion")
    if sid == "default":
        return os.path.join(orion_dir, filename)
    return os.path.join(orion_dir, "sessions", sid, filename)


@dataclass
class SessionState:
    """Mutable state of one /chat session & turn."""

    analyzed_files: set = field(default_factory=set)
    files_changed_this_turn: list = field(default_factory=list)
    pending_system_notices: list = field(default_factory=list)
    pending_image: object = None
    nudge_count: int = 0
    verifikasi_count: int = 0
    subagent_calls: int = 0

    edit_gagal: list = field(default_factory=list)
    edit_sukses: list = field(default_factory=list)
    _edit_urutan: int = 0

    current_iter: int = 0

    permintaan_turn_ini: str = ""
    last_tool_note: str = ""
    todo_ids_awal_turn: set = field(default_factory=set)
    delegasi_terpakai: set = field(default_factory=set)
    nasib_role: dict = field(default_factory=dict)
    utas_gagal: str = ""
    saran_count: int = 0
    fakta_persistensi_disampaikan: bool = False
    lampiran_dibaca_turn_ini: set = field(default_factory=set)
    nudge_referensi: int = 0
    gaya_diperiksa_turn_ini: bool = False
    nudge_gaya: int = 0
    tantang_klaim_visual: int = 0
    tantang_klaim_implementasi: int = 0
    tantang_perintah_tool: int = 0
    tantang_nihil_mutasi: int = 0
    fallback_ipv4_count: int = 0
    perintah_tool_eksplisit: str = ""
    panggilan_browser_turn_ini: int = 0
    bahasa_turn: str = ""

    terminal_ok: int = 0
    bukti_todo: dict = field(default_factory=dict)

    debt_verifikasi: list = field(default_factory=list)
    debt_disampaikan: list = field(default_factory=list)
    turn_ke: int = 0
    nir_mutasi_beruntun: int = 0
    tulis_akhir_iterasi: int = 0
    edit_gagal_beruntun: int = 0
    tantang_stagnasi: int = 0
    perintah_butuh_gaya: bool = False
    gaya_bukti_turn: list = field(default_factory=list)
    zero_mutasi_turn: bool = False
    kutipan_zero_mutasi: str = ""
    nudge_ledger: int = 0
    probe_tool_hilang: int = 0
    lampiran_note_turn: int = 0
    lampiran_blok: str = ""

    # >>> P9.0: Multi-session identity & activity tracking <<<
    session_id: str = "default"
    last_accessed: float = field(default_factory=time.time)
    _checkpoint_file_terakhir: list = field(default_factory=list, repr=False)

    def sentuh_aktivitas(self) -> None:
        """Perbarui timestamp aktivitas terakhir untuk pelacakan idle eviction."""
        self.last_accessed = time.time()

    def catat_file_berubah(self, entri: str) -> None:
        """Record one piece of real-work evidence (a successfully changed file)."""
        self.files_changed_this_turn.append(entri)
        self.edit_gagal_beruntun = 0
        self.tutup_debt_verifikasi("stall")

    def catat_edit_gagal(self, path: str, alasan: str) -> None:
        """Record ONE failed write_file/edit_file attempt (UPDATE-022 ledger)."""
        self._edit_urutan += 1
        self.edit_gagal_beruntun += 1
        self.edit_gagal.append(
            {"urutan": self._edit_urutan, "path": path, "alasan": alasan}
        )

    def catat_edit_sukses(self, path: str) -> None:
        """Record ONE successful write_file/edit_file (UPDATE-022 ledger)."""
        self._edit_urutan += 1
        self.edit_sukses.append({"urutan": self._edit_urutan, "path": path})
        self.edit_gagal_beruntun = 0

    def cap_iterasi_maks(self, basis: int) -> int:
        """UPDATE-025 commit 25: adaptive iteration ceiling."""
        return basis + 4 * len(self.debt_verifikasi)

    def catat_debt_verifikasi(self, jenis: str, perintah: str) -> None:
        """Open ONE verification debt (idempotent per jenis while open)."""
        try:
            if any(d.get("jenis") == jenis for d in self.debt_verifikasi):
                return
            from app.services import guard_rules

            teks_perintah = str(perintah or "").strip()
            if not teks_perintah or guard_rules.adalah_kata_lanjut(teks_perintah):
                teks_perintah = "(lanjutan tugas sebelumnya)"
            self.debt_verifikasi.append(
                {
                    "jenis": jenis,
                    "perintah": teks_perintah[:300],
                    "turn_dibuat": self.turn_ke,
                }
            )
        except Exception:  # noqa: BLE001 - ledger must never break a turn
            pass

    def utang_verifikasi_terbuka(self) -> list:
        """Copies of the open debts (anchor + emitters read this)."""
        return [dict(d) for d in self.debt_verifikasi]

    def tutup_debt_verifikasi(self, jenis: str) -> bool:
        """Close ONE open debt: evidence landed, or a decline about an OLDER debt."""
        sebelum = len(self.debt_verifikasi)
        self.debt_verifikasi = [
            d for d in self.debt_verifikasi if d.get("jenis") != jenis
        ]
        if len(self.debt_verifikasi) != sebelum:
            self.debt_disampaikan.append(jenis)
            return True
        return False

    def edit_gagal_belum_pulih(self) -> list:
        """Failed edits that NEVER succeeded afterwards, oldest first."""
        sukses_terakhir: dict = {}
        for entri in self.edit_sukses:
            path = entri["path"]
            if entri["urutan"] > sukses_terakhir.get(path, -1):
                sukses_terakhir[path] = entri["urutan"]
        terbaru: dict = {}
        for entri in sorted(self.edit_gagal, key=lambda e: e["urutan"]):
            if sukses_terakhir.get(entri["path"], -1) > entri["urutan"]:
                continue
            terbaru[entri["path"]] = entri
        return sorted(terbaru.values(), key=lambda e: e["urutan"])

    def ringkasan_ledger(self) -> str:
        """Checkpoint-report summary of the ledger (UPDATE-022)."""
        if not self.edit_gagal:
            return ""
        sukses_terakhir: dict = {}
        for entri in self.edit_sukses:
            path = entri["path"]
            if entri["urutan"] > sukses_terakhir.get(path, -1):
                sukses_terakhir[path] = entri["urutan"]
        baris = []
        for entri in sorted(self.edit_gagal, key=lambda e: e["urutan"]):
            pulih = sukses_terakhir.get(entri["path"], -1) > entri["urutan"]
            tanda = (
                "RECOVERED (a later attempt succeeded)" if pulih else "NEVER SUCCEEDED"
            )
            baris.append(f"- `{entri['path']}` — {entri['alasan']} [{tanda}]")
        return "\n\n**⚠️ EDIT LEDGER — failed edit attempts this turn:**\n" + "\n".join(
            baris
        )

    def tambah_notice(self, notice: str) -> None:
        """Queue a system notice to be flushed after the tool results are stored."""
        self.pending_system_notices.append(notice)

    def reset_per_turn(self) -> None:
        """Clear the turn-scoped records when the state is reused for a new turn."""
        self.files_changed_this_turn = []
        self.pending_system_notices = []
        self.edit_gagal = []
        self.edit_sukses = []
        self._edit_urutan = 0
        self.current_iter = 0
        self.nir_mutasi_beruntun = 0
        self.tulis_akhir_iterasi = 0
        self.edit_gagal_beruntun = 0
        self.tantang_stagnasi = 0
        self.perintah_butuh_gaya = False
        self.gaya_bukti_turn = []
        self.zero_mutasi_turn = False
        self.kutipan_zero_mutasi = ""
        self.nudge_ledger = 0
        self.probe_tool_hilang = 0
        self.lampiran_note_turn = 0
        self.lampiran_blok = ""
        self.turn_ke += 1
        self.sentuh_aktivitas()

    def sisa_todo(self, todo_manager) -> list:
        """Todo items still pending/in_progress ([] when todo_manager is absent)."""
        if todo_manager is None:
            return []
        return [
            t
            for t in todo_manager.get_todos()
            if t.get("status") in ("pending", "in_progress")
        ]

    # >>> P9.0: Multi-Session Hardening & Pause-Resume Persistence ===========
    def simpan_checkpoint(
        self,
        project_path: str,
        catatan_terakhir: str = "",
        session_id: str | None = None,
    ) -> None:
        """Simpan state lintas-giliran ke file checkpoint sesi secara atomik."""
        if not project_path:
            return
        sid = normalisasi_session_id(
            session_id if session_id is not None else self.session_id
        )
        self.session_id = sid
        self.sentuh_aktivitas()
        target = jalur_file_sesi(project_path, "session_checkpoint.json", sid)
        tmp = f"{target}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            payload = {
                "session_id": sid,
                "analyzed_files": sorted(self.analyzed_files)[-200:],
                "debt_verifikasi": list(self.debt_verifikasi),
                "utas_gagal": self.utas_gagal,
                "turn_ke": self.turn_ke,
                "file_terakhir_diubah": list(self.files_changed_this_turn)[-30:],
                "catatan_terakhir": (catatan_terakhir or self.last_tool_note)[:400],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, target)
        except Exception:  # noqa: BLE001 - persistensi tidak boleh mematikan stream
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    @classmethod
    def muat_checkpoint(
        cls, project_path: str, session_id: str | None = "default"
    ) -> "SessionState":
        """Muat SessionState dari file checkpoint sesi jika ada."""
        sid = normalisasi_session_id(session_id)
        state = cls(session_id=sid)
        if not project_path:
            return state
        target = jalur_file_sesi(project_path, "session_checkpoint.json", sid)
        if not os.path.isfile(target):
            return state
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state.analyzed_files = set(data.get("analyzed_files") or [])
                state.debt_verifikasi = list(data.get("debt_verifikasi") or [])
                state.utas_gagal = str(data.get("utas_gagal") or "")
                state.turn_ke = int(data.get("turn_ke") or 0)
                state.last_tool_note = str(data.get("catatan_terakhir") or "")
                state._checkpoint_file_terakhir = list(
                    data.get("file_terakhir_diubah") or []
                )
        except Exception:  # noqa: BLE001
            pass
        state.sentuh_aktivitas()
        return state

    @staticmethod
    def hapus_checkpoint(
        project_path: str, session_id: str | None = "default"
    ) -> None:
        """Hapus file checkpoint sesi saat /clear-chat."""
        if not project_path:
            return
        target = jalur_file_sesi(project_path, "session_checkpoint.json", session_id)
        try:
            if os.path.exists(target):
                os.remove(target)
        except OSError:
            pass
