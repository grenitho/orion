"""
test_tool_guards.py — UPDATE-025 hotfix-2, the shared pre-call gate
===================================================================
External review note (Sam Vallad33, 2026-10-28): the mcp__ dispatch
branch sits before the tool_executor guard, so an MCP tool that writes
or deletes gets a straighter path than Orion's own screened tools.
tool_guards.pre_call_check is the ONE function both branches pass
through; today it is logging-first (a notice string for mutating-
looking MCP names, '' everywhere else) and must never raise.
"""

from __future__ import annotations

from app.services.tool_guards import nama_terlihat_mutating, pre_call_check


def test_mcp_mutating_menghasilkan_notice():
    notice = pre_call_check("mcp__fs__delete_file", {"path": "x"}, mcp=True)
    assert notice.startswith("[ORION PRE-CALL NOTICE]")
    assert "mcp__fs__delete_file" in notice
    assert "do NOT screen" in notice
    for nama in (
        "mcp__db__run_query", "mcp__ci__update_build", "mcp__fs__write_file",
        "mcp__sys__kill_process", "mcp__repo__create_branch",
    ):
        assert pre_call_check(nama, {}, mcp=True), nama


def test_mcp_readonly_tanpa_notice():
    for nama in ("mcp__fs__read_file", "mcp__docs__search", "mcp__time__now"):
        assert pre_call_check(nama, {}, mcp=True) == "", nama


def test_builtin_selalu_kosong_parity_point():
    # Built-ins keep their teeth inside tool_executor; the shared call
    # site returns '' so the stream stays byte-identical (harness).
    assert pre_call_check("delete_file", {"path": "x"}, mcp=False) == ""
    assert pre_call_check("run_terminal", {"command": "rm -rf /"}, mcp=False) == ""


def test_tidak_pernah_raise():
    assert isinstance(pre_call_check(None, object(), mcp=True), str)
    assert isinstance(pre_call_check("x", None, mcp=True), str)
    assert nama_terlihat_mutating("") is False
    assert nama_terlihat_mutating(None) is False
