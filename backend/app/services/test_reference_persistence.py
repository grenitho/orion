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
from app.services.agent_loop import (
    Deps,
    _blok_referensi_belum_dibaca,
    _kalimat_referensi_ada,
    aliran_agent,
)
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


# ---------------------------------------------------- commit 12: in-turn gate


def test_nudge_referensi_menyala_sebelum_tutup_lalu_dibaca(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> gate
            _Resp("", tool_calls=[
                _tool_call("read_file", json.dumps({"file_path": REF}))]),  # 2
            _Resp("UI selesai setelah rekonsiliasi token."),               # 3 -> close
        ],
        executor=_TERef(tmp_path),
    )
    gabungan = "".join(frames)
    assert "reconciliation owed" in gabungan          # the in-turn challenge
    assert state.nudge_referensi == 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT consulted" not in laporan             # read -> silent closing


def test_nudge_dianggalkan_mengeskalsasi_blok_checkpoint(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> gate
            _Resp("Semua tugas selesai dan terverifikasi."),               # 2 -> defiance
        ],
        executor=_TERef(tmp_path),
    )
    assert sum("reconciliation owed" in f for f in frames) == 1  # cap 1
    assert state.nudge_referensi == 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert "NOT consulted" in laporan
    assert "UNVERIFIED" in laporan                    # escalated wording


def test_nudge_senyap_tanpa_referensi_tersimpan(tmp_path):
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("UI selesai dibangun."),
        ],
        executor=_TERef(tmp_path),
    )
    assert "reconciliation owed" not in "".join(frames)
    assert state.nudge_referensi == 0


def test_nudge_reset_per_turn(tmp_path):
    _buat_referensi(tmp_path, "invoiceflow.html")
    ex = _TERef(tmp_path)
    state = SessionState()
    _jalankan(
        "kerjakan UI",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("selesai."),
            _Resp("selesai sungguh."),
        ],
        executor=ex,
        state=state,
    )
    assert state.nudge_referensi == 1
    # turn berikut mulai dari nol lagi (cap bukan akumulasi lintas turn)
    _jalankan(
        "lanjutkan UI",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),
            _Resp("selesai."),
            _Resp("selesai sungguh."),
        ],
        executor=ex,
        state=state,
    )
    assert state.nudge_referensi == 1


# --------------------------------------- commit 12: keluhan bahasa sederhana


def test_keluhan_data_polos_membuka_utas():
    from app.services import guard_rules

    for kalimat in (
        "data pelanggan hilang lagi setelah restart",
        "kok daftar pelanggan kosong lagi tiap saya mulai ulang?",
        "my customers disappear after every restart",
        "faktur yang saya buat kemarin tidak tersimpan",
    ):
        assert guard_rules.utas_gagal_dari(kalimat), kalimat


def test_kalimat_biasa_tidak_membuka_utas():
    from app.services import guard_rules

    for kalimat in (
        "port berapa yang dipakai backend InvoiceFlow sekarang?",
        "faktur PDF sudah bisa diunduh, terima kasih",
        "field nama masih kosong, tolong isi dulu",
    ):
        assert guard_rules.utas_gagal_dari(kalimat) == "", kalimat


# ------------------------------------------- commit 15: full attachment paths
#
# Owner paste 2026-10-30 (backend 0.10.18 live): the Indonesian one-line
# request was fulfilled, the report spoke Indonesian (teeth 13/14 WORK),
# the disclosure + UNVERIFIED escalation fired — and the gate was STILL
# evaded. Orion took the mid-turn challenge, ran list_files on the
# PROJECT ROOT, concluded "FRONTEND_HANDOFF.md dan invoiceflow.html
# tidak ditemukan" (they were sitting in .orion/attachments/, the
# checkpoint block even named the full paths), and self-certified
# "fully follows standard responsive design…". Root cause: the
# injection listed BARE file names — ", ".join(f"`{r}`") — so a
# root-scoped listing legally "proved" absence. Teeth: the injection
# names the full relative paths in both languages and shares ONE
# anti-evasion sentence with the checkpoint block
# (_kalimat_referensi_ada): claiming absence without ever calling
# read_file on that path is a false claim.


def _injeksi_gate(deps):
    """The stored REFERENCE RECONCILIATION injection(s), both arms.
    Marker must START the message — the checkpoint report also quotes
    "(REFERENCE RECONCILIATION)" inside its escalation text."""
    return [
        str(m.get("content", ""))
        for m in deps.context_manager.history
        if str(m.get("content", "")).startswith(
            (
                "[SYSTEM - REFERENCE RECONCILIATION]",
                "[SISTEM - REKONSILIASI REFERENSI]",
            )
        )
    ]


def test_injeksi_gate_menyebut_jalur_lengkap_lampiran(tmp_path):
    _buat_referensi(tmp_path, "FRONTEND_HANDOFF.md", "invoiceflow.html")
    frames, state, deps = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> gate
            _Resp("Semua tugas selesai."),                                 # 2 -> close
        ],
        executor=_TERef(tmp_path),
    )
    injeksi = _injeksi_gate(deps)
    assert len(injeksi) == 1, frames
    teks = injeksi[0]
    # full relative paths, not bare names
    assert "`.orion/attachments/FRONTEND_HANDOFF.md`" in teks
    assert "`.orion/attachments/invoiceflow.html`" in teks
    assert "`FRONTEND_HANDOFF.md`" not in teks
    assert "`invoiceflow.html`" not in teks
    # the shared anti-evasion sentence rides the injection
    assert (
        "These files EXIST at those paths; read them with read_file" in teks
    )
    assert "false claim" in teks


def test_injeksi_gate_indonesia_menyebut_jalur_lengkap(tmp_path):
    _buat_referensi(tmp_path, "FRONTEND_HANDOFF.md", "invoiceflow.html")
    frames, state, deps = _jalankan(
        "tolong kerjakan implementasi halaman faktur untuk aplikasi ini",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> gate
            _Resp("Semua tugas selesai."),                                 # 2 -> close
        ],
        executor=_TERef(tmp_path),
    )
    assert state.bahasa_turn == "id"
    injeksi = _injeksi_gate(deps)
    assert len(injeksi) == 1, frames
    teks = injeksi[0]
    assert "[SISTEM - REKONSILIASI REFERENSI]" in teks
    assert "`.orion/attachments/FRONTEND_HANDOFF.md`" in teks
    assert "`.orion/attachments/invoiceflow.html`" in teks
    # the spec sentence, verbatim, in the turn language
    assert (
        "File-file ini ADA di jalur tersebut; baca dengan read_file — "
        "mengklaim tidak ada tanpa pernah memanggil read_file pada jalur "
        "itu adalah klaim palsu." in teks
    )


def test_klaim_referensi_hilang_tanpa_read_file_tetap_tereskalasi(tmp_path):
    """End-to-end pin of the 2026-10-30 incident: after the nudge the
    model claims the references "do not exist" WITHOUT ever calling
    read_file — the executor's ledger proves it never touched them —
    and self-certifies completion anyway. The turn closes through the
    checkpoint ending (as the real incident did) carrying the escalated
    disclosure: the absence claim no longer hides the gap from the
    user."""
    _buat_referensi(tmp_path, "FRONTEND_HANDOFF.md", "invoiceflow.html")
    ex = _TERef(tmp_path)
    frames, state, _ = _jalankan(
        "kerjakan implementasi UI nya",
        [
            _Resp("", tool_calls=[_tool_call("write_file", TULIS_VUE)]),   # 0
            _Resp("UI selesai dibangun."),                                 # 1 -> gate
            _Resp(                                                         # 2 -> evasion
                "Saya sudah memeriksa root proyek: FRONTEND_HANDOFF.md dan "
                "invoiceflow.html tidak ditemukan, jadi pekerjaan selesai — "
                "hasil fully follows standard responsive design."
            ),
        ],
        executor=ex,
    )
    assert ex.dibaca == []                    # read_file never called
    assert state.nudge_referensi == 1
    assert sum("reconciliation owed" in f for f in frames) == 1  # cap 1
    laporan = "".join(f for f in frames if "TASK COMPLETED" in f)
    assert laporan, frames
    assert "NOT consulted" in laporan
    assert "UNVERIFIED" in laporan            # escalation still ships
    assert ".orion/attachments/FRONTEND_HANDOFF.md" in laporan
    assert "These files EXIST at those paths" in laporan


def test_satu_sumber_kalimat_anti_elakan(tmp_path):
    """The checkpoint block and the gate injection quote ONE helper —
    same sentence, both languages (commit 15 spec point 2)."""
    _buat_referensi(tmp_path, "FRONTEND_HANDOFF.md")
    st = SessionState()
    st.files_changed_this_turn.append("- `src/App.vue` (write_file)")
    for bahasa, potongan in (
        ("en", "These files EXIST at those paths; read them with read_file"),
        ("id", "File-file ini ADA di jalur tersebut; baca dengan read_file"),
    ):
        st.bahasa_turn = bahasa
        blok = _blok_referensi_belum_dibaca(st, str(tmp_path))
        assert _kalimat_referensi_ada(st) in blok
        assert potongan in blok
