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
import re
import os
import time
from typing import Any, Callable

from app.services import findings_ledger
from app.services.perf_log import log_performance
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

# >>> UPDATE-017: shared closing sentence for every role prompt. The main
# loop's completion-claim gate (ORION-17b) reacts to claim words in the MAIN
# agent's reply; reports full of "done/selesai/berhasil" get quoted or
# paraphrased by the main agent and then challenged, burning an extra
# verification round (observed live in the UPDATE-016 owner E2E). Teaching
# sub-agents factual wording keeps the whole chain cheap.
_CLAIM_DISCIPLINE = (
    " Report factually (what exists, what you changed, what is missing); "
    "avoid completion-claim words such as 'done', 'completed', 'finished', "
    "'success', 'selesai', 'berhasil' — the orchestrating loop reads those "
    "as completion claims and pays an extra challenge round for them."
)

ROLE_PROMPTS = {
    EXPLORER: (
        "You are Orion's Explorer sub-agent. Investigate the task using ONLY "
        "read-only tools. Do not modify anything, do not ask the user anything. "
        "Finish with ONE condensed findings report: what exists where, what is "
        "relevant to the task, and what is missing. Keep it under 400 words."
        + _CLAIM_DISCIPLINE
    ),
    VERIFIER: (
        "You are Orion's Verifier sub-agent. Check the claimed state of the code "
        "with REAL tools (verify_code / verify_html / run_tests / git_diff / "
        "reads). Do not modify anything, do not ask the user anything. Finish "
        "with a verdict line (PASS / PASS WITH NOTES / FAIL) followed by a "
        "concrete defect list (file, problem, suggested fix) or 'no defects "
        "found'. Quote tool output where it matters."
        + _CLAIM_DISCIPLINE
    ),
    WORKER: (
        "You are an Orion Worker sub-agent. Implement ONLY the assigned task and "
        "ONLY inside your assigned file scope; writes outside the scope are "
        "rejected by the system. Do not ask the user anything. Finish with a "
        "short report: files changed, what was done, what was left out."
        + _CLAIM_DISCIPLINE
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
# >>> UPDATE-018: per-role model profiles (Feature B). Config shape:
#   "subagent_models": {"explorer"|"verifier"|"workers":
#       {"group": "gemini"|"provider_<N>"|"fallback", "model": name} | null}
# Empty/absent/null = the role keeps using the main router (owner's
# explicit requirement). The router owns credential resolution; a broken
# profile degrades to the main router with a warning, never to a crash.
CFG_MODELS = "subagent_models"

# Config keys are the OWNER-facing role names; note 'workers' (plural) maps
# to the internal WORKER role — every parallel worker of one delegation
# shares the same profile.
ROLE_PROFILE_KEYS = {EXPLORER: "explorer", VERIFIER: "verifier", WORKER: "workers"}

DEFAULT_MAX_ITER = 6
# >>> UPDATE-024 Commit 3: backoff for the single quiet retry a pinned
# profile call gets on failure (see _llm_with_profile).
PROFILE_RETRY_BACKOFF_SEC = 1.5
DEFAULT_MAX_PER_TURN = 3
DEFAULT_WORKERS_MAX = 3

PREFIX_REPORT = "[SUBAGENT REPORT"
PREFIX_BUDGET = "[SUBAGENT BUDGET]"
PREFIX_SCOPE = "[SUBAGENT SCOPE]"
PREFIX_GUARD = "[SUBAGENT GUARD]"
PREFIX_DISABLED = "[SUBAGENTS DISABLED]"
PREFIX_ERROR = "[SUBAGENT ERROR]"
# >>> UPDATE-025 commit 5: marker for a salvaged partial sub-run — the
# dead/capped explorer-worker-verifier's findings handed to the main agent
# so it continues FROM them instead of re-investigating from zero.
PREFIX_PARTIAL = "[SUBAGENT PARTIAL]"

# >>> UPDATE-025 commit 6 (owner directive: "pasang retry-1 untuk setiap
# sub agent — kejadian ini sudah berulang"): the router ALREADY retries
# per model (LLM_MAX_RETRIES_PER_MODEL) and fails over across the whole
# fleet, so an exception that reaches a sub-run means the ENTIRE cascade
# was down. The missing layer was a fleet-wide pause: ONE retry of the
# whole call after a backoff, then the 0.10.9 salvage path if it dies
# again. Retry-1, never more — a second sub-run-level retry would just
# double worst-case cost against an outage minutes long.
SUBRUN_RETRY_BACKOFF_SEC = float(os.getenv("ORION_SUBRUN_RETRY_BACKOFF", "8"))
_SUBRUN_RETRY_MARKERS = (
    "all ai models", "timeout", "timed out", "connection", "disconnect",
    "incomplete", "busy",
)


def _retryable(exc: BaseException) -> bool:
    """Which sub-run deaths deserve the single fleet-level retry.

    Network/timeout classes plus the router's cascade-final message
    ("All AI models are busy/failing…") qualify. Schema/auth/bad-request
    deaths do NOT: retrying a 400 burns quota against a certainty.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    teks = str(exc).lower()
    return any(m in teks for m in _SUBRUN_RETRY_MARKERS)

MIN_SUMMARY_LEN = 40  # a shorter 'report' is treated as not-yet-finished


def is_subagent_tool(name: str) -> bool:
    return name in SUBAGENT_TOOLS


def role_of_tool(name: str) -> str:
    return {TOOL_EXPLORER: EXPLORER, TOOL_VERIFIER: VERIFIER, TOOL_WORKERS: WORKER}[name]


# >>> UPDATE-025 commit 1 (delegation-compliance disclosure): which
# sub-agent roles did the USER's own message ask for? Two deterministic
# patterns, deliberately narrow (v1): the explicit tool names, and a role
# word in a sub-agent/delegation context ("sub agent verifier:",
# "delegasikan ke worker", "delegate the fix to the explorer"). A bare
# "verify"/"verification" must NOT match — verify_html is a tool, not a
# verifier, and over-matching would cry wolf on every build check.
_POLA_TOOL_DELEGASI = re.compile(r"\bdelegate_(explorer|verifier|workers)\b", re.I)
_POLA_SUB_PERAN = re.compile(
    r"(?:sub[\s_-]?agent|delegasi\w*|delegation|delegate)\b"
    r"(?:\s+\w+){0,3}\s+(explorer|verifier|workers?)\b",
    re.I,
)


def roles_diminta(teks: str) -> set:
    """Roles the user's request named this turn (empty set = none)."""
    if not teks:
        return set()
    peran = set()
    for pola in (_POLA_TOOL_DELEGASI, _POLA_SUB_PERAN):
        for m in pola.finditer(teks):
            nama = m.group(1).lower()
            peran.add(WORKER if nama == "workers" else nama)
    return peran


def _catat_peran(state: Any, role: str) -> None:
    """Record a successful dispatch on the turn's delegation ledger.

    Tolerates duck-typed states in tests that predate the field.
    """
    peran = getattr(state, "delegasi_terpakai", None)
    if peran is None:
        peran = set()
        state.delegasi_terpakai = peran
    peran.add(role)


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


def read_model_profiles(config: dict | None) -> dict:
    """Validated {role: {"group","model"}} from orion_config subagent_models.

    UPDATE-018. Deliberately tolerant: malformed entries (wrong types,
    empty strings, unknown roles) are DROPPED here so the affected role
    silently keeps using the main router; credential-level validity (does
    the group exist in this env?) is the router's job — it re-checks and
    logs the fallback warning. Nothing raises.
    """
    raw = (config or {}).get(CFG_MODELS) if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        return {}
    profiles: dict = {}
    for role, key in ROLE_PROFILE_KEYS.items():
        entry = raw.get(key)
        if not isinstance(entry, dict):
            continue  # null / absent / garbage -> main router
        group = entry.get("group")
        model = entry.get("model")
        if (
            isinstance(group, str)
            and group.strip()
            and isinstance(model, str)
            and model.strip()
        ):
            profiles[role] = {"group": group.strip(), "model": model.strip()}
    return profiles


def _llm_with_profile(llm_call: Callable, profile: dict | None) -> Callable:
    """Wrap llm_call so every sub-run call carries the role's profile.

    Returns the ORIGINAL callable when there is no profile, so the default
    path (no subagent_models configured) is bit-for-bit the pre-018
    behaviour — existing fakes in tests keep working untouched.

    >>> UPDATE-024 Commit 3: one quiet retry on failure. A pinned profile
    is a SINGLE-TARGET queue by design, so a transient provider timeout
    used to kill the whole sub-run (owner log 2026-10-27: glm TIMEOUT ->
    'sub-run-failed' -> the explorer report never arrived). The main loop
    retries across tiers; a pin has no tiers, so it gets one backoff retry
    instead — pin fidelity preserved (same model), flakiness absorbed.
    """
    if not profile:
        return llm_call

    async def _call(messages, tools=None):
        try:
            return await llm_call(messages=messages, tools=tools, profile=profile)
        except Exception:
            await asyncio.sleep(PROFILE_RETRY_BACKOFF_SEC)
            return await llm_call(messages=messages, tools=tools, profile=profile)

    return _call


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


def _catat_perf_subrun(tool_executor: Any, response: Any, role: str,
                       mulai: float, *, status: str | None = None,
                       extra: dict | None = None) -> None:
    """Record ONE sub-run LLM call into .orion/performance_log.jsonl
    (UPDATE-024 Commit 1, owner directive: sub-run token usage is user data).

    Entries carry the additive ``role`` field ("explorer"/"verifier"/
    "worker") so per-role accounting is possible; the main loop's entries
    keep their exact old shape. ``response=None`` records the failed-call
    shape (status "error", zero tokens). ``status="retry"`` (UPDATE-025
    commit 6) marks a first attempt that died and earned the sub-run's
    single retry; ``extra`` rides additive fields like ``retried``.
    Never raises: bookkeeping must not kill a sub-run — same rule as the
    ledger hook.
    """
    try:
        proj = getattr(tool_executor, "project_path", None) or "."
        if response is None:
            log_performance(
                proj,
                "sub-run-retry" if status == "retry" else "sub-run-failed",
                0, 0, 0.0, status or "error", role=role, extra=extra,
            )
            return
        usage = getattr(response, "usage", None)
        log_performance(
            proj,
            getattr(response, "model", None) or "auto_rotated_model",
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            (time.perf_counter() - mulai) * 1000,
            "success",
            role=role,
            extra=extra,
        )
    except Exception:  # noqa: BLE001 - bookkeeping never breaks a run
        pass


def _schemas_for(role: str) -> list:
    allowed = ROLE_TOOLSETS[role]
    return [t for t in TOOLS if t["function"]["name"] in allowed]


def _catat_edit_ke_ledger(state: Any, tool: str, path: str, result: str) -> None:
    """UPDATE-022: worker edits land in the MAIN turn's edit ledger.

    A worker's failed write/edit (including a scope rejection) used to be
    visible only inside the sub-agent report text — the main loop's claim
    gate never saw it, the exact defect class of the InvoiceFlow M1
    incident one layer down. Successes are recorded too, so a later retry
    marks the path recovered. Never raises: the ledger must not be able to
    kill a sub-run. ``state=None`` keeps every old call site valid.
    """
    if state is None or not hasattr(state, "catat_edit_gagal"):
        return
    try:
        if isinstance(result, str) and result.lower().startswith("success"):
            state.catat_edit_sukses(path)
        else:
            first_line = (
                str(result).splitlines()[0][:200] if result else "no result"
            )
            state.catat_edit_gagal(path, f"[{tool} via sub-agent] {first_line}")
    except Exception:  # noqa: BLE001 - bookkeeping must never break a run
        pass


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
    state: Any = None,
    retry_backoff: float = SUBRUN_RETRY_BACKOFF_SEC,
) -> dict:
    """Run ONE sub-agent to completion or cap. Never raises.

    Returns {"summary", "iterations", "tool_calls", "status", "retries",
    "salvage"} with status in {"ok", "capped", "error"}.
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
    iter_done = 0
    retries = 0
    # >>> UPDATE-025 commit 5 (owner transcript: explorer timeout -> main
    # re-investigated from zero): a dead or capped sub-run used to take
    # everything it learned to the grave. This trail digests what it
    # actually did (tool + key args + first slice of each result) so the
    # dispatch layer can hand the findings to the main agent instead of
    # a bare error string.
    jejak: list[str] = []

    def _salvage() -> str:
        if not jejak and not last_content:
            return ""
        bagian = []
        if jejak:
            bagian.append(
                "Tools already called by the sub-run (do NOT repeat these "
                "reads):\n" + "\n".join(jejak[-8:])
            )
        if last_content:
            bagian.append("Last report fragment:\n" + last_content[:1200])
        return "\n\n".join(bagian)

    def _log(msg: str) -> None:
        if logger is not None:
            try:
                logger.info("[SUBAGENT %s] %s", role, msg)
            except Exception:  # noqa: BLE001 - logging must never break a run
                pass

    try:
        for i in range(max_iter):
            _mulai_panggilan = time.perf_counter()
            response = None
            _percobaan = 0
            # >>> UPDATE-025 commit 6: retry-1 at the SUB-RUN layer. The
            # router's own retries (per model + fleet failover) already
            # ran and lost inside this call; one paused retry of the whole
            # call is the layer that was missing when the provider outage
            # outlived the cascade. Non-transient deaths raise immediately.
            for _percobaan in (0, 1):
                try:
                    response = await llm_call(messages=ctx.messages(), tools=tools)
                    break
                except Exception as exc_llm:
                    if _percobaan == 0 and _retryable(exc_llm):
                        retries += 1
                        _log(
                            f"LLM call died ({type(exc_llm).__name__}); retry-1 "
                            f"in {retry_backoff:.0f}s — router cascade already "
                            "exhausted"
                        )
                        _catat_perf_subrun(
                            tool_executor, None, role, _mulai_panggilan,
                            status="retry",
                        )
                        await asyncio.sleep(retry_backoff)
                        _mulai_panggilan = time.perf_counter()
                        continue
                    raise
            iter_done = i + 1
            # >>> UPDATE-024 Commit 1: record the call BEFORE interpreting it
            # — tokens were spent whether or not the answer is usable.
            _catat_perf_subrun(
                tool_executor, response, role, _mulai_panggilan,
                extra={"retried": True} if _percobaan else None,
            )
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
                        # >>> UPDATE-022: the main turn's ledger sees every
                        # worker edit outcome — scope rejections included.
                        _catat_edit_ke_ledger(state, name, target, result)
                    else:
                        method = getattr(tool_executor, name, None)
                        if method is None:
                            result = f"{PREFIX_GUARD} tool '{name}' does not exist here."
                        else:
                            result = str(method(**args))
                            if name in ("write_file", "edit_file"):
                                # Defensive: only workers hold write tools
                                # today; if a toolset ever widens, the ledger
                                # must not miss the edit.
                                _catat_edit_ke_ledger(
                                    state, name,
                                    os.path.normpath(str(args.get("file_path", ""))),
                                    result,
                                )
                    jejak.append(
                        f"- {name}("
                        + ", ".join(
                            f"{k}={str(v)[:80]}" for k, v in args.items()
                        )
                        + f") -> {result[:300]}"
                    )
                    ctx.add("tool", result)
                continue
            last_content = (msg.content or "").strip()
            if len(last_content) >= MIN_SUMMARY_LEN:
                return {
                    "summary": last_content,
                    "iterations": i + 1,
                    "tool_calls": tool_calls,
                    "status": "ok",
                    "retries": retries,
                }
        status = "capped" if tool_calls else "error"
        return {
            "summary": last_content or "(the sub-agent produced no report)",
            "iterations": max_iter,
            "tool_calls": tool_calls,
            "status": status,
            "salvage": _salvage(),
            "retries": retries,
        }
    except Exception as exc:  # the loop must never die because of a sub-run
        _log(f"sub-run failed: {type(exc).__name__}: {exc}")
        # >>> UPDATE-024 Commit 1: a failed sub-call still belongs in the
        # usage log (status "error"), so trial data never hides a dead pin.
        _catat_perf_subrun(tool_executor, None, role, 0.0)
        return {
            "summary": f"{PREFIX_ERROR} {role} sub-run failed: {type(exc).__name__}: {str(exc)[:200]}",
            # >>> UPDATE-025 commit 5: honest iteration count (0 used to
            # erase the fact that the sub-run DID work before dying).
            "iterations": iter_done,
            "tool_calls": tool_calls,
            "status": "error",
            "salvage": _salvage(),
            "retries": retries,
        }


def _semua_error(outcomes) -> bool:
    """True when EVERY worker report string carries the error prefix —
    the delegation produced nothing at all (UPDATE-024 Commit 3)."""
    reports = [o for o in outcomes if isinstance(o, str)]
    return bool(reports) and all(PREFIX_ERROR in o for o in reports)


def _format_report(role: str, outcome: dict) -> str:
    teks = (
        f"{PREFIX_REPORT} • {role} • {outcome['iterations']} iterations, "
        f"{outcome['tool_calls']} tool calls, status {outcome['status']}]\n"
        f"{outcome['summary']}"
    )
    # >>> UPDATE-025 commit 5 (owner: "the explorer was cut off, then Orion
    # re-investigated everything itself"): when a sub-run dies or is capped,
    # its salvaged trail rides along. The main agent continues FROM it —
    # no repeat reads, and the pipeline may resume that role NEXT turn.
    salvage = str(outcome.get("salvage") or "")
    if salvage and outcome.get("status") in ("error", "capped"):
        teks += (
            f"\n\n{PREFIX_PARTIAL} the {role} sub-run ended early "
            f"(status: {outcome['status']}, retries spent: "
            f"{outcome.get('retries', 0)}). What it already accomplished "
            "survives below — continue FROM it with your own tools; do NOT "
            "re-read files it already read. Delegating this same role again "
            "is allowed on a NEXT turn, never in this one.\n"
            f"{salvage}"
        )
    return teks


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

    # >>> UPDATE-018: per-role model profile (orion_config subagent_models).
    # Resolved ONCE per delegation; workers of one delegation share the
    # 'workers' profile. A missing/invalid entry means llm_call_role is the
    # untouched main-router callable (pre-018 behaviour); credential-level
    # fallback happens inside the router with a logged warning.
    profile = read_model_profiles(config).get(role)
    if profile and logger is not None:
        try:
            logger.info(
                "[SUBAGENT %s] model profile pinned: group=%s model=%s",
                role,
                profile["group"],
                profile["model"],
            )
        except Exception:  # noqa: BLE001 - logging must never break a dispatch
            pass
    llm_call_role = _llm_with_profile(llm_call, profile)

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
                llm_call=llm_call_role,
                tool_executor=tool_executor,
                max_iter=cfg["max_iter"],
                scope=item.get("files") or [],
                project_map=project_map,
                logger=logger,
                state=state,  # UPDATE-022: worker edits feed the main ledger
            )
            return f"--- worker {index + 1} ---\n{_format_report(WORKER, outcome)}"

        outcomes = await asyncio.gather(
            *[_one(i, t) for i, t in enumerate(tasks) if isinstance(t, dict)]
        )
        # >>> UPDATE-024 Commit 3: a delegation that died wholesale must not
        # eat the turn's budget — refund it so the main agent may retry once.
        if _semua_error(outcomes):
            state.subagent_calls = max(0, state.subagent_calls - 1)
        else:
            # >>> UPDATE-025 commit 1: at least one worker really ran —
            # the turn's delegation ledger records the role.
            _catat_peran(state, WORKER)
            # >>> UPDATE-024 Commit 6: worker reports enter the findings
            # ledger so next turn's context still knows what they found.
            for _idx, _rep in enumerate(outcomes):
                findings_ledger.simpan_temuan(
                    getattr(tool_executor, "project_path", "."),
                    f"workers#{_idx + 1}", _rep)
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
        llm_call=llm_call_role,
        tool_executor=tool_executor,
        max_iter=cfg["max_iter"],
        project_map=project_map,
        logger=logger,
        state=state,  # UPDATE-022: read-only roles never edit, but the
        # ledger hook is uniform (and future-proof) for every role
    )
    if outcome.get("status") == "error":
        state.subagent_calls = max(0, state.subagent_calls - 1)
        # >>> UPDATE-025 commit 5: a dead sub-run still found things. The
        # salvaged trail enters the findings ledger too, so even a context
        # prune/summarize or a soft-limit resume cannot erase it — the
        # durable half of the "Orion loses context" fix.
        if str(outcome.get("salvage") or ""):
            findings_ledger.simpan_temuan(
                getattr(tool_executor, "project_path", "."),
                role, _format_report(role, outcome))
    else:
        # >>> UPDATE-025 commit 1: the sub-run succeeded — record the role
        # on the turn's delegation ledger (a crashed sub-run refunds the
        # budget AND stays invisible here, so the checkpoint can disclose
        # "requested but never really ran").
        _catat_peran(state, role)
        # >>> UPDATE-024 Commit 6: the report becomes durable conversation
        # memory (findings ledger), immune to history pruning/summarize —
        # the fix for "Orion forgot its own explorer findings next turn".
        findings_ledger.simpan_temuan(
            getattr(tool_executor, "project_path", "."),
            role, _format_report(role, outcome))
    return _format_report(role, outcome)
