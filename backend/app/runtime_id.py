"""
runtime_id.py — identity & lifecycle of the running backend (UPDATE-013)
========================================================================
Before this module existed, the extension decided "the backend is ready"
from a bare TCP connect to port 8000: ANY process listening there — an
orphaned old backend that survived VS Code, a second Orion install, or a
completely unrelated app — was accepted as "Orion is running". Stale code
then answered chat requests after an upgrade, which is invisible to the
user and extremely confusing to debug.

This module gives every backend process a verifiable identity:

  * ``GET /health`` (token-guarded like every other route) answers with
    the payload from :func:`health_payload` — app name, version, pid,
    boot timestamp, absolute backend folder, and the active project.
  * A PID file (``<repo>/.orion/orion_backend.pid`` by default, override
    with ``ORION_PID_FILE`` for tests) is written at startup and removed
    at shutdown, so tooling can tell which process claims this install.

The extension compares ``backend_path`` with the folder it discovered and
``started_at`` with the mtime of ``app/main.py``: a backend that booted
BEFORE the code was last modified is running stale code and gets offered
a restart (see orion/src/services/backendManager.ts, UPDATE-013).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Bumped 0.4.1 -> 0.5.0 for UPDATE-013: /health + PID file are new
# machine-readable surface, so a minor bump is the honest signal.
# Bumped 0.5.0 -> 0.5.1 for patch 0007: /health gained the interpreter
# fields (python_executable / python_prefix) and the scanner hardening
# landed — both machine-visible, so the version should move.
# Bumped 0.5.1 -> 0.6.0 for UPDATE-014: MCP tool-server foundation —
# /set-project gained the additive "mcp" field, /health gained
# mcp_servers/mcp_tools, and the agent loop can dispatch mcp__* tools.
# New machine-visible surface without breaking old clients = minor bump.
# Bumped 0.6.0 -> 0.6.1 for the UPDATE-014 adendum (patch 0005): mcp.json
# now tolerates a Windows BOM (utf-8-sig) and the test suite no longer
# touches the developer's real ~/.orion_last_project.json / MCP config.
# Bumped 0.6.1 -> 0.7.0 for UPDATE-015: token-guarded management surface
# GET /mcp/status + POST /mcp/config (validate -> atomic write -> reload),
# consumed by the new VS Code MCP panel and wizard card.
# Bumped 0.7.0 -> 0.8.0 for UPDATE-016: internal sub-agents
# (delegate_explorer / delegate_verifier / delegate_workers) with depth-1
# rule, disjoint write scopes, per-turn and per-run budgets; closes the
# MCP phase together with 0.6.x/0.7.x -> milestone v0.1.0.
# Bumped 0.8.0 -> 0.8.1 for UPDATE-017: delegation policy in SYSTEM_PROMPT,
# claim-discipline in sub-agent role prompts, and the mcp_enabled master
# switch (behaviour-shaping, machine-visible via /mcp/status 'enabled').
# Bumped 0.8.1 -> 0.9.0 for UPDATE-018: the .orion/inbox drop-zone (new
# per-turn ingest, 📥 status frame, archive+manifest) and per-role
# sub-agent model profiles (new /models/* routes, subagent_models config).
# Bumped 0.9.0 -> 0.9.1 for UPDATE-019 (defects & forensics): the
# duplicate-assets structure check is pattern-pruned and capped, and the
# incident-013 twin-process diagnosis gained a token-guarded surface
# (GET /runtime/forensics) plus a startup log line — additive diagnostic
# surface, no behaviour change for existing routes.
# Bumped 0.9.1 -> 0.9.2 for UPDATE-020: /mcp/config gained the
# 'master_switch' op (mcp_enabled written atomically into orion_config and
# applied live). The switch STATE was already machine-visible since 0.8.1
# (/mcp/status 'enabled'), so a patch bump is the honest signal — same
# precedent as 0.8.1 itself.
# Bumped 0.9.2 -> 0.9.3 for UPDATE-021: the attachment pipeline accepts
# pdf/docx/xlsx and extracts them through the inbox engine (new behaviour
# on an existing route, /chat; no new surface, hence a patch bump).
# Bumped 0.9.3 -> 0.9.4 for UPDATE-022: the claim-integrity overhaul —
# the failure-aware edit ledger (session_state) with the per-file claim
# challenge TANTANG_EDIT_GAGAL (loop_guards), per-file disk evidence and
# the unchanged-file Error in write_file/edit_file results plus automatic
# syntax checking after .py edits (tool_executor/code_verifier), worker
# edits feeding the main ledger (subagent_manager), and the proactive
# delegation policy (prompts). Behaviour of existing routes changes
# (honesty layer); no new route or surface, hence a patch bump.
# Bumped 0.9.4 -> 0.10.0 for UPDATE-023: the behaviour architecture change
# ("bebas tapi tidak ngawur") — SYSTEM_PROMPT rebuilt from 22 sections
# (~6.1k tokens, 41 MUST) into 3 layers (~3.0k, conversation contract
# first), and the ORION-17b claim challenge became a SOFT one-shot
# answer-first correction (hard ledger-backed challenge keeps full quota).
# A minor bump is the honest signal: existing chat behaviour changes
# substantially (deliberately — one harness scenario re-registered), even
# though no route or field changed shape.
# Bumped 0.10.0 -> 0.10.1 for UPDATE-024 Commit 1: sub-agent LLM calls
# are recorded in .orion/performance_log.jsonl with an additive "role"
# field (owner directive: sub-run token usage is billing/trial data;
# a silent sub-agent burning tokens was the same defect class as a
# silent profile fallback). Additive logging only — patch bump.
# Bumped 0.10.1 -> 0.10.2 for UPDATE-024 commits 2-5: hardened stdout
# sink (the 707-block logging spam), pinned sub-run retry + budget
# refund, the vibe-coder conversation clause, and the Supabase loan
# package (docs, template, service-role warning, floor rule).
# Bumped 0.10.2 -> 0.10.3 for UPDATE-024 commits 6-7: the FINDINGS
# LEDGER (durable conversation memory injected per turn, pruning
# exemption for sub-agent reports) and the memory-honesty contract
# rule (name your source; re-derive or admit, never hallucinate
# compliance, never search disk for conversation content).
# Bumped 0.10.3 -> 0.10.4 for UPDATE-024 Commit 8: the REAL fix for the
# logging-error spam. The 0.10.2 sink hardening guarded the wrong
# object — uvicorn's access/error loggers hold the stderr handle
# captured at uvicorn import time (pre-redirect; invalid on
# console-less Windows), so their flush failures never touched the
# guarded writer. repoint_handlers() now moves exactly the uvicorn*
# StreamHandlers onto the guarded sink, at import and again at
# lifespan startup; the owner growth-check (707 -> 719 blocks) is
# what disproved the first fix.
# UPDATE-025 hotfix (InvoiceFlow todo-hijack incident, owner transcript
# 2026-10-28): a stale todo list from an earlier session hijacked a
# frontend-pricing question into old Decimal work (forced LANJUT_TODO
# nudge), and the soft-limit pause leaked the literal placeholder
# "[mention the last task]" - born as "[sebutkan task terakhir]", never
# filled by anyone since the backend composes that message itself. The
# soft-limit warning is now composed from real state (the turn's
# request, last tool, pending todos) and stored as the resume anchor;
# a nudge whose next todo predates the turn re-anchors to the user's
# request instead of forcing the stale item.
# Bumped 0.10.5 -> 0.10.6 for UPDATE-025 hotfix-2, three mechanical
# teeth grown from the same field day: (1) tool_guards.pre_call_check,
# the shared pre-call screening gate both dispatch branches pass
# through (external review note: the mcp__ path bypassed every
# built-in guard; logging-first notice for mutating-looking MCP
# names); (2) git_commit hygiene - blind `git add -A` had committed
# frontend/node_modules, dist, pyc files and .orion/session_history.json
# on a project without .gitignore; the tool now creates/tops up a
# baseline .gitignore, untracks generated paths (index only, files
# stay on disk), and reports the staged file count with a >200 large-
# commit warning; (3) checkpoint disclosure of todo items completed
# this turn with zero work evidence at completion (the "Implementasikan
# UI" todo completed on a turn whose only file change was a status
# markdown) - commit 5 v1, disclosure before rejection.
# Bumped 0.10.6 -> 0.10.7 for the hotfix-2 adendum: the hygiene
# untrack matcher was root-anchored (startswith "node_modules/") and
# therefore missed nested generated paths - frontend/node_modules,
# frontend/dist, app/__pycache__ - the exact InvoiceFlow shape. It now
# scans path segments at any depth, consistent with the unanchored
# .gitignore patterns. (The ADENDUM-1 commit claimed this bump but its
# scripted edit targeted a __version__ symbol that does not exist; the
# version lives HERE. This commit is the real bump.)
# Bumped 0.10.7 -> 0.10.8 for UPDATE-025 commit 1: delegation-compliance
# disclosure. InvoiceFlow round 2 proved the gap reproducible even with
# quota 6 and an explicit "panggil delegate_verifier" instruction: the
# model self-verified (verify_html + npm build) and claimed "diverifikasi"
# with zero delegate_verifier dispatches. The checkpoint now discloses
# requested-minus-dispatched roles; per-turn ledger in SessionState.
# Bumped 0.10.8 -> 0.10.9 for UPDATE-025 commit 5: sub-run salvage +
# end-of-turn claim disclosure. Owner transcript (InvoiceFlow add-customer)
# proved two context-loss mechanisms: (1) a dead/capped sub-run's MiniContext
# was discarded — the main agent re-investigated from zero; now the salvaged
# tool trail + last fragment ride the [SUBAGENT PARTIAL] report and enter the
# findings ledger. (2) After a SOFT-LIMIT pause, the resume segment resets
# SessionState, so a completion claim routed JAWAB_NORMAL escaped every
# delegation disclosure; the requested-minus-dispatched gap now rides that
# frame too when the reply claims completion.
# Bumped 0.10.9 -> 0.10.10 for UPDATE-025 commit 6 (owner directive):
# retry-1 per sub-agent LLM call. The router already retries per model and
# fails over the whole fleet, so a death reaching the sub-run = fleet-wide
# outage; the missing layer was ONE paused retry of the whole call
# (backoff 8 s, env ORION_SUBRUN_RETRY_BACKOFF), classified transient-only
# (_retryable). Retry exhaustion falls through to the 0.10.9 salvage path.
# Perf log gains status "retry" rows and an additive "retried" flag.
BACKEND_VERSION = "0.10.10"
APP_NAME = "orion-backend"

# This file is <repo>/backend/app/runtime_id.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Captured at import time, i.e. moments before uvicorn binds the port.
STARTED_AT = time.time()


def pid_file_path() -> Path:
    """Where this install announces its live process (overridable for tests)."""
    override = os.getenv("ORION_PID_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _REPO_ROOT / ".orion" / "orion_backend.pid"


def write_pid_file() -> Path | None:
    """Announce this process as the live backend of THIS install.

    Called from the lifespan startup hook, i.e. only after uvicorn has
    successfully bound port 8000 — a backend that fails to bind never
    leaves a misleading PID file behind. Best-effort: an unwritable
    location degrades to a warning, never to a crashed startup.
    """
    path = pid_file_path()
    payload = {
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "version": BACKEND_VERSION,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path
    except OSError as e:
        print(f"[ORION RUNTIME WARNING] cannot write PID file {path}: {e}")
        return None


def clear_pid_file() -> None:
    """Remove the PID file on shutdown — but ONLY if it is still ours.

    When two installs race (or the file was replaced by a newer process),
    deleting someone else's announcement would recreate the very confusion
    this module exists to prevent.
    """
    path = pid_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("pid") == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


# >>> UPDATE-019: twin-process forensics (incident 013) ====================
# The 013 incident ("two backends alive at once") was painful because the
# three sources of truth — the PID file's claim, the process actually
# answering port 8000, and the code mtime on disk — can each disagree, and
# nothing exposed all three at once. These helpers never raise: forensics
# must work exactly when the install is misbehaving.
_IS_WINDOWS = os.name == "nt"


def _pid_alive_windows(pid: int):
    """Side-effect-free liveness probe for Windows; None when unavailable.

    REGRESSION GUARD (owner's machine, 2026-10-23): ``os.kill(pid, 0)`` is
    UNUSABLE on Windows — CPython maps any signal besides the two console
    events to ``TerminateProcess(pid, sig)``, so a "liveness probe" would
    KILL the target process (and with sig=0 it can even surface as
    SystemError: <built-in function kill> returned a result with an
    exception set). The supported probe is OpenProcess +
    GetExitCodeProcess == STILL_ACTIVE, which only observes.
    """
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None  # not a real Windows runtime: degrade safely
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False  # no such process (or no rights to even query)
        try:
            exit_code = ctypes.c_ulong(0)
            if not windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return None
            return exit_code.value == STILL_ACTIVE
        finally:
            windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - a broken probe must never raise
        return None


def pid_alive(pid) -> bool:
    """Best-effort liveness probe for a pid; never raises, never kills.

    POSIX: signal 0 via os.kill. Windows: the OpenProcess probe above —
    os.kill would terminate the target there (see _pid_alive_windows).
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if _IS_WINDOWS:
        return bool(_pid_alive_windows(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it just is not ours to signal
    except OSError:
        return False
    return True


def read_pid_file() -> dict:
    """The raw PID-file claim: {path, exists, pid, started_at, version, error}.

    ``error`` is set when the file EXISTS but cannot be parsed (corrupt or
    foreign content); a missing file is simply exists=False, error=None.
    """
    path = pid_file_path()
    out = {
        "path": str(path),
        "exists": False,
        "pid": None,
        "started_at": None,
        "version": None,
        "error": None,
    }
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return out  # missing file = simply no claim, not an error
    except OSError as e:
        out["error"] = f"unreadable: {type(e).__name__}"
        return out
    out["exists"] = True
    try:
        data = json.loads(raw)
    except ValueError:
        out["error"] = "corrupt: not valid JSON"
        return out
    if isinstance(data, dict):
        out["pid"] = data.get("pid")
        out["started_at"] = data.get("started_at")
        out["version"] = data.get("version")
    else:
        out["error"] = "corrupt: not a JSON object"
    return out


def forensics_payload(project_path: str | None) -> dict:
    """The body of GET /runtime/forensics — incident-013 evidence in one call.

    Compares the three sources of truth that can disagree:
      1. the PID file's CLAIM (which process announced this install),
      2. THIS process (by definition the one answering port 8000),
      3. the code on disk (booted before the last modification = stale).
    ``verdicts`` holds honest, human-readable conclusions; an empty list
    means nothing suspicious. The extension's own staleness check
    (backendManager.ts) stays as it is — this route is the owner/agent
    facing diagnostic, and the startup log line uses the same evidence.
    """
    claim = read_pid_file()
    me = os.getpid()
    claim_pid = claim.get("pid")
    claim_alive = pid_alive(claim_pid)
    verdicts: list = []

    if claim["exists"] and claim["error"] is not None:
        verdicts.append(
            f"PID FILE UNREADABLE ({claim['error']}); its claim is ignored. "
            "A corrupt or foreign file at this path usually means another "
            "tool wrote here, or the previous process died mid-write."
        )
    elif claim["exists"] and isinstance(claim_pid, int) and claim_pid != me:
        if claim_alive:
            verdicts.append(
                f"TWIN SUSPECTED: the PID file claims pid {claim_pid} for this "
                f"install and that pid is ALIVE, yet the process answering this "
                f"request is {me}. Two backends are racing for one install — "
                f"only one can hold port 8000. Find the stray process "
                f"(Get-Process -Id {claim_pid}) and kill it, or restart VS Code."
            )
        else:
            verdicts.append(
                f"STALE PID FILE: it claims pid {claim_pid}, which is not alive "
                f"— the previous shutdown was unclean (crash/kill). This "
                f"process ({me}) replaced the claim at boot; no twin."
            )

    code_modified_at = None
    try:
        code_modified_at = (BACKEND_DIR / "app" / "main.py").stat().st_mtime
    except OSError:
        pass
    if code_modified_at is not None and STARTED_AT < code_modified_at:
        verdicts.append(
            "STALE CODE: this process booted BEFORE app/main.py was last "
            "modified, so it is running old code. Restart the backend "
            "(kill the port-8000 listener, then Start Backend)."
        )

    return {
        "app": APP_NAME,
        "version": BACKEND_VERSION,
        "self": {"pid": me, "started_at": STARTED_AT},
        "pid_file": claim,
        "pid_file_claim_alive": claim_alive,
        "code_modified_at": code_modified_at,
        "project_path": project_path,
        "verdicts": verdicts,
    }
# >>> END OF UPDATE-019 forensics ==========================================


def health_payload(project_path: str | None) -> dict:
    """The body of GET /health. Keep keys stable — the extension parses them.

    UPDATE-013 patch 0007: the interpreter fields exist because a real
    incident showed a backend listening on port 8000 while running under a
    DIFFERENT Python than the project venv (wrong package stack, silent
    provider failures). Version, folder and PID alone could not see it;
    the executable/prefix can.
    """
    return {
        "app": APP_NAME,
        "version": BACKEND_VERSION,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "backend_path": str(BACKEND_DIR),
        "project_path": project_path,
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
    }
