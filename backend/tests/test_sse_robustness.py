"""
test_sse_robustness.py — the three SSE hardening fixes (UPDATE-013 scope e)
============================================================================
Each test pins one behaviour that the SSE harness review found fragile:

  1. the no-project /chat early-exit MUST end with a terminal 'done'
     (it used to end after 'error' — the only /chat path that bypassed
     jamin_done — so the extension showed a false "connection lost");
  2. an LLM answer with an empty `choices` list must produce a clear
     'error' event instead of an IndexError mid-stream;
  3. when the consumer disconnects while an LLM call is in flight, the
     call must be CANCELLED — no orphan task burning quota in the
     background after VS Code closed the stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace

from app.services import sse
from app.services.agent_loop import Deps, aliran_agent
from app.services.auth import HEADER, TOKEN_ENV
from app.services.session_state import SessionState


# --------------------------------------------------------------------------- fakes

class _ContextManagerPalsu:
    """Minimal stand-in: empty history, records messages, never persists."""

    def __init__(self):
        self.messages: list = []

    def get_history(self) -> list:
        return []

    def add_message(self, role, content, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    def get_project_map(self) -> str:
        return "Project Structure:\n📁 demo.txt"

    def set_project_map(self, project_map: str):
        pass


def _deps(llm_call, tmp_path) -> Deps:
    return Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=SimpleNamespace(project_path=str(tmp_path)),
        llm_call=llm_call,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 5, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_sse_robustness"),
        context_message={"role": "user", "content": "Current project structure:\n📁 demo.txt"},
        lampiran_desc="",
    )


# ------------------------------------------------------------------- fix 1: done

def test_chat_tanpa_proyek_berakhir_dengan_done(monkeypatch, tmp_path):
    """Route-level: 'Please set a project first.' must be followed by done."""
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    from fastapi.testclient import TestClient

    from app import main as main_module

    monkeypatch.setattr(main_module, "tool_executor", None)
    monkeypatch.setattr(main_module.context_manager, "project_map", "")

    with TestClient(main_module.app) as client:
        r = client.post("/chat", json={"message": "hai"}, headers={HEADER: "s3cret"})
        assert r.status_code == 200
        body = r.text
        assert '"type": "error"' in body
        assert "Please set a project first." in body
        assert '"type": "done"' in body, (
            "the no-project stream must end with done — without it the "
            "extension reports a false 'connection lost' (ORION-12 logic)"
        )
        assert body.index('"type": "error"') < body.index('"type": "done"')


# ------------------------------------------------------- fix 2: empty choices

def test_choices_kosong_melaporkan_error_bukan_crash(tmp_path):
    async def llm_kosong(**kwargs):
        return SimpleNamespace(choices=[], usage=None, model="mock-model")

    async def _kumpul():
        agen = aliran_agent(SessionState(), _deps(llm_kosong, tmp_path))
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = asyncio.run(_kumpul())
    types = [f for f in frames if '"type": "error"' in f]
    assert types, f"expected an error frame, got: {frames}"
    assert "empty response" in types[0]
    # no IndexError escaped; the loop ended after the error frame
    assert frames[-1] == types[0]


# --------------------------------------------------- fix 3: cancel the in-flight call

def test_tugas_llm_dibatalkan_saat_konsumen_putus(tmp_path):
    """Simulate the client disconnecting while the LLM call is in flight:
    the in-flight task must be cancelled, not orphaned."""

    async def _main():
        mulai = asyncio.Event()
        dibatalkan = asyncio.Event()

        async def llm_gantung(**kwargs):
            mulai.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                dibatalkan.set()
                raise
            return SimpleNamespace(choices=[], usage=None, model="mock-model")

        agen = aliran_agent(SessionState(), _deps(llm_gantung, tmp_path))

        async def konsumen():
            async for _frame in agen:
                pass

        tugas = asyncio.create_task(konsumen())
        await asyncio.wait_for(mulai.wait(), timeout=5)
        await asyncio.sleep(0.05)  # generator pasti sudah masuk asyncio.wait
        tugas.cancel()  # == yang dilakukan uvicorn saat klien memutus koneksi
        with contextlib.suppress(asyncio.CancelledError):
            await tugas
        await asyncio.sleep(0.05)
        with contextlib.suppress(Exception):
            await agen.aclose()
        assert dibatalkan.is_set(), (
            "the in-flight LLM call was NOT cancelled when the consumer "
            "disconnected — it would keep running orphaned in the background"
        )

    asyncio.run(_main())
