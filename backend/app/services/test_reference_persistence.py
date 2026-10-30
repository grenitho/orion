"""
test_reference_persistence.py — UPDATE-025 commit 11 (backend 0.10.15)
======================================================================
Owner incident 2026-10-30 (InvoiceFlow frontend): the user attached a
single-file reference prototype (invoiceflow.html: deep-green #10302a
sidebar, gold #d9a21b accent, Bricolage Grotesque) plus a design-token
handoff (FRONTEND_HANDOFF.md) and screenshots. Orion rebuilt the UI in
an INVENTED indigo #4f46e5 / cyan palette and reported "selesai",
verified only by `npm run build`.

Three structural defects, three teeth:
  1. attachments were inline-only: the copy lived inside ONE user
     message; summarize/prune erased it and no file ever existed on
     disk, so on the implementation turn the reference was literally
     unreadable ("invoiceflow.html is not on disk") -> attachments.py
     now persists text-ish attachments under .orion/attachments/ and
     the placeholder names the durable path; read_file of a reference
     is exempt from the redundancy guard (re-reading after
     summarization is the whole point).
  2. the option-answer reminder banned read_file on ".orion/ or ANY
     documentation file" — which swallowed the user's own handoff when
     the owner replied "ya, lanjutkan" -> ban narrowed to .orion/
     context files; user references are MANDATORY reading.
  3. nothing compared the work against the reference -> the checkpoint
     now discloses when UI files changed while saved reference
     attachments sat unread for the whole turn.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json

from app.services import agent_loop as al
from app.services import attachments
from app.services.agent_loop import Deps, _blok_referensi_belum_dibaca, aliran_agent
from app.services.session_state import SessionState
from app.services.test_soft_limit_reanchor import _CM, _LLM, _Resp, _TM, _tool_call
from app.services.test_todo_evidence_checkpoint import _L, _TE

REF_HTML = b"<html><style>:root{--side:#10302a;--acc:#d9a21b}</style></html>"


def _att(nama="invoiceflow.html", isi=REF_HTML):
    return {
        "name": nama,
        "mime": "text/html",
        "data": base64.b64encode(isi).decode(),
    }


class _TERef(_TE):
    """Executor pointing at a real tmp project; records read_file calls."""

    def __init__(self, path):
        self.project_path = str(path)
        self.dibaca = []

    def read_file(self, **kw):
        self.dibaca.append(kw.get("file_path"))
        return REF_HTML.decode()


def _buat_referensi(tmp_path, *nama):
    refdir = tmp_path / ".orion" / "attachments"
    refdir.mkdir(parents=True, exist_ok=True)
    for n in nama:
        (refdir / n).write_text("ref", encoding="utf-8")
    return refdir


def _jalankan(pesan, responses, todos=None, executor=None, state=None):
    state = state if state is not None else SessionState()
    deps = Deps(
        context_manager=_CM([{"role": "user", "content": pesan}]),
        todo_manager=_TM(todos) if todos is not None else None,
        tool_executor=executor or _TE(),
        llm_call=_LLM(responses),
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": 8, "token_budget_threshold": 12000},
        logger=_L(),
        context_message={"role": "user", "content": "Current project structure: x"},
    )
    frames = []

    async def _run():
        async for f in aliran_agent(state, deps):
            frames.append(f)

    asyncio.run(_run())
    return frames, state, deps


# ---------------------------------------------------------------- tooth 1


def test_lampiran_teks_disimpan_ke_disk_dan_placeholder_menunjuknya(tmp_path):
    parts, ringkasan = attachments.build_user_content(
        "bangun frontend sesuai referensi terlampir",
        [_att()],
        project_path=str(tmp_path),
    )
    tersimpan = tmp_path / ".orion" / "attachments" / "invoiceflow.html"
    assert tersimpan.exists() and tersimpan.read_bytes() == REF_HTML
    assert ".orion/attachments/invoiceflow.html" in ringkasan
    assert "survives context summarization" in ringkasan
    # the inline copy stays — persistence ADDS a durable one, changes
    # nothing about what the model sees on the attaching turn.
    gabungan = parts if isinstance(parts, str) else "".join(str(p) for p in parts)
    assert "ATTACHED FILE CONTENTS: invoiceflow.html" in gabungan
    assert "#10302a" in gabungan


def test_nama_lampiran_disanitasi_tanpa_path_escape(tmp_path):
    attachments.proses("pesan", [_att(nama="../../etc/pa ss.html")],
                       project_path=str(tmp_path))
    saved = list((tmp_path / ".orion" / "attachments").iterdir())
    assert len(saved) == 1
    assert "/" not in saved[0].name and "\\" not in saved[0].name
    assert ".." not in saved[0].name


def test_tanpa_project_path_perilaku_lama_utuh():
    parts, ringkasan = attachments.build_user_content("pesan", [_att()])
    assert "durable copy" not in ringkasan
    assert "#10302a" in ringkasan or any("#10302a" in str(p) for p in parts)


def test_referensi_boleh_dibaca_ulang_meski_sudah_dianalisis(tmp_path):
    ref = ".orion/attachments/invoiceflow.html"
    _buat_referensi(tmp_path, "invoiceflow.html")
    ex = _TERef(tmp_path)
    state = SessionState()
    state.analyzed_files = {ref}  # "already read" on an earlier turn
    frames, state, _ = _jalankan(
        "lanjutkan implementasi",
        [
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": ref}))]),
            _Resp("Referensi sudah saya baca ulang."),
        ],
        executor=ex,
        state=state,
    )
    gabungan = "".join(frames)
    assert "REDUNDANCY DETECTED" not in gabungan
    assert ex.dibaca == [ref]
    assert state.lampiran_dibaca_turn_ini == {ref}


# ---------------------------------------------------------------- tooth 2


def test_reminder_jawaban_opsi_tidak_melarang_referensi_user():
    src = inspect.getsource(al)
    # the over-broad ban that swallowed FRONTEND_HANDOFF.md is gone…
    assert "or any documentation file" not in src
    # …stale .orion/ claims stay banned, user references become
    # MANDATORY reading, and the durable-copy location is named.
    assert "Do NOT call read_file on `.orion/` context files" in src
    assert "MANDATORY reading" in src
    assert ".orion/attachments/" in src


# ---------------------------------------------------------------- tooth 3


def test_blok_referensi_menyalak_hanya_untuk_ui_tanpa_bacaan(tmp_path):
    _buat_referensi(tmp_path, "FRONTEND_HANDOFF.md")
    st = SessionState()
    # no changes at all -> silent
    assert _blok_referensi_belum_dibaca(st, str(tmp_path)) == ""
    # non-UI changes only -> silent
    st.files_changed_this_turn.append("- `backend/main.py` (edit_file)")
    assert _blok_referensi_belum_dibaca(st, str(tmp_path)) == ""
    # a UI file changed, references unread -> fires and names them
    st.files_changed_this_turn.append("- `src/App.vue` (write_file)")
    blok = _blok_referensi_belum_dibaca(st, str(tmp_path))
    assert "NOT consulted" in blok
    assert ".orion/attachments/FRONTEND_HANDOFF.md" in blok
    assert "design tokens" in blok
    # consulted this turn -> silent again
    st.lampiran_dibaca_turn_ini.add(".orion/attachments/FRONTEND_HANDOFF.md")
    assert _blok_referensi_belum_dibaca(st, str(tmp_path)) == ""


def test_blok_referensi_senyap_tanpa_lampiran_tersimpan(tmp_path):
    st = SessionState()
    st.files_changed_this_turn.append("- `src/App.vue` (write_file)")
    assert _blok_referensi_belum_dibaca(st, str(tmp_path)) == ""


TODO_AWAL = [{"id": "1", "text": "Bangun UI sesuai referensi", "status": "pending"}]
SELESAI = (
    '{"todos": [{"id": "1", "text": "Bangun UI sesuai referensi", '
    '"status": "completed"}]}'
)
TULIS_VUE = json.dumps({"file_path": "src/App.vue", "content": "<template/>"})
REF = ".orion/attachments/invoiceflow.html"


def test_checkpoint_mengungkapkan_ui_selesai_tanpa_membaca_referensi(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("", tool_calls=[_tool_call("update_todos", SELESAI)]),
            _Resp("UI selesai dibangun."),
        ],
        todos=[dict(t) for t in TODO_AWAL],
        executor=_TERef(tmp_path),
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT consulted" in laporan
    assert "invoiceflow.html" in laporan


def test_checkpoint_senyap_bila_referensi_dibaca_lebih_dulu(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]),
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("", tool_calls=[_tool_call("update_todos", SELESAI)]),
            _Resp("UI selesai dibangun sesuai referensi."),
        ],
        todos=[dict(t) for t in TODO_AWAL],
        executor=_TERef(tmp_path),
    )
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT consulted" not in laporan
    assert state.lampiran_dibaca_turn_ini == {REF}
