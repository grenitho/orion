"""
test_soft_limit_reanchor.py — UPDATE-025 hotfix regressions (backend 0.10.5)
============================================================================
Field incident (InvoiceFlow, owner transcript + backend log 2026-10-28):
the user asked "saya mau semua free/gratis. mana pilihan yang tepat?".
A stale todo list from an earlier session was still pending in the
project's .orion/todo_list.json ("Perbaiki presisi perkalian line_total
... menggunakan Decimal", in_progress). The log shows the turn being
taken over:

    08:36:49 [CLAIM WITHOUT EVIDENCE] soft one-shot challenge
    08:38:19 [ORION NUDGE 1/10] 4 todos still pending. Continuing with [1]

The forced continuation nudge pushed the model into the OLD Decimal
work; at iteration 24/30 the soft limit paused the turn emitting the
literal template placeholder "[mention the last task]" (the backend
composes that message itself — nobody ever filled the placeholder in,
in any language, since the feature was born). The same blind text was
stored into the history, so when the owner typed "lanjut" nothing
anchored the model to the unanswered question and it reported the
Decimal work instead. The owner had to scold it back to the topic.

Pinned here (backend 0.10.5):
  1. the soft-limit warning quotes the turn's ACTUAL request, names the
     last executed tool and the pending todos, and NEVER contains the
     old placeholder;
  2. the copy stored in history carries the same anchor (that is what
     the "lanjut" resume turn reads);
  3. a pending todo that existed BEFORE the turn triggers a RE-ANCHOR
     notice (answer the current request first, surface the old todos,
     ask) instead of the forced "[SYSTEM - CONTINUE]" injection;
  4. a todo created DURING the turn still gets the original forced
     continuation — the PATCH ORION-1 "only does the first task"
     protection is preserved byte-identically;
  5. the two pure helpers behave on edge cases ([SYSTEM messages,
     vision-parts lists, long/odd tool args).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.agent_loop import (
    Deps,
    _catatan_tool,
    _permintaan_pengguna_terakhir,
    aliran_agent,
)
from app.services.session_state import SessionState


# --------------------------------------------------------------------------
# Fakes (same shape as test_agent_loop_attach_guard.py)
# --------------------------------------------------------------------------
class _CM:
    """Minimal ContextManager stand-in (history + project map only)."""

    def __init__(self, history):
        self.history = history

    def get_history(self):
        return self.history

    def add_message(self, role, content, **kwargs):
        self.history.append({"role": role, "content": content})

    def get_project_map(self):
        return "Project Structure:\n📁 README.md"

    def save_history(self):
        pass


class _TM:
    """Minimal TodoManager stand-in."""

    def __init__(self, todos=None):
        self.todos = list(todos or [])

    def get_todos(self):
        return self.todos

    def set_todos(self, todos):
        cleaned = []
        for i, item in enumerate(todos or []):
            cleaned.append(
                {
                    "id": str(item.get("id", i + 1)),
                    "text": str(item.get("text", "")),
                    "status": item.get("status", "pending"),
                }
            )
        self.todos = cleaned
        return self.render_markdown()

    def render_markdown(self):
        if not self.todos:
            return "_(Todo list is empty)_"
        return "\n".join(
            f"- [{t['id']}] {t['text']} ({t['status']})" for t in self.todos
        )


class _TE:
    project_path = "/tmp/proyek-uji"


class _L:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _Resp:
    def __init__(self, content=None, tool_calls=None):
        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
        )
        self.choices = [SimpleNamespace(message=message)]


def _tool_call(name, arguments, tc_id="tc_1"):
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _LLM:
    """Scripted fake LLM: replays responses in order, sticks on the last."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, messages, tools=None, **kwargs):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


def _jalankan(history, *, responses, todos=None, max_iter=5, state=None):
    """Run one turn; return (frames, state, deps)."""
    deps = Deps(
        context_manager=_CM(history),
        todo_manager=_TM(todos) if todos is not None else None,
        tool_executor=_TE(),
        llm_call=responses,
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": max_iter, "token_budget_threshold": 12000},
        logger=_L(),
        context_message={"role": "user", "content": "Current project structure: x"},
    )
    st = state if state is not None else SessionState()
    frames = []

    async def _run():
        async for f in aliran_agent(st, deps):
            frames.append(f)

    asyncio.run(_run())
    return frames, st, deps


# --------------------------------------------------------------------------
# 1 + 2: the soft-limit warning is composed from REAL state
# --------------------------------------------------------------------------
PERMINTAAN = "saya mau semua free/gratis. mana pilihan yang tepat?"


def test_soft_limit_mengutip_permintaan_turn_dan_tanpa_placeholder():
    history = [{"role": "user", "content": PERMINTAAN}]
    # A short no-claim reply keeps the loop in TERUSKAN until the soft limit.
    frames, st, deps = _jalankan(
        history, responses=_LLM([_Resp("Sebentar, saya cek dulu.")]), max_iter=5
    )
    warning = "".join(f for f in frames if "SOFT LIMIT" in f)
    assert warning, f"soft-limit frame must be emitted; got {frames}"
    # The incident: the raw template placeholder reached the user.
    assert "[mention the last task]" not in warning
    assert "[sebutkan task terakhir]" not in warning.lower()
    # The real request is quoted...
    assert PERMINTAAN in warning
    # ...the status lines exist and degrade honestly...
    assert "(no tool executed yet this turn)" in warning
    assert "Todo list: none" in warning
    # ...and the resume contract is stated.
    assert "the request quoted above comes first" in warning
    # The frozen tail of the pause sequence is untouched.
    assert any(
        "Soft limit reached. Continue, or give new instructions?" in f
        for f in frames
    )
    assert "done" in frames[-1]
    # The history copy (what the "lanjut" turn will read) carries the anchor.
    stored = [
        m["content"]
        for m in deps.context_manager.history
        if m["role"] == "user" and "[SYSTEM WARNING]" in str(m["content"])
    ]
    assert stored and PERMINTAAN in stored[-1]
    assert st.permintaan_turn_ini == PERMINTAAN


def test_soft_limit_menyebut_tool_terakhir_dan_todo_pending():
    history = [{"role": "user", "content": "cek dulu lalu jawab"}]
    tool_resp = _Resp(
        "",
        tool_calls=[
            _tool_call(
                "update_todos",
                '{"todos": [{"id": "1", "text": "Jawab pertanyaan frontend", '
                '"status": "pending"}]}',
            )
        ],
    )
    frames, st, deps = _jalankan(
        history,
        responses=_LLM([tool_resp, _Resp("Ok, lanjut memeriksa.")]),
        todos=[],
        max_iter=5,
    )
    warning = "".join(f for f in frames if "SOFT LIMIT" in f)
    assert warning, frames
    assert "update_todos" in warning          # last action named
    assert "Jawab pertanyaan frontend" in warning  # pending todo listed
    assert "1 pending" in warning
    assert st.last_tool_note.startswith("update_todos")


# --------------------------------------------------------------------------
# 3: a todo that PREDATES the turn re-anchors instead of hijacking
# --------------------------------------------------------------------------
def test_todo_basi_melabuhkan_ulang_ke_permintaan_turn():
    history = [{"role": "user", "content": PERMINTAAN}]
    stale_todos = [
        {
            "id": "1",
            "text": "Perbaiki presisi perkalian line_total dengan Decimal",
            "status": "in_progress",
        },
        {"id": "2", "text": "Jalankan test suite", "status": "pending"},
    ]
    st = SessionState()
    st.catat_file_berubah("app/routers/invoices.py")  # evidence for gate 3
    frames, st, deps = _jalankan(
        history,
        responses=_LLM(
            [
                _Resp("Saya lihat sebentar."),          # _=0: TERUSKAN
                _Resp("Selesai."),                      # _=1: claim -> LANJUT_TODO
                _Resp(
                    "Untuk kebutuhan sepenuhnya gratis, pilihan paling tepat "
                    "adalah React + Vite + Tailwind dengan hosting statis."
                ),                                      # _=2: JAWAB_NORMAL
            ]
        ),
        todos=stale_todos,
        max_iter=8,
        state=st,
    )
    users = [
        str(m["content"])
        for m in deps.context_manager.history
        if m["role"] == "user"
    ]
    reanchor = [c for c in users if "[SYSTEM - RE-ANCHOR]" in c]
    assert reanchor, users
    # The re-anchor names BOTH the stale item and the actual request.
    assert "Perbaiki presisi perkalian line_total" in reanchor[0]
    assert PERMINTAAN in reanchor[0]
    assert "ASK the user whether to resume" in reanchor[0]
    # The forced injection must NOT fire for stale items...
    assert not any("[SYSTEM - CONTINUE]" in c for c in users)
    # ...and the turn still ends by answering (frames contain the answer).
    assert any("React + Vite" in f for f in frames)
    assert any("Re-anchoring to your request" in f for f in frames)


# --------------------------------------------------------------------------
# 4: a todo created THIS turn keeps the original forced continuation
# --------------------------------------------------------------------------
def test_todo_turn_ini_tetap_dipaksa_lanjut():
    history = [{"role": "user", "content": "kerjakan dua perbaikan berikut"}]
    st = SessionState()
    st.catat_file_berubah("a.py")
    frames, st, deps = _jalankan(
        history,
        responses=_LLM(
            [
                _Resp(
                    "",
                    tool_calls=[
                        _tool_call(
                            "update_todos",
                            '{"todos": ['
                            '{"id": "1", "text": "Perbaikan A", "status": "pending"},'
                            '{"id": "2", "text": "Perbaikan B", "status": "pending"}]}',
                        )
                    ],
                ),                              # _=0: todos created THIS turn
                _Resp("Perbaikan A selesai."),  # _=1: claim -> forced continue
                _Resp(
                    "Melanjutkan ke perbaikan B sekarang dengan langkah yang "
                    "jelas dan terukur."
                ),                              # _=2: JAWAB_NORMAL
            ]
        ),
        todos=[],       # empty at turn start -> snapshot is empty
        max_iter=8,
        state=st,
    )
    users = [
        str(m["content"])
        for m in deps.context_manager.history
        if m["role"] == "user"
    ]
    continue_msgs = [c for c in users if "[SYSTEM - CONTINUE]" in c]
    assert continue_msgs, users
    # PATCH ORION-1 protection: the original forced wording, byte-identical.
    assert "Work on item `[1]` NOW" in continue_msgs[0]
    assert "do NOT conclude the task is complete" in continue_msgs[0]
    assert not any("[SYSTEM - RE-ANCHOR]" in c for c in users)
    assert any("Continuing with todo [1]" in f for f in frames)


# --------------------------------------------------------------------------
# 5: the pure helpers on their edge cases
# --------------------------------------------------------------------------
def test_permintaan_pengguna_terakhir_melewati_sistem_dan_vision_parts():
    history = [
        {"role": "user", "content": "pertanyaan asli"},
        {"role": "assistant", "content": "jawaban"},
        {"role": "user", "content": "[SYSTEM WARNING]: soft limit blah"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "pertanyaan dengan gambar"},
                {"type": "image_url", "image_url": {"url": "data:x"}},
            ],
        },
    ]
    assert _permintaan_pengguna_terakhir(history) == "pertanyaan dengan gambar"
    assert _permintaan_pengguna_terakhir(
        [{"role": "user", "content": "[SYSTEM - CONTINUE]: x"}]
    ) == ""
    assert _permintaan_pengguna_terakhir([]) == ""
    assert _permintaan_pengguna_terakhir(None) == ""
    # assistant/system messages never qualify
    assert _permintaan_pengguna_terakhir(
        [{"role": "assistant", "content": "selesai"}]
    ) == ""


def test_catatan_tool_ringkas_dan_deterministik():
    assert _catatan_tool("read_file", {"path": "src/x.py"}) == (
        "read_file path='src/x.py'"
    )
    assert _catatan_tool("list_files", {}) == "list_files"
    assert _catatan_tool("run_tests", None) == "run_tests"
    panjang = _catatan_tool("write_file", {"content": "B" * 500})
    assert "…" in panjang
    assert len(panjang) < 100
    # whitespace normalized -> one line, safe inside the SSE frame
    assert "\n" not in _catatan_tool("edit_file", {"path": "a\nb.py"})
    # deterministic: same input, same output
    assert _catatan_tool("read_file", {"path": "x"}) == _catatan_tool(
        "read_file", {"path": "x"}
    )
