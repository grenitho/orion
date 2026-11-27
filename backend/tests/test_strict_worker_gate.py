import asyncio
from types import SimpleNamespace
from app.services import agent_loop, subagent_manager
from app.services.session_state import SessionState


def test_strict_worker_mode_strips_direct_write_tools_from_main_agent(tmp_path):
    captured_tools = []

    async def fake_llm(messages, tools=None):
        captured_tools.append([t["function"]["name"] for t in (tools or [])])
        msg = SimpleNamespace(content="Siap mendelegasikan.", tool_calls=None, reasoning_content=None)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage, model="test-model")

    class DummyCtx:
        def get_history(self):
            return [{"role": "user", "content": "ubah file app/models.py"}]
        def get_project_map(self):
            return ""
        def add_message(self, *a, **kw):
            pass

    class DummyExec:
        project_path = str(tmp_path)

    state = SessionState()
    deps = agent_loop.Deps(
        context_manager=DummyCtx(),
        todo_manager=None,
        tool_executor=DummyExec(),
        llm_call=fake_llm,
        kirim_status=lambda s: f"data: {s}\n\n",
        config={"strict_worker_coding": True, "max_iterations": 3},
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, exception=lambda *a, **k: None),
        context_message={"role": "user", "content": "Current project structure"},
    )

    async def _run():
        frames = []
        async for ev in agent_loop.aliran_agent(state, deps):
            frames.append(ev)
        return frames

    asyncio.run(_run())
    assert captured_tools, "LLM tidak terpanggil"
    assert "write_file" not in captured_tools[0]
    assert "edit_file" not in captured_tools[0]
    assert "delegate_workers" in captured_tools[0]
    assert "write_file" in subagent_manager.WORKER_TOOLS
    assert "edit_file" in subagent_manager.WORKER_TOOLS
