"""
subagent_manager.py — Orion's sub-agent orchestration (UPDATE-016)
===================================================================
Closes the MCP phase: the main agent loop can delegate bounded work to
internal sub-agents. Three roles, one level deep, summaries only:

  * ``explorer``  read-only investigation; returns ONE condensed findings
    report instead of dragging dozens of file reads through the main
    context window;
  * ``verifier``  post-edit critic; runs the real verification tools
    (verify_code/verify_html/run_tests/git_diff/…) and returns a verdict
    plus a concrete defect list — the honesty philosophy of the claim gate
    applied by a second pair of eyes;
  * ``worker``    parallel executors for independent multi-file tasks; each
    worker's WRITE scope is an explicit disjoint file list checked BEFORE
    any subprocess/LLM work starts, and re-checked on every write.

Hard rules (v1, all unit-tested):
  * depth 1 — a sub-agent has no delegation tools, so it cannot spawn;
  * a sub-agent never talks to the user (no ask_user, no confirm_terminal,
    no run_terminal: dangerous commands stay exclusively in the main loop
    where the approval modal lives);
  * sub-agent history is an in-memory MiniContext — session_history.json is
    never touched by a sub-run;
  * budgets: ``subagent_max_per_turn`` delegations per /chat turn (counted
    in SessionState), ``subagent_max_iterations`` LLM rounds per sub-run,
    ``subagent_workers_max`` parallel workers per delegation;
  * NOTHING here raises into the agent loop: every failure (budget, scope
    overlap, dead model, tool crash) becomes an informative
    ``[SUBAGENT ...]`` string, exactly like the MCP failure contract.

Visibility: the agent loop emits frozen 'status' frames around each
delegation (🤖 Sub-agent …); the full report arrives as the ordinary tool
result string, so the seven-event SSE contract is untouched.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

from app.services.tools_schema import TOOLS

# --------------------------------------------------------------------------
# Roles, tool allowances, prompts
# --------------------------------------------------------------------------
EXPLORER = "explorer"
VERIFIER = "verifier"
WORKER = "worker"

READ_TOOLS = frozenset({
    "list_files", "read_file", "read_file_range", "search_code", "read_json_path",
})
VERIFY_TOOLS = READ_TOOLS | {"verify_code", "verify_html", "run_tests", "git_diff", "git_status"}
WORKER_TOOLS = READ_TOOLS | {"write_file", "edit_file"}

ROLE_TOOLSETS = {EXPLORER: READ_TOOLS, VERIFIER: VERIFY_TOOLS, WORKER: WORKER_TOOLS}

ROLE_PROMPTS = {
    EXPLORER: (
        "You are Orion's Explorer sub-agent. Investigate the task using ONLY "
        "read-only tools. Do not modify anything, do not ask the user anything. "
        "Finish with ONE condensed findings report: what exists where, what is "
        "relevant to the task, and what is missing. Keep it under 400 words."
    ),
    VERIFIER: (
        "You are Orion's Verifier sub-agent. Check the claimed state of the code "
        "with REAL tools (verify_code / verify_html / run_tests / git_diff / "
        "reads). Do not modify anything, do not ask the user anything. Finish "
        "with a verdict line (PASS / PASS WITH NOTES / FAIL) followed by a "
        "concrete defect list (file, problem, suggested fix) or 'no defects "
        "found'. Quote tool output where it matters."
    ),
    WORKER: (
        "You are an Orion Worker sub-agent. Implement ONLY the assigned task and "
        "ONLY inside your assigned file scope; writes outside the scope are "
        "rejected by the system. Do not ask the user anything. Finish with a "
        "short report: files changed, what was done, what was left out."
    ),
}

# --------------------------------------------------------------------------
# Tool names & config keys
# --------------------------------------------------------------------------
TOOL_EXPLORER = "delegate_explorer"
TOOL_VERIFIER = "delegate_verifier"
TOOL_WORKERS = "delegate_workers"
SUBAGENT_TOOLS = frozenset({TOOL_EXPLORER, TOOL_VERIFIER, TOOL_WORKERS})

CFG_ENABLED = "subagents_enabled"
CFG_MAX_ITER = "subagent_max_iterations"
CFG_MAX_PER_TURN = "subagent_max_per_turn"
CFG_WORKERS_MAX = "subagent_workers_max"

DEFAULT_MAX_ITER = 6
DEFAULT_MAX_PER_TURN = 3
DEFAULT_WORKERS_MAX = 3

PREFIX_REPORT = "[SUBAGENT REPORT"
PREFIX_BUDGET = "[SUBAGENT BUDGET]"
PREFIX_SCOPE = "[SUBAGENT SCOPE]"
PREFIX_GUARD = "[SUBAGENT GUARD]"
PREFIX_DISABLED = "[SUBAGENTS DISABLED]"
PREFIX_ERROR = "[SUBAGENT ERROR]"

MIN_SUMMARY_LEN = 40  # a shorter 'report' is treated as not-yet-finished


def is_subagent_tool(name: str) -> bool:
    return name in SUBAGENT_TOOLS


def role_of_tool(name: str) -> str:
    return {TOOL_EXPLORER: EXPLORER, TOOL_VERIFIER: VERIFIER, TOOL_WORKERS: WORKER}[name]


def read_config(config: dict | None) -> dict:
    """Clamped sub-agent knobs from orion_config (missing keys = defaults)."""
    config = config or {}

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        raw = config.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, int):
            return default
        return max(lo, min(hi, raw))

    return {
        "enabled": bool(config.get(CFG_ENABLED, True)),
        "max_iter": _int(CFG_MAX_ITER, DEFAULT_MAX_ITER, 1, 30),
        "max_per_turn": _int(CFG_MAX_PER_TURN, DEFAULT_MAX_PER_TURN, 1, 10),
        "workers_max": _int(CFG_WORKERS_MAX, DEFAULT_WORKERS_MAX, 1, 6),
    }


def check_scopes_disjoint(scopes: list) -> str | None:
    """Pure guard: error message when two worker scopes share a path."""
    seen: dict[str, int] = {}
    for idx, scope in enumerate(scopes):
        for path in scope or []:
            norm = os.path.normpath(str(path))
            if norm in seen:
                return (
                    f"{PREFIX_SCOPE} worker scopes overlap on '{norm}' "
                    f"(workers {seen[norm] + 1} and {idx + 1}); split the task so "
                    "every file belongs to exactly one worker, then retry."
                )
            seen[norm] = idx
    return None


class MiniContext:
    """In-memory message list for ONE sub-run.

    Deliberately NOT the project ContextManager: sub-agent chatter must
    never reach session_history.json (quota + privacy + 'the main memory
    stays the main agent's').
    """

    def __init__(self) -> None:
        self.history: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def messages(self) -> list[dict]:
        return list(self.history)


def _schemas_for(role: str) -> list:
    allowed = ROLE_TOOLSETS[role]
    return [t for t in TOOLS if t["function"]["name"] in allowed]


# --------------------------------------------------------------------------
# One sub-run
# --------------------------------------------------------------------------
async def run_subagent(
    role: str,
    task: str,
    *,
    llm_call: Callable,
    tool_executor: Any,
    max_iter: int = DEFAULT_MAX_ITER,
    scope: list | None = None,
    project_map: str = "",
    logger: Any = None,
) -> dict:
    """Run ONE sub-agent to completion or cap. Never raises.

    Returns {"summary", "iterations", "tool_calls", "status"} with status
    in {"ok", "capped", "error"}.
    """
    ctx = MiniContext()
    head = ROLE_PROMPTS[role]
    if role == WORKER and scope:
        head += f" Your write scope (exact paths): {sorted(scope)}."
    if project_map:
        head += f"\n\nProject structure:\n{project_map[:6000]}"
    ctx.add("system", head)
    ctx.add("user", task)

    tools = _schemas_for(role)
    allowed = ROLE_TOOLSETS[role]
    scope_norm = {os.path.normpath(str(p)) for p in (scope or [])}
    last_content = ""
    tool_calls = 0

    def _log(msg: str) -> None:
        if logger is not None:
            try:
                logger.info("[SUBAGENT %s] %s", role, msg)
            except Exception:  # noqa: BLE001 - logging must never break a run
                pass

    try:
        for i in range(max_iter):
            response = await llm_call(messages=ctx.messages(), tools=tools)
            choices = getattr(response, "choices", None)
            if not choices:
                _log("empty model response; ending sub-run")
                break
            msg = choices[0].message
            calls = getattr(msg, "tool_calls", None)
            if calls:
                ctx.add("assistant", msg.content or "")
                for call in calls:
                    name = getattr(call.function, "name", "")
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls += 1
                    if name not in allowed:
                        result = (
                            f"{PREFIX_GUARD} tool '{name}' is not allowed for the "
                            f"{role} sub-agent; use one of {sorted(allowed)}."
                        )
                    elif name in ("write_file", "edit_file") and scope is not None:
                        target = os.path.normpath(str(args.get("file_path", "")))
                        if target not in scope_norm:
                            result = (
                                f"{PREFIX_SCOPE} write to '{target}' rejected: outside "
                                f"assigned scope {sorted(scope)}."
                            )
                        else:
                            result = str(getattr(tool_executor, name)(**args))
                    else:
                        method = getattr(tool_executor, name, None)
                        if method is None:
                            result = f"{PREFIX_GUARD} tool '{name}' does not exist here."
                        else:
                            result = str(method(**args))
                    ctx.add("tool", result)
                continue
            last_content = (msg.content or "").strip()
            if len(last_content) >= MIN_SUMMARY_LEN:
                return {
                    "summary": last_content,
                    "iterations": i + 1,
                    "tool_calls": tool_calls,
                    "status": "ok",
                }
        status = "capped" if tool_calls else "error"
        return {
            "summary": last_content or "(the sub-agent produced no report)",
            "iterations": max_iter,
            "tool_calls": tool_calls,
            "status": status,
        }
    except Exception as exc:  # the loop must never die because of a sub-run
        _log(f"sub-run failed: {type(exc).__name__}: {exc}")
        return {
            "summary": f"{PREFIX_ERROR} {role} sub-run failed: {type(exc).__name__}: {str(exc)[:200]}",
            "iterations": 0,
            "tool_calls": tool_calls,
            "status": "error",
        }


def _format_report(role: str, outcome: dict) -> str:
    return (
        f"{PREFIX_REPORT} • {role} • {outcome['iterations']} iterations, "
        f"{outcome['tool_calls']} tool calls, status {outcome['status']}]\n"
        f"{outcome['summary']}"
    )


# --------------------------------------------------------------------------
# Dispatch entry point (called from the agent loop's tool branch)
# --------------------------------------------------------------------------
async def dispatch(
    tool_name: str,
    args: dict,
    *,
    state: Any,
    llm_call: Callable,
    tool_executor: Any,
    config: dict | None = None,
    project_map: str = "",
    logger: Any = None,
) -> str:
    """Run the requested delegation and return ONE tool-result string.

    Never raises. Enforces: enabled-flag, per-turn budget, worker count,
    disjoint scopes. Increments ``state.subagent_calls`` once per delegation.
    """
    cfg = read_config(config)
    if not cfg["enabled"]:
        return (
            f"{PREFIX_DISABLED} sub-agents are turned off in this project's "
            ".orion/orion_config.json (subagents_enabled=false). Do the work "
            "yourself with the built-in tools, or ask the user to enable them."
        )
    budget = getattr(state, "subagent_calls", 0)
    if budget >= cfg["max_per_turn"]:
        return (
            f"{PREFIX_BUDGET} this chat turn already used {budget} delegations "
            f"(limit {cfg['max_per_turn']}, see subagent_max_per_turn). Continue "
            "with your own tools; delegating again now would waste quota."
        )

    role = role_of_tool(tool_name)

    if tool_name == TOOL_WORKERS:
        tasks = args.get("tasks") or []
        if not isinstance(tasks, list) or not tasks:
            return f"{PREFIX_ERROR} delegate_workers needs a non-empty 'tasks' list."
        if len(tasks) > cfg["workers_max"]:
            return (
                f"{PREFIX_BUDGET} {len(tasks)} workers requested but the limit is "
                f"{cfg['workers_max']} (subagent_workers_max); split the work "
                "across turns or merge tasks."
            )
        scopes = [t.get("files") or [] for t in tasks if isinstance(t, dict)]
        overlap = check_scopes_disjoint(scopes)
        if overlap:
            return overlap
        state.subagent_calls += 1

        async def _one(index: int, item: dict) -> str:
            outcome = await run_subagent(
                WORKER,
                str(item.get("task") or ""),
                llm_call=llm_call,
                tool_executor=tool_executor,
                max_iter=cfg["max_iter"],
                scope=item.get("files") or [],
                project_map=project_map,
                logger=logger,
            )
            return f"--- worker {index + 1} ---\n{_format_report(WORKER, outcome)}"

        outcomes = await asyncio.gather(
            *[_one(i, t) for i, t in enumerate(tasks) if isinstance(t, dict)]
        )
        return "\n\n".join(outcomes)

    # explorer / verifier: single sequential sub-run
    task = str(args.get("task") or "")
    if not task:
        return f"{PREFIX_ERROR} {tool_name} needs a non-empty 'task'."
    if tool_name == TOOL_VERIFIER and args.get("claims"):
        task += f"\n\nClaims to check:\n{args['claims']}"
    state.subagent_calls += 1
    outcome = await run_subagent(
        role,
        task,
        llm_call=llm_call,
        tool_executor=tool_executor,
        max_iter=cfg["max_iter"],
        project_map=project_map,
        logger=logger,
    )
    return _format_report(role, outcome)
