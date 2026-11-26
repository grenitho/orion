"""
test_agent_loop_attach_guard.py — regression for the 2026-10-25 png crash
==========================================================================
Owner incident: attaching an IMAGE made the last user message's content a
vision-parts LIST; the short-reply anti-amnesia guard then concatenated its
reminder string onto it (``user_content + reminder``) -> TypeError
"can only concatenate list (not 'str') to list" and the turn died with an
error frame. Text attachments never hit it (their content stays a string),
which is exactly why the owner's txt test passed and the png test crashed.
The guard only fires when the previous assistant reply looks like a
question/options — the owner's previous reply contained the keyword
"atau ", so it fired.

Pinned here: with image parts injected and an options-style previous
assistant reply, aliran_agent completes; the reminder is folded INTO the
leading text part (or a new one is added), never concatenated onto a list.
The string-content path keeps its exact old behaviour.
"""

from __future__ import annotations

import asyncio

from app.services.agent_loop import Deps, aliran_agent
from app.services.session_state import SessionState

PNG_PART = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
}


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


class _TE:
    project_path = "/tmp/proyek-uji"


class _Resp:
    def __init__(self, content):
        class M:
            pass

        class C:
            pass

        m = M()
        m.content = content
        m.tool_calls = None
        c = C()
        c.message = m
        self.choices = [c]


def _jalankan(history, pending_image):
    """Run one turn; return (frames, messages_seen_by_llm)."""
    seen = []

    async def fake_llm(messages, tools=None, **kwargs):
        seen.append(messages)
        return _Resp(
            "This is a plain factual answer of adequate length with no "
            "claim keywords and no tool calls at all."
        )

    deps = Deps(
        context_manager=_CM(history),
        todo_manager=None,
        tool_executor=_TE(),
        llm_call=fake_llm,
        kirim_status=lambda teks: f"STATUS:{teks}",
        config={"max_iterations": 5, "token_budget_threshold": 12000},
        logger=None,
        context_message={"role": "user", "content": "Current project structure: x"},
    )
    state = SessionState(pending_image=pending_image)

    frames = []

    async def _run():
        async for f in aliran_agent(state, deps):
            frames.append(f)

    asyncio.run(_run())
    return frames, seen


HISTORY_PEMICU = [
    {"role": "user", "content": "halo"},
    {
        "role": "assistant",
        "content": "Halo! Silakan sampaikan instruksi atau tugas selanjutnya "
        "yang ingin dikerjakan.",  # contains the 'atau ' option keyword
    },
    {"role": "user", "content": "tentang apa ini?"},  # short -> guard window
]


def test_guard_dengan_vision_parts_tidak_crash_dan_reminder_masuk_text_part():
    parts = [{"type": "text", "text": "tentang apa ini?"}, PNG_PART]
    frames, seen = _jalankan([dict(m) for m in HISTORY_PEMICU], parts)

    assert frames, "loop must produce frames"
    assert not any("can only concatenate" in f for f in frames)
    assert seen, "llm must be called"
    last_user = [m for m in seen[0] if m["role"] == "user"][-1]
    content = last_user["content"]
    assert isinstance(content, list), "vision parts must reach the llm"
    teks = [p for p in content if p.get("type") == "text"]
    assert teks and "[END OF REMINDER]" in teks[0]["text"]
    assert any(p is PNG_PART or p == PNG_PART for p in content), "image part kept"


def test_guard_tanpa_reminder_bila_asisten_bukan_pertanyaan():
    hist = [
        {"role": "user", "content": "halo"},
        {
            "role": "assistant",
            "content": "Cuaca hari ini cerah dan laporan telah disusun dengan "
            "rapi sejak pagi tadi, tanpa bentuk pertanyaan apa pun.",
        },
        {"role": "user", "content": "tentang apa ini?"},
    ]
    parts = [{"type": "text", "text": "tentang apa ini?"}, PNG_PART]
    frames, seen = _jalankan(hist, parts)
    last_user = [m for m in seen[0] if m["role"] == "user"][-1]
    teks = [p for p in last_user["content"] if p.get("type") == "text"]
    assert teks and "[END OF REMINDER]" not in teks[0]["text"]


def test_guard_string_tetap_perilaku_lama():
    """No attachments: the reminder still concatenates onto the string."""
    frames, seen = _jalankan([dict(m) for m in HISTORY_PEMICU], None)
    last_user = [m for m in seen[0] if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], str)
    assert "[END OF REMINDER]" in last_user["content"]
