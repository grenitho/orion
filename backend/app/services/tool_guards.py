"""
tool_guards.py — the shared pre-call screening entry point (UPDATE-025 hotfix-2)
================================================================================
Born from an external review note (Sam Vallad33, 2026-10-28, answered in
the owner's reply mail): the UPDATE-014 ``mcp__`` dispatch branch sits
BEFORE the tool_executor guard, and the real built-in screening (path
containment, dangerous-command rejection, SSRF rejection) lives INSIDE
the individual executor methods. An MCP call therefore passes none of
it — the only shared protection is the post-call anti-loop guard. Once
a project's MCP server exposes a tool that writes or deletes, it gets
a straighter path than Orion's own tools.

The fix philosophy is the owner's standing principle ("free but not
lawless — freedom in the conversation layer, teeth in the code") and
Sam's own suggestion: ONE function that BOTH dispatch branches pass
through, introduced logging-first. Today it:

  * returns a plain-text NOTICE for MCP tools whose name hints at
    state mutation (write/delete/exec/...); the loop logs it and
    prepends it to the tool result so the model (and the transcript)
    can see the call was unscreened;
  * returns '' for built-in tools — their teeth already live in
    tool_executor, and this call site is the structural parity point
    where future shared teeth (a real permit system, a Vestige-style
    hook) get wired without touching the loop again.

Pure: no I/O, no wall-clock, deterministic — harness-safe.
"""

from __future__ import annotations

# Substrings that mark an MCP tool name as probably state-mutating.
# Deliberately coarse on purpose: a false positive only adds a notice
# line, a false negative is the status quo (no screening at all yet).
MCP_MUTATING_HINTS = (
    "write", "delete", "remove", "exec", "run", "shell", "create",
    "update", "put", "post", "save", "move", "rename", "mkdir", "kill",
)


def nama_terlihat_mutating(nama: str) -> bool:
    """True when an (MCP) tool name hints at state mutation."""
    rendah = str(nama or "").lower()
    return any(h in rendah for h in MCP_MUTATING_HINTS)


def pre_call_check(nama: str, args, *, mcp: bool) -> str:
    """The shared pre-call screening gate for EVERY tool dispatch.

    Returns a notice string that must be surfaced (logged + prepended
    to the tool result), or '' when there is nothing to say. Never
    raises: a malformed args object or a weird name degrades to ''.
    """
    try:
        if not mcp:
            # Built-in tools: screening already lives inside
            # tool_executor's methods. This call is the parity point —
            # future shared teeth land HERE, in one place, for both
            # branches.
            return ""
        if nama_terlihat_mutating(nama):
            return (
                f"[ORION PRE-CALL NOTICE] MCP tool '{nama}' looks "
                "state-mutating. Orion's built-in guards (path containment, "
                "dangerous-command rejection, SSRF rejection) do NOT screen "
                "MCP calls yet; this result is unscreened. Judge it by its "
                "tool result only."
            )
        return ""
    except Exception:
        return ""
