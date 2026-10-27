"""
test_claim_ledger.py — the failure-aware edit ledger + claim gate (UPDATE-022)
================================================================================
Closes the product defect class exposed by the InvoiceFlow M1 forensics
(HANDOFF_UPDATE_022 §2C):

  * Orion reported "already fixed" for files whose edit_file/write_file call
    FAILED (or was never made) — the todo/checkpoint went green from reading
    already-correct code;
  * the ORION-17b claim gate passed anyway because the SAME turn contained
    a real edit of ANOTHER file: the gate counted the presence of changes,
    not the match between the claim and per-file evidence.

Layers tested here:
  1. SessionState ledger mechanics (record, recovery, order, reset, summary);
  2. guard_rules.klaim_edit_gagal_perlu_ditantang truth table;
  3. loop_guards: the new gate fires BEFORE the evidence gate and before the
     todo nudge, degrades with the shared quota, and stays out of the way
     when nothing failed or the failure recovered;
  4. the agent loop end-to-end (fake LLM): failed edit + claim → challenge
     naming the file → real retry → checkpoint whose report carries the
     ledger summary with the RECOVERED marker.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from app.services import guard_rules, loop_guards
from app.services.agent_loop import Deps, aliran_agent
from app.services.loop_guards import Keputusan
from app.services.session_state import SessionState

KLAIM = "The feature is finished; every file has been written and validated."
KLAIM_ID = "Tugas selesai: src/x.py sudah saya perbaiki seluruhnya malam ini."
JAWAB_PANJANG = "Here is an explanation of how the module works, step by step, in detail."
PENDEK = "ok"

TODO_PENDING = [{"id": "2", "text": "still to do", "status": "pending"}]


def _evaluasi(state, konten, *, iterasi=1, max_iter=20, sisa_todo=None,
              maks_nudge=10, maks_verifikasi=2):
    return loop_guards.evaluasi_akhir_iterasi(
        state, konten,
        iterasi=iterasi, max_iter=max_iter,
        sisa_todo=sisa_todo if sisa_todo is not None else [],
        maks_nudge=maks_nudge, maks_verifikasi=maks_verifikasi,
    )


# ============================================================ 1. ledger mechanics

def test_ledger_baru_kosong_dan_independen():
    a = SessionState()
    b = SessionState()
    assert a.edit_gagal == [] and a.edit_sukses == []
    assert a.edit_gagal_belum_pulih() == []
    assert a.ringkasan_ledger() == ""
    a.catat_edit_gagal("x.py", "boom")
    assert b.edit_gagal == []  # default factories are not shared


def test_gagal_tanpa_pemulihan_terdaftar():
    state = SessionState()
    state.catat_edit_gagal("src/auth.py", "Error: SEARCH block not found")
    belum = state.edit_gagal_belum_pulih()
    assert [g["path"] for g in belum] == ["src/auth.py"]
    assert belum[0]["alasan"] == "Error: SEARCH block not found"


def test_gagal_lalu_sukses_pada_path_sama_pulih():
    state = SessionState()
    state.catat_edit_gagal("src/auth.py", "Error: SEARCH block not found")
    state.catat_edit_sukses("src/auth.py")
    assert state.edit_gagal_belum_pulih() == []


def test_sukses_lalu_gagal_tetap_terdaftar():
    """A failure AFTER a success is unrecovered — order matters, not presence."""
    state = SessionState()
    state.catat_edit_sukses("src/auth.py")
    state.catat_edit_gagal("src/auth.py", "Error writing file")
    belum = state.edit_gagal_belum_pulih()
    assert [g["path"] for g in belum] == ["src/auth.py"]


def test_dua_gagal_satu_path_hanya_entri_terbaru():
    state = SessionState()
    state.catat_edit_gagal("src/a.py", "alasan lama")
    state.catat_edit_gagal("src/a.py", "alasan baru")
    belum = state.edit_gagal_belum_pulih()
    assert len(belum) == 1
    assert belum[0]["alasan"] == "alasan baru"


def test_urutan_lintas_path_diikuti():
    state = SessionState()
    state.catat_edit_gagal("b.py", "e1")
    state.catat_edit_sukses("c.py")
    state.catat_edit_gagal("a.py", "e2")
    assert [g["path"] for g in state.edit_gagal_belum_pulih()] == ["b.py", "a.py"]


def test_reset_per_turn_membersihkan_ledger():
    state = SessionState()
    state.catat_edit_gagal("x.py", "boom")
    state.catat_edit_sukses("y.py")
    state.reset_per_turn()
    assert state.edit_gagal == [] and state.edit_sukses == []
    assert state.edit_gagal_belum_pulih() == []
    # a fresh turn starts the sequence over
    state.catat_edit_gagal("z.py", "boom")
    assert state.edit_gagal[0]["urutan"] == 1


def test_ringkasan_ledger_hanya_bila_ada_gagal():
    state = SessionState()
    state.catat_edit_sukses("y.py")
    assert state.ringkasan_ledger() == ""  # success-only turns: report unchanged
    state.catat_edit_gagal("x.py", "Error: SEARCH block not found")
    state.catat_edit_gagal("z.py", "Error writing file")
    state.catat_edit_sukses("z.py")
    ringkas = state.ringkasan_ledger()
    assert "EDIT LEDGER" in ringkas
    assert "`x.py`" in ringkas and "NEVER SUCCEEDED" in ringkas
    assert "`z.py`" in ringkas and "RECOVERED" in ringkas


# ============================================================ 2. pure gate rule

def test_truth_table_klaim_edit_gagal():
    f = guard_rules.klaim_edit_gagal_perlu_ditantang
    assert f(True, [{"path": "a"}], 0, 2) is True
    assert f(False, [{"path": "a"}], 0, 2) is False   # no claim → untouched
    assert f(True, [], 0, 2) is False                 # clean ledger → untouched
    assert f(True, [{"path": "a"}], 2, 2) is False    # quota spent → degrade


# ============================================================ 3. decision gates

def test_klaim_dengan_edit_gagal_dan_file_lain_berubah_ditantang():
    """THE InvoiceFlow M1 regression: a real edit of config.py must NOT
    launder the failed edit of auth.py the claim is about."""
    state = SessionState()
    state.catat_file_berubah("- `config.py` (edit_file)")
    state.catat_edit_sukses("config.py")
    state.catat_edit_gagal("auth.py", "Error: SEARCH block not found")
    assert _evaluasi(state, KLAIM) is Keputusan.TANTANG_EDIT_GAGAL


def test_klaim_edit_gagal_tanpa_file_berubah_mendahului_tantang_biasa():
    """With failures in the ledger the file-naming challenge wins over the
    generic zero-evidence one (same quota, more precise message)."""
    state = SessionState()
    state.catat_edit_gagal("auth.py", "Error: SEARCH block not found")
    assert _evaluasi(state, KLAIM) is Keputusan.TANTANG_EDIT_GAGAL


def test_edit_gagal_yang_pulih_tidak_menantang():
    state = SessionState()
    state.catat_edit_gagal("auth.py", "Error: SEARCH block not found")
    state.catat_edit_sukses("auth.py")
    state.catat_file_berubah("- `auth.py` (edit_file)")
    assert _evaluasi(state, KLAIM) is Keputusan.CHECKPOINT


def test_tanpa_klaim_edit_gagal_tidak_mengubah_keputusan():
    state = SessionState()
    state.catat_edit_gagal("auth.py", "Error: SEARCH block not found")
    assert _evaluasi(state, JAWAB_PANJANG) is Keputusan.JAWAB_NORMAL
    assert _evaluasi(state, PENDEK, iterasi=0) is Keputusan.TERUSKAN


def test_kuota_habis_edit_gagal_degradasi_ke_checkpoint_dengan_ledger():
    state = SessionState()
    state.verifikasi_count = 2
    state.catat_edit_gagal("auth.py", "Error: SEARCH block not found")
    state.catat_file_berubah("- `config.py` (edit_file)")
    assert _evaluasi(state, KLAIM) is Keputusan.CHECKPOINT
    assert "NEVER SUCCEEDED" in state.ringkasan_ledger()  # honesty moves to the report


def test_kuota_habis_edit_gagal_tanpa_file_jawab_normal():
    state = SessionState()
    state.verifikasi_count = 2
    state.catat_edit_gagal("auth.py", "boom")
    assert _evaluasi(state, KLAIM) is Keputusan.JAWAB_NORMAL


def test_edit_gagal_mendahului_nudge_todo():
    """Honesty before continuation: pending todos do not deflect the
    challenge while the ledger holds unrecovered failures."""
    state = SessionState()
    state.catat_file_berubah("- `a.py` (write_file)")
    state.catat_edit_gagal("auth.py", "boom")
    assert _evaluasi(state, KLAIM, sisa_todo=TODO_PENDING) is Keputusan.TANTANG_EDIT_GAGAL


def test_klaim_edit_gagal_di_iterasi_pertama():
    state = SessionState()
    state.catat_edit_gagal("auth.py", "boom")
    assert _evaluasi(state, KLAIM, iterasi=0) is Keputusan.TANTANG_EDIT_GAGAL


# ============================================================ 4. agent-loop E2E

class _ContextManagerPalsu:
    def __init__(self):
        self.messages: list = []

    def get_history(self) -> list:
        # replay what was stored so the loop's api_messages stay valid
        return [
            {k: v for k, v in m.items()}
            for m in self.messages
        ]

    def add_message(self, role, content, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    def get_project_map(self) -> str:
        return "Project Structure:\n📁 src/x.py"

    def set_project_map(self, project_map: str):
        pass


class _ToolExecutorPalsu:
    """edit_file fails on the first attempt, succeeds on the retry."""

    def __init__(self, project_path):
        self.project_path = str(project_path)
        self.panggilan = 0

    def edit_file(self, file_path, diff):
        self.panggilan += 1
        if self.panggilan == 1:
            return "Error: SEARCH block not found in the original file."
        return f"Success: edited file '{file_path}'. 1 block(s) replaced."


def _respon_teks(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=content, tool_calls=None, reasoning_content=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="mock-model",
    )


def _respon_tool(*calls):
    tool_calls = [
        SimpleNamespace(id=call_id, function=SimpleNamespace(
            name=name, arguments=json.dumps(args)))
        for call_id, name, args in calls
    ]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="", tool_calls=tool_calls, reasoning_content=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="mock-model",
    )


def _frame(f):
    return json.loads(f[len("data: "):])


def test_loop_edit_gagal_diklaim_lalu_pulih_checkpoint_berledger(tmp_path):
    """Failed edit + claim → challenge naming the file → real retry →
    checkpoint whose report carries the ledger summary (RECOVERED)."""
    skrip = [
        _respon_tool(("c1", "edit_file", {"file_path": "src/x.py", "diff": "x"})),
        _respon_teks(KLAIM_ID),
        _respon_tool(("c2", "edit_file", {"file_path": "src/x.py", "diff": "y"})),
        _respon_teks(KLAIM_ID),
    ]
    indeks = {"i": 0}

    async def llm_palsu(**kwargs):
        r = skrip[indeks["i"]]
        indeks["i"] += 1
        return r

    from app.services import sse

    executor = _ToolExecutorPalsu(tmp_path)
    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=executor,
        llm_call=llm_palsu,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 10, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_claim_ledger"),
        context_message={"role": "user", "content": "Current project structure:\n📁 src/x.py"},
        lampiran_desc="",
    )

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = [_frame(f) for f in asyncio.run(_kumpul())]
    types = [f["type"] for f in frames]
    statuses = [f["text"] for f in frames if f["type"] == "status"]
    tokens = [f["text"] for f in frames if f["type"] == "token"]

    # the failed attempt is visible in the UI layer, not just the ledger
    assert "❌ File src/x.py NOT edited (edit failed)" in statuses
    # the challenge names the failed file
    assert any(
        "Verifying completion claim (failed edits: `src/x.py`)" in s
        for s in statuses
    ), statuses
    # the retry happened and the run ended on a checkpoint
    assert executor.panggilan == 2
    assert types.count("done") == 1 and types[-1] == "done"
    assert "ask_user" in types
    # the checkpoint report carries the ledger summary with the recovery
    checkpoint = next(t for t in tokens if "TASK COMPLETED" in t)
    assert "EDIT LEDGER" in checkpoint
    assert "RECOVERED" in checkpoint and "src/x.py" in checkpoint
    # the challenge message stored in history names the file too
    reject = [
        m for m in deps.context_manager.messages
        if m["role"] == "user" and isinstance(m.get("content"), str)
        and "YOUR CLAIM IS REJECTED" in m["content"]
    ]
    assert reject and "src/x.py" in reject[0]["content"]


def test_loop_edit_gagal_tanpa_pemulihan_ditantang_dua_kali(tmp_path):
    """Quota behaviour end-to-end: an edit that keeps failing challenges the
    claim twice (ORION_MAX_VERIFIKASI default 2), then the run ends as a
    normal answer — the gate can never become a loop."""
    class _SelaluGagal(_ToolExecutorPalsu):
        def edit_file(self, file_path, diff):
            self.panggilan += 1
            return "Error: SEARCH block not found in the original file."

    skrip = [
        _respon_tool(("c1", "edit_file", {"file_path": "src/x.py", "diff": "x"})),
        _respon_teks(KLAIM_ID),
        _respon_teks(KLAIM_ID),
        _respon_teks(KLAIM_ID),
    ]
    indeks = {"i": 0}

    async def llm_palsu(**kwargs):
        r = skrip[indeks["i"]]
        indeks["i"] += 1
        return r

    from app.services import sse

    executor = _SelaluGagal(tmp_path)
    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=executor,
        llm_call=llm_palsu,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 10, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_claim_ledger"),
        context_message={"role": "user", "content": "Current project structure:\n📁 src/x.py"},
        lampiran_desc="",
    )

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = [_frame(f) for f in asyncio.run(_kumpul())]
    types = [f["type"] for f in frames]
    statuses = [f["text"] for f in frames if f["type"] == "status"]

    tantangan = [s for s in statuses if "failed edits: `src/x.py`" in s]
    assert len(tantangan) == 2, tantangan          # quota = 2, then degrade
    assert "ask_user" not in types                 # no checkpoint: nothing succeeded
    assert types.count("done") == 1 and types[-1] == "done"


def test_loop_klaim_tanpa_edit_ditantang_sekali_answer_first(tmp_path):
    """UPDATE-023 soft challenge, end-to-end: a pure Q&A-turn claim (zero
    edits, empty ledger) is corrected ONCE with the answer-first message;
    the second claim passes through as a normal answer. The old double
    numbered ritual must never come back."""
    skrip = [
        _respon_teks(KLAIM_ID),
        _respon_teks(
            "Semua pemeriksaan sudah selesai dan hasilnya baik; "
            "silakan tinjau lalu beri tahu langkah berikutnya."
        ),
    ]
    indeks = {"i": 0}

    async def llm_palsu(**kwargs):
        r = skrip[indeks["i"]]
        indeks["i"] += 1
        return r

    from app.services import sse

    executor = _ToolExecutorPalsu(tmp_path)
    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=executor,
        llm_call=llm_palsu,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 10, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_claim_ledger"),
        context_message={"role": "user", "content": "Current project structure:\n📁 src/x.py"},
        lampiran_desc="",
    )

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = [_frame(f) for f in asyncio.run(_kumpul())]
    types = [f["type"] for f in frames]
    statuses = [f["text"] for f in frames if f["type"] == "status"]

    assert statuses.count("🔍 Verifying completion claim (0 files changed)...") == 1
    assert types.count("done") == 1 and types[-1] == "done"
    assert "token" in types                     # the second claim answers normally
    assert executor.panggilan == 0              # no edits happened at all
    suntikan = [
        m for m in deps.context_manager.messages
        if m["role"] == "user" and isinstance(m.get("content"), str)
        and "EVIDENCE CHECK" in m["content"]
    ]
    assert len(suntikan) == 1
    assert "Answer the user's actual question FIRST" in suntikan[0]["content"]
    # the old ritual wording must never return
    assert "State the file name + line number" not in suntikan[0]["content"]
