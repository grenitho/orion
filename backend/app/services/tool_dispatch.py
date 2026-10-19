"""
tool_dispatch.py — dispatch table for the "simple" tools (Phase B of the main.py split)
========================================================================================
Before this module existed, 22 tools were handled by an `elif func_name == ...`
chain hundreds of lines long inside the stream generator. Most branches had
only the shape: send status -> call executor method -> send completion status.
This module moves those 14 pure branches into a TABLE, so that:

  * main.py only keeps the genuinely complex and interactive branches
    (ask_user, update_todos, write_file, edit_file, run_terminal,
    read_file/read_file_range with their redundancy guards, git_diff);
  * adding a simple tool later means adding one table row, not touching the
    stream generator;
  * the UI status labels live in one readable, testable place.

Tools that must NOT enter this table: ones with guards, confirmation flows,
heartbeats, or control-flow effects. Those rules are enforced in
test_tool_dispatch.py.
"""

from __future__ import annotations

from typing import Callable, Optional

Label = Callable[[dict], str]

# tool name -> (label_start(args), label_finish(args) | None)
TABLE: dict[str, tuple[Label, Optional[Label]]] = {
    "list_files": (
        lambda a: f"📂 Reading folder structure: {a.get('path', '.')}",
        lambda a: "✅ Folder structure read",
    ),
    "search_code": (
        lambda a: f"🔍 Searching: {str(a.get('query', ''))[:60]}",
        lambda a: "✅ Search complete",
    ),
    "read_json_path": (
        lambda a: f"📋 Reading JSON path: {a.get('json_path', '')} from {a.get('file_path', '')}",
        lambda a: "✅ JSON path read",
    ),
    "web_search": (
        lambda a: f"🌐 Searching the web: {str(a.get('query', ''))[:60]}",
        lambda a: "✅ Web search complete",
    ),
    "web_fetch": (
        lambda a: f"📄 Fetching page: {str(a.get('url', ''))[:70]}",
        lambda a: "✅ Page fetched",
    ),
    "verify_code": (lambda a: f"🩺 Verifying: {a.get('file_path', '')}", None),
    "verify_html": (lambda a: f"🩺 Verifying HTML: {a.get('file_path', '')}", None),
    "run_tests": (lambda a: "🧪 Running test suite...", None),
    "git_status": (lambda a: "🔀 Checking git status...", None),
    "git_commit": (lambda a: "🔒 Creating git checkpoint...", None),
    "list_backups": (lambda a: "🗄️ Listing available backups...", None),
    "restore_backup": (lambda a: f"♻️ Restoring: {a.get('file_path', '')}", None),
    "delete_file": (
        lambda a: f"🗑️ Deleting (with backup first): {a.get('file_path', '')}", None),
    "move_file": (
        lambda a: f"📦 Moving: {a.get('from_path', '')} -> {a.get('to_path', '')}", None),
}

# For these 14 entries, tool name == method name on ToolExecutor.
# If that ever diverges, just change this dict.
METHOD: dict[str, str] = {nama: nama for nama in TABLE}

# Tools that MUST stay handled by main.py because they have guards/interaction.
# Listed explicitly so this decision is readable, not implicit.
WAJIB_DI_MAIN = {
    "ask_user",        # stops the stream, waits for the user's answer
    "update_todos",    # touches todo_manager + the todo_update event
    "read_file",       # analyzed_files redundancy guard
    "read_file_range", # analyzed_files redundancy guard
    "write_file",      # permission_manager + post_edit_verifier
    "edit_file",       # permission_manager + post_edit_verifier
    "run_terminal",    # confirmation modal + heartbeat + kill tree
    "git_diff",        # special handling of large output
}


def tool_sederhana(nama: str) -> bool:
    return nama in TABLE


def label(nama: str, args: dict) -> tuple[str, Optional[str]]:
    mulai, selesai = TABLE[nama]
    return mulai(args or {}), (selesai(args or {}) if selesai else None)


def jalankan(nama: str, executor, args: dict):
    method = getattr(executor, METHOD[nama])
    return method(**(args or {}))
