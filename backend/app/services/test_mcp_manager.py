"""
test_mcp_manager.py — unit tests for the MCP client foundation (UPDATE-014)
============================================================================
Everything here runs on fakes: no subprocess is ever spawned inside pytest
(the real end-to-end path — spawn, initialize, list_tools, call_tool — is
covered by the SSE harness scenarios mcp_ok/mcp_error/mcp_timeout and by
the owner's manual PANDUAN checks).

Guarded contracts:
  * pure helpers: name sanitizing/namespacing, config parsing (forgiving
    for beginners, strict where it matters), OpenAI schema conversion,
    result formatting for BOTH mcp 1.x (camelCase) and 2.x (snake_case)
    result shapes;
  * the failure contract: MCPManager.call() NEVER raises and every failure
    class maps to its [MCP ...] prefix — including the SDK timeout shape
    (McpError -32001 'timed out', which is NOT an asyncio.TimeoutError)
    and connection loss with exactly one transparent reconnect attempt;
  * lifecycle robustness: missing/broken config files degrade to warnings;
  * the agent-loop branch: a namespaced tool call produces the frozen
    'status' frame '🔌 MCP <server>/<tool> ...', merges the MCP schema into
    the provider request, and feeds the [MCP ...]/plain string back as an
    ordinary tool result;
  * mcp_manager.py must never touch the SSE layer (no frames from there).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from app.services import mcp_manager
from app.services.mcp_manager import (
    MCPManager,
    ServerSpec,
    build_openai_tool,
    build_server_spec,
    format_tool_result,
    parse_config,
    public_tool_name,
    sanitize_component,
)


# --------------------------------------------------------------------------- fakes

class FakeText:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeImage:
    type = "image"


class FakeResult2x:
    """mcp 2.x shape: snake_case fields."""

    def __init__(self, text="ok", is_error=False, structured=None, content=None):
        self.content = content if content is not None else (
            [FakeText(text)] if text is not None else []
        )
        self.is_error = is_error
        self.structured_content = structured


class FakeResult1x:
    """mcp 1.x shape: camelCase fields."""

    def __init__(self, text="ok", isError=False, structuredContent=None):
        self.content = [FakeText(text)] if text is not None else []
        self.isError = isError
        self.structuredContent = structuredContent


class FakeSession:
    """call_tool returns queued items (Exception items are raised)."""

    def __init__(self, results=None, exc=None):
        self.calls = []
        self._results = list(results or [])
        self._exc = exc

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls.append((name, arguments, read_timeout_seconds))
        if self._exc is not None:
            raise self._exc
        if self._results:
            item = self._results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResult2x(text="ok")


class LegacySession:
    """mcp 1.x style: read_timeout_seconds MUST be a timedelta."""

    def __init__(self):
        self.used = []

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        if not isinstance(read_timeout_seconds, timedelta):
            raise TypeError("1.x requires timedelta")
        self.used.append(read_timeout_seconds)
        return FakeResult1x(text="legacy ok")


def _runtime(server="demo", tool="echo", session=None, timeout=30.0, status="ok"):
    spec = ServerSpec(name=server, command="python", args=[], env={}, timeout=timeout)
    rt = mcp_manager._ServerRuntime(
        spec=spec, status=status, session=session if session is not None else FakeSession()
    )
    pub = public_tool_name(server, tool)
    rt.tools[pub] = tool
    rt.openai.append(build_openai_tool(pub, "demo tool", {"type": "object", "properties": {}}))
    return rt, pub


def _manager_with_tool(**kwargs):
    mgr = MCPManager()
    rt, pub = _runtime(**kwargs)
    mgr._servers[rt.spec.name] = rt
    mgr._register_tools(rt)
    return mgr, pub


# --------------------------------------------------------------- pure helpers

def test_sanitize_component():
    assert sanitize_component("web.fetch") == "web_fetch"
    assert sanitize_component("a b/c:d") == "a_b_c_d"
    assert sanitize_component("ok-name_1") == "ok-name_1"


def test_public_tool_name_namespace_dan_batas():
    assert public_tool_name("fetch", "get") == "mcp__fetch__get"
    assert public_tool_name("my.srv", "web.fetch") == "mcp__my_srv__web_fetch"
    long = public_tool_name("s" * 80, "t" * 80)
    assert len(long) <= 64
    assert long.startswith("mcp__")


def test_parse_config_valid():
    raw = json.dumps({
        "mcpServers": {
            "demo": {"command": "python", "args": ["srv.py"], "timeoutSeconds": 5},
            "off": {"command": "node", "disabled": True},
        }
    })
    specs, warnings = parse_config(raw)
    assert warnings == []
    assert specs["demo"].command == "python"
    assert specs["demo"].args == ["srv.py"]
    assert specs["demo"].timeout == 5.0
    assert specs["off"].disabled is True


def test_parse_config_rusak_tidak_fatal():
    specs, warnings = parse_config("{bukan json")
    assert specs == {}
    assert any("not valid JSON" in w for w in warnings)

    specs, warnings = parse_config("[1,2]")
    assert specs == {}
    assert any("must be a JSON object" in w for w in warnings)

    specs, warnings = parse_config('{"mcpServers": []}')
    assert specs == {}
    assert any("must be an object" in w for w in warnings)


def test_build_server_spec_varian():
    spec, w = build_server_spec("a", {"command": "python", "args": "solo.py"})
    assert spec.args == ["solo.py"] and any("list of strings" in x for x in w)

    spec, w = build_server_spec("b", {"command": "x", "timeoutSeconds": 5000})
    assert spec.timeout == mcp_manager.DEFAULT_CALL_TIMEOUT
    assert any("outside 1..600" in x for x in w)

    spec, w = build_server_spec("c", {"command": "x", "timeoutSeconds": True})
    assert spec.timeout == mcp_manager.DEFAULT_CALL_TIMEOUT
    assert any("must be a number" in x for x in w)

    spec, w = build_server_spec("d", {"command": "x", "env": "PATH=/bin", "zonk": 1})
    assert spec.env == {} and any("not an object" in x for x in w)
    assert any("unknown config key" in x and "zonk" in x for x in w)

    spec, w = build_server_spec("e", {"args": []})
    assert spec is None and any("command" in x for x in w)

    spec, w = build_server_spec("f", "bukan object")
    assert spec is None and any("JSON object" in x for x in w)


def test_build_openai_tool():
    schema = {"type": "object", "properties": {"t": {"type": "string"}}, "required": ["t"]}
    tool = build_openai_tool("mcp__s__t", "Do it", schema)
    assert tool["function"]["name"] == "mcp__s__t"
    assert tool["function"]["parameters"] is schema
    assert tool["function"]["description"] == "Do it"

    fallback = build_openai_tool("mcp__s__t", "", {"type": "array"})
    assert fallback["function"]["parameters"] == {"type": "object", "properties": {}}
    assert fallback["function"]["description"]  # never empty

    long_desc = build_openai_tool("n", "x" * 5000, None)
    assert len(long_desc["function"]["description"]) == mcp_manager.DESCRIPTION_MAX_LEN


def test_format_tool_result_dua_gaya_versi():
    assert format_tool_result(FakeResult2x(text="hello")) == "hello"
    assert format_tool_result(FakeResult1x(text="hello")) == "hello"

    err = format_tool_result(FakeResult2x(text="boom", is_error=True))
    assert err.startswith(mcp_manager.PREFIX_TOOL_ERROR) and "boom" in err
    err1x = format_tool_result(FakeResult1x(text="boom", isError=True))
    assert err1x.startswith(mcp_manager.PREFIX_TOOL_ERROR)

    # no text content -> structured content (2.x snake / 1.x camel)
    s2 = format_tool_result(FakeResult2x(text=None, structured={"result": 5}))
    assert json.loads(s2) == {"result": 5}
    s1 = format_tool_result(FakeResult1x(text=None, structuredContent={"r": 1}))
    assert json.loads(s1) == {"r": 1}

    # non-text parts become typed placeholders; empty results stay explicit
    mixed = format_tool_result(FakeResult2x(content=[FakeText("a"), FakeImage()]))
    assert mixed == "a\n[non-text content: image]"
    assert format_tool_result(FakeResult2x(text=None)) == "(the tool returned no content)"


# ------------------------------------------------------------ manager: config

def test_start_all_tanpa_file_config(tmp_path, monkeypatch):
    monkeypatch.delenv(mcp_manager.ENV_CONFIG_OVERRIDE, raising=False)
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path)))
    assert summary == {"servers": [], "tools": 0, "warnings": []}
    assert mgr.openai_tools() == []
    assert mgr.health_fields() == {"mcp_servers": [], "mcp_tools": 0}


def test_start_all_config_rusak_hanya_warning(tmp_path, monkeypatch):
    monkeypatch.delenv(mcp_manager.ENV_CONFIG_OVERRIDE, raising=False)
    orion = tmp_path / ".orion"
    orion.mkdir()
    (orion / "mcp.json").write_text("{rusak", encoding="utf-8")
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path)))
    assert summary["servers"] == [] and summary["tools"] == 0
    assert any("not valid JSON" in w for w in summary["warnings"])


def test_start_all_server_disabled_tidak_disentuh(tmp_path, monkeypatch):
    monkeypatch.delenv(mcp_manager.ENV_CONFIG_OVERRIDE, raising=False)
    orion = tmp_path / ".orion"
    orion.mkdir()
    (orion / "mcp.json").write_text(
        json.dumps({"mcpServers": {"d": {"command": "python", "disabled": True}}}),
        encoding="utf-8",
    )
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path)))
    assert summary["servers"] == [{"name": "d", "status": "disabled", "tools": 0}]
    assert mgr.openai_tools() == []


def test_start_all_sdk_tersedia_buruk_hanya_warning(tmp_path, monkeypatch):
    (tmp_path / ".orion").mkdir()
    (tmp_path / ".orion" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "python"}}}), encoding="utf-8"
    )
    monkeypatch.setattr(mcp_manager, "MCP_IMPORT_ERROR", "ModuleNotFoundError: mcp")
    monkeypatch.delenv(mcp_manager.ENV_CONFIG_OVERRIDE, raising=False)
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path)))
    assert summary["servers"] == [] and summary["tools"] == 0
    assert any("mcp package is unavailable" in w for w in summary["warnings"])


def test_override_lokasi_config_lewat_env(tmp_path, monkeypatch):
    alt = tmp_path / "elsewhere.json"
    alt.write_text(
        json.dumps({"mcpServers": {"e": {"command": "python", "disabled": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(mcp_manager.ENV_CONFIG_OVERRIDE, str(alt))
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path / "proyek")))
    assert [s["name"] for s in summary["servers"]] == ["e"]


def test_config_dengan_bom_windows_tetap_terbaca(tmp_path, monkeypatch):
    monkeypatch.delenv(mcp_manager.ENV_CONFIG_OVERRIDE, raising=False)
    """PowerShell 5.1 (Set-Content -Encoding UTF8) and some Notepad modes
    prepend a BOM; json.loads chokes on it. load_config reads utf-8-sig, so
    a BOM'd mcp.json must parse exactly like a clean one (adendum 014,
    observed on the owner's machine)."""
    orion = tmp_path / ".orion"
    orion.mkdir()
    data = json.dumps(
        {"mcpServers": {"bom": {"command": "python", "disabled": True}}}
    )
    (orion / "mcp.json").write_bytes(b"\xef\xbb\xbf" + data.encode("utf-8"))
    mgr = MCPManager()
    summary = asyncio.run(mgr.start_all(str(tmp_path)))
    assert [s["name"] for s in summary["servers"]] == ["bom"]
    assert summary["warnings"] == []


# ------------------------------------------------------------- manager: call

def test_call_sukses_dan_argumen_diteruskan():
    session = FakeSession(results=[FakeResult2x(text="the answer")])
    mgr, pub = _manager_with_tool(session=session)
    out = asyncio.run(mgr.call(pub, {"text": "hi"}))
    assert out == "the answer"
    name, args, timeout = session.calls[0]
    assert name == "echo" and args == {"text": "hi"}
    assert timeout == 30.0


def test_call_arguments_none_jadi_dict_kosong():
    session = FakeSession()
    mgr, pub = _manager_with_tool(session=session)
    asyncio.run(mgr.call(pub, None))
    assert session.calls[0][1] == {}


def test_call_tool_error_diformat_bukan_raise():
    session = FakeSession(results=[FakeResult2x(text="nope", is_error=True)])
    mgr, pub = _manager_with_tool(session=session)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_TOOL_ERROR) and "nope" in out


def test_call_tool_tidak_dikenal():
    mgr, _ = _manager_with_tool()
    out = asyncio.run(mgr.call("mcp__demo__tidakada", {}))
    assert out.startswith(mcp_manager.PREFIX_ERROR) and "not registered" in out


def test_call_timeout_asyncio_dipetakan_ke_mcp_timeout():
    session = FakeSession(exc=asyncio.TimeoutError())
    mgr, pub = _manager_with_tool(session=session)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_TIMEOUT)
    assert "did not answer within 30 seconds" in out


def test_call_timeout_sdk_mcperror_dipetakan_ke_mcp_timeout():
    """Spike E4: the SDK's own read timeout surfaces as McpError -32001
    'timed out' — NOT asyncio.TimeoutError. It must still classify as a
    timeout so the LLM gets the right advice."""

    class FakeMcpError(Exception):
        pass

    session = FakeSession(exc=FakeMcpError("Request 'tools/call' timed out"))
    mgr, pub = _manager_with_tool(session=session)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_TIMEOUT), out


def test_call_error_biasa_tanpa_reconnect():
    session = FakeSession(exc=ValueError("bad arguments shape"))
    mgr, pub = _manager_with_tool(session=session)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_ERROR) and "ValueError" in out


def test_call_koneksi_putus_reconnect_gagal(monkeypatch):
    session = FakeSession(exc=Exception("Connection closed"))
    mgr, pub = _manager_with_tool(session=session)

    async def fail_start(spec):
        return mcp_manager._ServerRuntime(spec=spec, status="error", error="spawn failed")

    monkeypatch.setattr(mgr, "_start_server", fail_start)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_SERVER_DOWN)
    assert "lost its connection" in out and "spawn failed" in out


def test_call_koneksi_putus_reconnect_sukses(monkeypatch):
    session = FakeSession(exc=Exception("Connection closed"))
    mgr, pub = _manager_with_tool(session=session, server="demo", tool="echo")

    async def ok_start(spec):
        rt, _ = _runtime(server=spec.name, tool="echo",
                         session=FakeSession(results=[FakeResult2x(text="after reconnect")]))
        return rt

    monkeypatch.setattr(mgr, "_start_server", ok_start)
    out = asyncio.run(mgr.call(pub, {}))
    assert out == "after reconnect"
    assert mgr.has_tool(pub)  # registry rebuilt by the reconnect
    assert mgr.status_label(pub) == "demo/echo"


def test_call_server_sudah_mati_sebelum_panggilan(monkeypatch):
    mgr, pub = _manager_with_tool(status="error")
    mgr._servers["demo"].session = None
    mgr._servers["demo"].error = "server did not become ready within 15s"

    async def fail_start(spec):
        return mcp_manager._ServerRuntime(spec=spec, status="error", error="still broken")

    monkeypatch.setattr(mgr, "_start_server", fail_start)
    out = asyncio.run(mgr.call(pub, {}))
    assert out.startswith(mcp_manager.PREFIX_SERVER_DOWN) and "not connected" in out


def test_call_tidak_pernah_raise_untuk_error_tak_terduga(monkeypatch):
    mgr, pub = _manager_with_tool()

    async def boom(*a, **k):
        raise RuntimeError("total surprise")

    monkeypatch.setattr(mgr, "_call_once", boom)
    out = asyncio.run(mgr.call(pub, {}))
    assert isinstance(out, str) and out.startswith(mcp_manager.PREFIX_ERROR)


def test_call_tool_compat_fallback_timedelta():
    session = LegacySession()
    res = asyncio.run(mcp_manager._call_tool_compat(session, "echo", {}, 5.0))
    assert format_tool_result(res) == "legacy ok"
    assert session.used == [timedelta(seconds=5.0)]


# --------------------------------------------------------- manager: registry

def test_registrasi_bentrok_antar_server_dilewati_dengan_warning():
    mgr = MCPManager()
    rt1, pub1 = _runtime(server="a.b", tool="t")
    rt2, pub2 = _runtime(server="a_b", tool="t")
    assert pub1 == pub2  # both sanitize to mcp__a_b__t
    mgr._servers["a.b"] = rt1
    mgr._register_tools(rt1)
    mgr._servers["a_b"] = rt2
    mgr._register_tools(rt2)
    assert mgr.has_tool(pub1)
    assert mgr.status_label(pub1) == "a.b/t"  # first server keeps the name
    assert any("already taken" in w for w in mgr._warnings)
    assert len(mgr.openai_tools()) == 1  # the duplicate schema was removed


def test_stop_all_membersihkan_registry():
    mgr, pub = _manager_with_tool()
    asyncio.run(mgr.stop_all())
    assert not mgr.has_tool(pub)
    assert mgr.tool_names() == set()
    assert mgr.openai_tools() == []


def test_summary_dan_health_fields_hanya_server_sehat():
    mgr = MCPManager()
    ok_rt, _ = _runtime(server="good", tool="t1")
    bad_rt, _ = _runtime(server="bad", tool="t2", status="error")
    bad_rt.error = "spawn failed"
    bad_rt.tools = {}
    mgr._servers["good"] = ok_rt
    mgr._servers["bad"] = bad_rt
    mgr._register_tools(ok_rt)
    summary = mgr.summary()
    assert summary["tools"] == 1
    assert {s["name"]: s["status"] for s in summary["servers"]} == {"good": "ok", "bad": "error"}
    health = mgr.health_fields()
    assert health == {"mcp_servers": ["good"], "mcp_tools": 1}


# ------------------------------------------------- agent-loop integration

class _ContextManagerPalsu:
    def __init__(self):
        self.messages = []

    def get_history(self):
        return []

    def add_message(self, role, content, **kwargs):
        self.messages.append({"role": role, "content": content, **kwargs})

    def get_project_map(self):
        return "Project Structure:\n📁 demo.txt"

    def set_project_map(self, project_map):
        pass


def test_agent_loop_cabang_mcp_frame_status_dan_skema(tmp_path):
    """One namespaced tool call must: merge the MCP schema into the provider
    request, emit exactly one frozen 'status' frame with the 🔌 MCP label,
    pass the parsed arguments to manager.call(), and feed the returned string
    back as an ordinary tool result (then finish with token+done)."""
    from app.services import sse
    from app.services.agent_loop import Deps, aliran_agent
    from app.services.session_state import SessionState

    terlihat = {}

    class FakeMcp:
        def has_tool(self, name):
            return name == "mcp__fake__echo"

        def status_label(self, name):
            return "fake/echo"

        def tool_names(self):
            return {"mcp__fake__echo"}

        def openai_tools(self):
            return [{
                "type": "function",
                "function": {"name": "mcp__fake__echo", "description": "d",
                             "parameters": {"type": "object", "properties": {}}},
            }]

        async def call(self, name, args):
            terlihat["call"] = (name, args)
            return "MCP_RESULT_OK"

    tc = SimpleNamespace(
        id="call_x",
        function=SimpleNamespace(name="mcp__fake__echo", arguments='{"text":"hi"}'),
    )
    resp_tool = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[tc], reasoning_content=None))],
        usage=None, model="mock",
    )
    final_text = (
        "This is the final harness answer for the MCP branch test, long "
        "enough to pass the short-reply gate without any claim keyword."
    )
    resp_final = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=final_text, tool_calls=None, reasoning_content=None))],
        usage=None, model="mock",
    )
    antrean = [resp_tool, resp_final]

    async def llm_palsu(**kwargs):
        terlihat.setdefault("tools", []).append(
            [t["function"]["name"] for t in kwargs.get("tools") or []]
        )
        return antrean.pop(0)

    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=SimpleNamespace(project_path=str(tmp_path)),
        llm_call=llm_palsu,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={"max_iterations": 5, "token_budget_threshold": 999999},
        logger=logging.getLogger("test_mcp_manager"),
        context_message={"role": "user", "content": "Current project structure:\n📁 demo.txt"},
        lampiran_desc="",
        mcp=FakeMcp(),
    )

    async def _kumpul():
        agen = aliran_agent(SessionState(), deps)
        try:
            return [frame async for frame in agen]
        finally:
            await agen.aclose()

    frames = asyncio.run(_kumpul())

    assert terlihat["call"] == ("mcp__fake__echo", {"text": "hi"})
    assert "mcp__fake__echo" in terlihat["tools"][0]  # schema merged on turn 1
    status_mcp = [f for f in frames if '"type": "status"' in f and "MCP fake/echo ..." in f]
    assert len(status_mcp) == 1, frames
    assert any('"type": "token"' in f and final_text in f for f in frames)
    assert frames[-1] == 'data: {"type": "done"}\n\n'
    # the tool result string reached the history as an ordinary tool message
    assert any(
        m["role"] == "tool" and m["content"] == "MCP_RESULT_OK"
        for m in deps.context_manager.messages
    )


def test_deps_tanpa_mcp_tetap_valid(tmp_path):
    """Deps.mcp defaults to None — every pre-014 construction site works."""
    from app.services.agent_loop import Deps
    from app.services import sse

    deps = Deps(
        context_manager=_ContextManagerPalsu(),
        todo_manager=None,
        tool_executor=SimpleNamespace(project_path=str(tmp_path)),
        llm_call=None,
        kirim_status=lambda teks: sse.ev('status', text=teks),
        config={},
        logger=None,
        context_message={},
    )
    assert deps.mcp is None


# ------------------------------------------------------------ source guards

def test_mcp_manager_tidak_pernah_menyentuh_lapisan_sse():
    src = Path(mcp_manager.__file__).read_text(encoding="utf-8")
    assert "sse.ev(" not in src
    assert "from app.services import sse" not in src
    assert 'data: {"type"' not in src


# ------------------------------------------------------- route-level: /health

def test_health_route_memiliki_field_mcp_aditif(monkeypatch, tmp_path):
    """/health keeps every 013 identity field and gains the additive MCP
    fields; with no project configured they report an empty, honest state."""
    from fastapi.testclient import TestClient

    from app.services.auth import HEADER, TOKEN_ENV

    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    monkeypatch.setenv("ORION_PID_FILE", str(tmp_path / "orion_backend.pid"))
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/health", headers={HEADER: "s3cret"})
        assert r.status_code == 200
        body = r.json()
        # 013 identity fields stay (backendManager.ts parses them)
        for key in ("app", "version", "pid", "started_at", "backend_path",
                    "project_path", "python_executable", "python_prefix"):
            assert key in body
        # 014 additive fields
        assert isinstance(body["mcp_servers"], list)
        assert body["mcp_tools"] == 0
