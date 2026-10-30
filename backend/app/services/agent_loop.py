"""
agent_loop.py — the Orion agent loop (Phase C step 4)
======================================================
`aliran_agent` is the async generator that used to be `_aliran_inti` inside
the /chat endpoint: the agentic loop that calls the LLM, dispatches tools,
streams SSE events, and guards itself (anti-loop, claim verification, token
budget, heartbeats). It moved here VERBATIM — no behaviour was changed while
moving (FASE_C_PLAN.md hard rule 2).

main.py keeps HTTP routing and composition: it builds the SessionState and
the Deps below, and wraps this generator with stream_utils.jamin_done so
every run still ends with exactly one 'done' (and a genuine crash still
produces none).

The private helpers the loop needs (text normalizers, token estimation,
terminal-command policy, summarize/prune, performance logging) moved with it;
they had no other consumer in main.py. SYSTEM_PROMPT and TOOLS are static
module constants and are imported directly.

One deliberate, documented semantic note: Deps captures tool_executor at
request time, whereas the old closure read main.py's global on every access.
The two differ only if /set-project runs WHILE a stream is active — a race
the extension never produces (it calls set-project before chat). Snapshot
semantics are the stricter, more predictable behaviour.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import tiktoken

from app.services import guard_rules, loop_guards, project_facts, research_rules
from app.services import sse, tool_dispatch
from app.services import subagent_manager, tool_guards
from app.services.llm_router import get_ai_response
# >>> UPDATE-024 Commit 1: the jsonl writer moved to perf_log.py so
# subagent_manager can record sub-run usage without a circular import.
# Re-exported here for backwards compatibility with any caller that
# imported it from agent_loop since Phase C.
from app.services.perf_log import log_performance
from app.services.prompts import SYSTEM_PROMPT
from app.services.session_state import SessionState
from app.services.tools_schema import TOOLS

_THINKING_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)

_token_encoder = tiktoken.get_encoding("cl100k_base")


def fix_over_escaped_text(text) -> str:
    """Some models (especially the OpenRouter/Cloudflare fallback tiers)
    sometimes double-escape newlines when generating the JSON 'arguments'
    of a tool call containing multi-line text (e.g. the 'question' in
    ask_user) — writing '\\\\n' instead of '\\n' inside the JSON. After
    json.loads(), this shows up on the user's screen as the two-character
    literal '\\n' (not a real newline). This function normalizes that
    without breaking strings that were already correct.

    >>> FIX: the old version only did .replace("\\n", "\n") in a single
    pass. That is enough for 1 layer of over-escaping, but when the model
    over-escapes 2x or more (e.g. raw JSON containing many consecutive
    backslashes before 'n'), a single replace pass — or even repeated
    replaces — still leaves stray backslashes in front of the newline (e.g.
    it becomes '\\\\n' instead of a clean newline) because replace()
    matches exactly 1 backslash + 'n', not a RUN of backslashes + 'n'.
    It now uses the regex \\+n / \\+t / \\+r which sweeps the ENTIRE
    run of backslashes in front of n/t/r into a single real whitespace
    character, however many escape layers there are, in 1 pass — no
    leftover backslashes."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\\+n", "\n", text)
    text = re.sub(r"\\+t", "\t", text)
    text = re.sub(r"\\+r", "\r", text)
    return text


def strip_thinking_blocks(text: str) -> str:
    """Strip <thinking>...</thinking> blocks before showing text to the user in the frontend.
    The RAW version (with thinking) is still saved to context_manager for Orion's
    internal memory — only what is yielded to the stream gets cleaned."""
    if not text:
        return text
    return _THINKING_BLOCK_RE.sub("", text).strip()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_token_encoder.encode(text))
    except Exception:
        return len(text) // 3


def estimate_message_tokens(msg: dict) -> int:
    """Estimate tokens for 1 message, INCLUDING tool_calls (name + arguments).
    Previously it only counted 'content', so messages with large tool_calls
    (e.g. a long 'diff' in edit_file) were underestimated."""
    # >>> ATTACHMENT FEATURE: content can be a LIST of parts (multimodal).
    # Without this, str(list) produces a Python repr containing all the base64
    # -> the token estimate explodes and auto-summarize cuts history wrongly.
    _c = msg.get("content")
    if isinstance(_c, list):
        _teks = " ".join(
            str(p.get("text", "")) for p in _c
            if isinstance(p, dict) and p.get("type") == "text"
        )
        # Gemini charges +/- 258 tokens per image, far cheaper than its base64
        _n_img = sum(1 for p in _c if isinstance(p, dict) and p.get("type") == "image_url")
        total = estimate_tokens(_teks) + (_n_img * 258)
    else:
        total = estimate_tokens(str(_c or ""))
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            func = tc.get("function", {}) if isinstance(tc, dict) else {}
            total += estimate_tokens(str(func.get("name", "")))
            total += estimate_tokens(str(func.get("arguments", "")))
    return total


# --- LIST OF DANGEROUS COMMANDS ---
DANGEROUS_PATTERNS = [
    "rm -rf",
    "rm -r",
    "del /s",
    "del /q",
    "format ",
    "mkfs",
    "git push",
    "git reset --hard",
    "git clean -fd",
    "drop table",
    "drop database",
    "truncate table",
    "sudo ",
    "chmod 777",
    "shutdown",
    "reboot",
]

BLOCKING_SERVER_PATTERNS = [
    "streamlit run",
    "npm run dev",
    "npm start",
    "flask run",
    "uvicorn ",
    "python -m http.server",
    "node server",
    "next dev",
    "django runserver",
    "tauri dev",
    "cargo run",
]


def is_long_running_server(cmd: str) -> bool:
    """Check whether this command starts a server that runs forever."""
    cmd_lower = cmd.lower()
    return any(pattern in cmd_lower for pattern in BLOCKING_SERVER_PATTERNS)


def is_dangerous_command(cmd: str) -> bool:
    """Check whether a terminal command contains dangerous keywords."""
    cmd_lower = cmd.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd_lower:
            return True
    return False


def is_terminal_failure(output: str) -> bool:
    """Detect whether terminal output contains a fatal error that needs a code fix."""
    out_lower = output.lower()
    fatal_errors = [
        "traceback (most recent call last)",
        "syntaxerror:",
        "nameerror:",
        "typeerror:",
        "valueerror:",
        "modulenotfounderror:",
        "importerror:",
        "npm err!",
        "error ts",
        "tsc: error",
        "fatal error",
        "command not found",
        "permission denied",
        "errno",
    ]
    return any(err in out_lower for err in fatal_errors)


# >>> UPDATE-025 commit 7 (owner: "backend dikabarkan terputus padahal
# tidak, backend tetap running"): Node's fetch (undici) aborts a response
# body that stays SILENT for ~300 s (default bodyTimeout). The main LLM
# await has carried a 15 s heartbeat since ORION-29, but three awaits in
# this stream sent nothing for minutes — two summarize passes and, above
# all, the sub-agent dispatch (owner perf log: a single 159 s glm call
# inside a >5 min explorer sub-run). The client-side abort then became
# the extension's "Cannot reach the Orion backend" while the backend
# lived on and kept working. SSE COMMENT bytes every SSE_KEEPALIVE_SEC
# keep the body flowing; every conforming parser — including the
# webview reader, which only acts on 'data: ' lines — ignores comments.
SSE_KEEPALIVE_SEC = float(os.getenv("ORION_SSE_KEEPALIVE_SEC", "20"))


async def _keepalive(coro, hasil: list, interval: float | None = None):
    """Run ``coro`` as a task, yielding SSE keepalive comments while it is
    silent; append its result to ``hasil`` (async generators cannot return
    values to async-for). The coro's exception re-raises at the caller's
    await point, exactly like a bare await would. If the stream itself is
    cancelled (client gone), the task is cancelled too — same anti-orphan
    rule as UPDATE-013's LLM cancel guard, now covering sub-runs.
    """
    tugas = asyncio.ensure_future(coro)
    jeda = SSE_KEEPALIVE_SEC if interval is None else interval
    try:
        while not tugas.done():
            selesai, _pong = await asyncio.wait({tugas}, timeout=jeda)
            if not selesai:
                yield sse.KEEPALIVE
        hasil.append(tugas.result())
    except BaseException:
        if not tugas.done():
            tugas.cancel()
        raise


def _L(state, en: str, idn: str) -> str:
    """UPDATE-025 commit 13: user-facing literals follow the detected
    turn language (state.bahasa_turn). The model mirrors the system
    voice; an English-only template out-votes the prompt's LANGUAGE
    RULE, so the templates now speak the user's language."""
    return idn if getattr(state, "bahasa_turn", "") == "id" else en


def _referensi_belum_dibaca(state, project_path) -> list:
    """UPDATE-025 commit 12: the shared condition behind BOTH the
    checkpoint disclosure (commit 11) and the in-turn reconciliation
    gate (commit 12): UI files changed this turn, saved reference
    attachments exist, none was read this turn. Returns the reference
    names (empty list = condition false).
    """
    if not getattr(state, "files_changed_this_turn", None):
        return []
    _UI = (".jsx", ".tsx", ".vue", ".css", ".scss", ".html")

    def _ada_ui(entri) -> bool:
        # ledger entries read "- `src/App.vue` (write_file)" — take the
        # backticked path, fall back to the raw string.
        _m = re.findall(r"`([^`]+)`", str(entri))
        _p = _m[0] if _m else str(entri)
        return _p.lower().endswith(_UI)

    if not any(_ada_ui(f) for f in state.files_changed_this_turn):
        return []
    dirr = os.path.join(project_path or ".", ".orion", "attachments")
    try:
        refs = sorted(os.listdir(dirr))
    except OSError:
        return []
    if not refs:
        return []
    if getattr(state, "lampiran_dibaca_turn_ini", None):
        return []
    return refs


def _perlu_nudge_referensi(state, tool_executor) -> bool:
    """One in-turn reconciliation challenge per turn (commit 12)."""
    if getattr(state, "nudge_referensi", 0):
        return False
    return bool(
        _referensi_belum_dibaca(
            state, getattr(tool_executor, "project_path", ".")
        )
    )


def _blok_referensi_belum_dibaca(state, project_path) -> str:
    """UPDATE-025 commit 11 (owner incident: Orion invented indigo
    #4f46e5 while the green/gold handoff it was given sat unread): when
    UI files change on a turn that never opened the saved reference
    attachments, say so at the checkpoint. Deterministic: the reference
    list is the .orion/attachments/ directory itself, the consultation
    is this turn's read_file ledger. Disclosure, not rejection.
    """
    refs = _referensi_belum_dibaca(state, project_path)
    if not refs:
        return ""
    daftar = "\n".join(f"- `.orion/attachments/{r}`" for r in refs)
    if getattr(state, "bahasa_turn", "") == "id":
        teks = (
            "\n\n**⚠️ Lampiran referensi tersimpan TIDAK dibuka turn ini:**\n"
            + daftar
            + "\n\nFile UI berubah sementara referensi bawaan user ini tidak "
            "dibaca sama sekali turn ini. Baca ulang dengan read_file dan "
            "rekonsiliasi token desain (warna, font, spasi, layout) terhadap "
            "hasil render sebelum menyebut pekerjaan selesai."
        )
    else:
        teks = (
            "\n\n**⚠️ Saved reference attachments were NOT consulted this "
            "turn:**\n"
            + daftar
            + "\n\nUI files changed while these user-provided references "
            "sat unread this turn. Re-read them with read_file and reconcile "
            "the design tokens (colors, fonts, spacing, layout) against the "
            "rendered result before calling the work done."
        )
    if getattr(state, "nudge_referensi", 0):
        # >>> commit 12 escalation: the model was challenged INSIDE the
        # turn and closed anyway — the user must see that, not just the
        # plain gap.
        teks += _L(
            state,
            "\n\nOrion was reminded mid-turn (REFERENCE RECONCILIATION) "
            "and still closed without reading them: every design token "
            "in this turn's work must be treated as UNVERIFIED against "
            "the user's reference.",
            "\n\nOrion sudah diingatkan di tengah turn (REFERENCE "
            "RECONCILIATION) namun tetap menutup tanpa membacanya: semua "
            "token desain hasil turn ini harus dianggap BELUM "
            "TERVERIFIKASI terhadap referensi user.",
        )
    return teks


def _blok_edit_gagal_akhir(state) -> str:
    """UPDATE-025 commit 14 (owner paste 2026-10-30: a one-line-comment
    request vanished — the edit failed with 'SEARCH block not found',
    TANTANG_EDIT_GAGAL fired, and Orion rationalized the rejection as
    'a system injection testing or noting an earlier context/retry
    simulation', then verified, committed and reported done WITHOUT
    ever retrying the edit). The mid-turn challenge now quotes the
    turn's real request and says it is not a simulation; this is the
    end-of-turn twin: failed edits that never recovered get named at
    BOTH turn endings, so 'build passed' can never stand in for
    'your change landed'. Single emitter, both endings, localized.
    """
    gagal = state.edit_gagal_belum_pulih()
    if not gagal:
        return ""
    daftar = "\n".join(f"- `{g['path']}` — {g['alasan']}" for g in gagal[:6])
    if len(gagal) > 6:
        daftar += f"\n- +{len(gagal) - 6} more"
    return _L(
        state,
        "\n\n**⚠️ Failed edits were NEVER recovered this turn:**\n"
        + daftar
        + "\n\nThe changes above were never successfully applied, so this "
        "turn's request may be UNFULFILLED even where the reply reads as "
        "done. Judge the claim against these files, not against a passing "
        "build.",
        "\n\n**⚠️ Edit gagal TIDAK PERNAH pulih sampai akhir turn:**\n"
        + daftar
        + "\n\nPerubahan di atas tidak pernah berhasil diterapkan, jadi "
        "permintaan turn ini mungkin BELUM TERPENUHI meski jawaban terbaca "
        "selesai. Nilai klaim terhadap file-file ini, bukan terhadap build "
        "yang lolos.",
    )


def _blok_persistensi_bila_perlu(state, tool_executor) -> str:
    """UPDATE-025 commit 9: the deterministic persistence-fact rider.

    Fires only when (a) an open failure thread exists, (b) it is about
    stored/added data, (c) the bounded project scan finds NO persistence
    marker at all, and (d) the block was not already delivered for this
    thread. Fact, not guess: the block states exactly what was checked.
    """
    if not getattr(state, "utas_gagal", ""):
        return ""
    if getattr(state, "fakta_persistensi_disampaikan", False):
        return ""
    if not guard_rules.utas_tentang_data(state.utas_gagal):
        return ""
    proj = getattr(tool_executor, "project_path", ".")
    if project_facts.ada_persistensi(proj):
        return ""
    state.fakta_persistensi_disampaikan = True
    return project_facts.blok_persistensi(
        proj, bahasa=getattr(state, "bahasa_turn", "") or "en"
    )


def _tutup_utas_bila_disarankan(state, konten) -> None:
    """The thread closes ONLY on demonstrated advisory wording in the
    model's own text — boilerplate blocks never close it for it."""
    if getattr(state, "utas_gagal", "") and guard_rules.kata_saran(konten):
        state.utas_gagal = ""


def _blok_delegasi_akhir(state, klaim: bool = False) -> str:
    """UPDATE-025 commit 10: ONE merged end-of-turn delegation block.

    Commit 8's pipeline-broke block and commit 5's claim disclosure
    could both fire on the same frame, stating the same gap twice and —
    worse — contradicting each other: a role dispatched-but-dead read
    "ended early (retries 2)" in one block and "zero successful
    delegate_* calls reached them" in the other (owner paste,
    2026-10-29: verifier died AFTER two retries yet the second block
    claimed nobody reached it). One block now, per-role fate accurate:

      * requested role with fate error/capped -> "ended early (status
        X, retries N)";
      * requested role with no fate at all    -> "was never
        dispatched" (mati branch) / bare line (claim-only branch);
      * header: pipeline-broke when anything died, else the
        requested-but-NOT-dispatched header, claim-gated;
      * exactly one guidance paragraph.
    """
    diminta = subagent_manager.roles_diminta(
        getattr(state, "permintaan_turn_ini", "") or ""
    )
    if not diminta:
        return ""
    nasib = getattr(state, "nasib_role", None) or {}
    mati = sorted(
        r for r in diminta if (nasib.get(r) or ("",))[0] in ("error", "capped")
    )
    belum = sorted(r for r in diminta if r not in nasib)
    if not mati and not (klaim and belum):
        return ""
    if mati:
        baris = "\n".join(
            _L(
                state,
                f"- ⚠️ `{r}` ended early (status {nasib[r][0]}, retries {nasib[r][1]})",
                f"- ⚠️ `{r}` mati lebih dulu (status {nasib[r][0]}, retries {nasib[r][1]})",
            )
            for r in mati
        )
        if belum:
            baris += "\n" + "\n".join(
                _L(state, f"- ⚠️ `{r}` was never dispatched",
                   f"- ⚠️ `{r}` tidak pernah dispatch")
                for r in belum
            )
        paragraf = _L(
            state,
            "The conclusions above rest on the dead sub-run's salvaged "
            "partial findings plus main-agent self-checks — NOT the "
            "ordered independent pipeline.",
            "Kesimpulan di atas bertumpu pada temuan parsial selamatan "
            "dari sub-run yang mati plus pemeriksaan mandiri agen utama "
            "— BUKAN pipeline independen yang Anda perintahkan.",
        )
        if belum or klaim:
            paragraf += _L(
                state,
                " Self-run checks (builds, verify_*, pytest) are NOT "
                "independent verification.",
                " Pemeriksaan mandiri (build, verify_*, pytest) BUKAN "
                "verifikasi independen.",
            )
        paragraf += _L(
            state,
            " Dispatch the missing role(s) on a next turn, or tell the "
            "user plainly why they were skipped.",
            " Dispatch role yang hilang pada turn berikutnya, atau "
            "katakan terus terang kepada user kenapa role itu dilewati.",
        )
        return (
            _L(
                state,
                "\n\n**⚠️ Sub-agent pipeline broke this turn:**\n",
                "\n\n**⚠️ Pipeline sub-agent putus turn ini:**\n",
            )
            + f"{baris}\n\n{paragraf}"
        )
    baris = "\n".join(f"- ⚠️ `{r}`" for r in belum)
    return (
        _L(
            state,
            "\n\n**⚠️ Delegation requested but NOT dispatched this turn:**\n",
            "\n\n**⚠️ Delegasi diminta tetapi TIDAK dispatch turn ini:**\n",
        )
        + f"{baris}\n\n"
        + _L(
            state,
            "This turn's request named the role(s) above, but zero "
            "successful delegate_* calls reached them. Self-run checks "
            "(builds, verify_*, pytest) are NOT independent verification. "
            "Dispatch the missing role(s), or tell the user plainly why "
            "they were skipped.",
            "Permintaan turn ini menyebut role di atas, tetapi nol "
            "panggilan delegate_* yang sukses sampai ke mereka. "
            "Pemeriksaan mandiri (build, verify_*, pytest) BUKAN "
            "verifikasi independen. Dispatch role yang hilang, atau "
            "katakan terus terang kepada user kenapa mereka dilewati.",
        )
    )


async def summarize_old_history(old_messages: list) -> str:
    """Call the LLM to summarize old chat history.
    IMPORTANT: this function does NOT swallow exceptions. If the API fails, the error is raised to the caller.
    """
    prompt = "Summarize the following conversation into 1 dense paragraph containing the main context, the decisions made, and the current task status. Ignore pleasantries.\n\n"
    for msg in old_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if content:
            prompt += f"{role}: {content[:500]}\n"

    # REMOVED the try/except here. Let exceptions bubble up to main.py so clear_history() is not called on failure.
    response = await get_ai_response(
        messages=[{"role": "user", "content": prompt}], tools=None
    )
    return response.choices[0].message.content


async def execute_summarize(context_manager, history, threshold=15):
    """Helper that runs the sliding-window summarize. Returns True on success."""
    if len(history) <= threshold:
        return False

    # Use safe_split_index so the assistant+tool pair is never split
    split_idx = context_manager.safe_split_index(history, 10)
    messages_to_summarize = history[:split_idx]
    recent_messages = history[split_idx:]

    prompt = "Summarize the following conversation and tool results into 1 dense paragraph. Focus on: which files were read/changed, which errors were found, and what the next step is. Ignore pleasantries.\n\n"
    for msg in messages_to_summarize:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))[:1000]
        prompt += f"{role}: {content}\n"

    # Create a backup before the risky operation
    context_manager.create_backup()

    # Call the API (raises on failure, thanks to Critical fix #1)
    summary_text = await summarize_old_history(messages_to_summarize)

    # Remove old history and fill it with the summary + the most recent messages
    context_manager.clear_history()
    context_manager.add_message("user", f"[SUMMARY OF PREVIOUS SESSION]: {summary_text}")
    for m in recent_messages:
        context_manager.add_message(
            m["role"],
            m["content"],
            tool_calls=m.get("tool_calls"),
            tool_call_id=m.get("tool_call_id"),
            reasoning_content=m.get("reasoning_content"),
        )
    return True


def prune_history_safely(context_manager, keep_last_n: int, notice: str = None):
    """Centralized helper for PERMANENTLY pruning the context_manager history
    using safe_split_index (so an assistant tool_calls + tool response pair
    is never cut apart / left 'orphaned').
    Used by both the 400-error recovery and the token budget guard, so
    pruning is consistent and need not be repeated every iteration."""
    history = context_manager.get_history()
    if len(history) <= keep_last_n:
        return False

    split_idx = context_manager.safe_split_index(
        history, max(0, len(history) - keep_last_n)
    )
    recent_history = history[split_idx:]

    context_manager.clear_history()
    if notice:
        context_manager.add_message("user", notice)
    for m in recent_history:
        context_manager.add_message(
            m["role"],
            m["content"],
            tool_calls=m.get("tool_calls"),
            tool_call_id=m.get("tool_call_id"),
            reasoning_content=m.get("reasoning_content"),
        )
    return True


# >>> CRITICAL FIX (moved from main.py in Phase C step 4): these two were
# hard-imports -- a missing/broken file would ModuleNotFoundError the whole
# backend before it ever listened on a port. They are soft dependencies:
# if the import fails, only the related feature is disabled (with a safe
# fallback) and the backend still starts normally. (The third soft module,
# todo_manager, is imported by main.py itself and injected via Deps.)
try:
    from app.services.post_edit_verifier import (
        extract_replace_snippet,
        verify_file_edit,
        verify_file_write,
    )
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] post_edit_verifier.py failed to import ({e}). Post-edit verification disabled."
    )

    def verify_file_write(*args, **kwargs):
        return ""

    def verify_file_edit(*args, **kwargs):
        return ""

    def extract_replace_snippet(*args, **kwargs):
        return ""


try:
    from app.services.permission_manager import is_path_safe, is_sensitive_file
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] permission_manager.py failed to import ({e}). Path-safety guard DISABLED (fallback: all paths are considered safe -- restore this file IMMEDIATELY)."
    )

    def is_path_safe(project_path, file_path):
        return True, "OK (permission_manager unavailable, guard skipped)"

    def is_sensitive_file(file_path):
        return False

# The 22 tool names that REALLY have a dispatch branch in the agent loop.
# Lives here (next to the dispatch table) since Phase C step 4; main.py's
# startup consistency checker and the loop's unknown-tool message both use
# it via tool_dispatch.NAMA_TOOL_AKTIF.
NAMA_TOOL_AKTIF = {
    "ask_user", "update_todos", "list_files", "read_file", "read_file_range",
    "write_file", "edit_file", "search_code", "read_json_path", "run_terminal",
    "web_search", "web_fetch", "git_status", "git_diff", "verify_html",
    "verify_code",
    "restore_backup", "list_backups", "delete_file", "move_file", "git_commit",
    "run_tests",
}


# >>> UPDATE-025 hotfix (InvoiceFlow todo-hijack incident, 2026-10-28) <<<
# Two PURE helpers for the per-turn re-anchor state. The incident: a stale
# todo list from an earlier session ("fix line_total precision with
# Decimal") hijacked a turn whose actual user request was a frontend
# pricing question — the forced LANJUT_TODO nudge pushed the model into
# the old work, the soft limit then paused the turn with the literal
# template placeholder "[mention the last task]", and the owner's
# "lanjut" resumed blind: Orion reported Decimal work instead of
# answering the question. Both helpers are deterministic (no wall-clock
# values) so harness recordings stay byte-identical across runs.
def _permintaan_pengguna_terakhir(history) -> str:
    """The user's genuine request of the current turn.

    Walks the history newest-first and returns the first user message
    that is not a system injection ("[SYSTEM ...]" — warnings, nudges,
    continuations). Vision-parts content (a list of typed parts) is
    flattened to its text parts. '' when nothing qualifies; callers
    display a "(not captured)" fallback rather than guessing.
    """
    for msg in reversed(history or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content or "").strip()
        if not content or content.startswith("[SYSTEM"):
            continue
        return content
    return ""


def _catatan_tool(nama: str, args) -> str:
    """Deterministic one-line "tool key='value'" note for the soft-limit
    status. Shows only the FIRST argument, whitespace-normalized and
    truncated at 60 chars, so a write_file body can never blow up the
    warning frame. Never raises: a malformed args object degrades to the
    bare tool name."""
    try:
        if isinstance(args, dict) and args:
            kunci = next(iter(args))
            nilai = " ".join(str(args[kunci]).split())
            if len(nilai) > 60:
                nilai = nilai[:60] + "…"
            return f"{nama} {kunci}={nilai!r}"
        return str(nama)
    except Exception:
        return str(nama)
# >>> END OF UPDATE-025 hotfix helpers <<<


@dataclass
class Deps:
    """Explicit dependencies of the agent loop (Phase C step 4).

    Carries what FASE_C_PLAN.md prescribes — context_manager, todo_manager,
    tool_executor, llm_call, kirim_status, config, logger — plus the two
    per-request inputs the loop needs: the prebuilt project/editor context
    message and the one-line attachment description for the status event.
    """

    context_manager: object
    todo_manager: object | None
    tool_executor: object | None
    llm_call: Callable            # get_ai_response (heartbeat wrapper stays in the loop)
    kirim_status: Callable[[str], str]   # teks -> SSE 'status' frame (sse.ev partial)
    config: dict                  # orion_config: max_iterations, token_budget_threshold
    logger: object
    context_message: dict         # "Current project structure" + editor context
    lampiran_desc: str = ""       # one-line attachment description (may be empty)
    # >>> UPDATE-014: the MCP client manager (app.services.mcp_manager.MCPManager)
    # or None when MCP is unavailable/unconfigured. The loop only ever calls
    # has_tool/status_label/tool_names/openai_tools/call on it — all of them
    # are side-effect-free except call(), which NEVER raises (failure strings
    # come back as ordinary tool results). Default None keeps every existing
    # Deps(...) construction site valid.
    mcp: object | None = None
    # >>> UPDATE-018: one-line inbox arrival description for the frozen
    # 'status' frame (📥 Inbox: …) — produced by inbox_manager.describe()
    # in the /chat route; '' when the drop-zone brought nothing this turn,
    # which keeps the SSE stream byte-identical to the pre-018 shape.
    inbox_desc: str = ""


async def aliran_agent(state: SessionState, deps: Deps) -> AsyncIterator[str]:
    """The agent loop: one /chat turn as an async iterator of SSE frames.

    Moved verbatim out of main.py (Phase C step 4). `state` carries the
    per-turn bookkeeping (session_state.SessionState); `deps` carries the
    explicit dependencies (Deps below). The terminal-event guarantee stays
    OUTSIDE this generator: main.py wraps it with stream_utils.jamin_done,
    so a genuine crash here still produces no 'done'.
    """
    # >>> AUTO-SUMMARIZE CONTEXT WINDOW (SLIDING WINDOW) <<<
    # If the history exceeds 15 messages, summarize the old ones
    # >>> AUTO-SUMMARIZE CONTEXT WINDOW (START OF CHAT) <<<
    history = deps.context_manager.get_history()
    if len(history) > 15:
        yield deps.kirim_status('🧠 Memory full, compressing context...')
        try:
            # >>> UPDATE-025 commit 7: keepalive across the silent summarize
            _hasil_summarize: list = []
            async for _ka in _keepalive(
                execute_summarize(deps.context_manager, history, threshold=15),
                _hasil_summarize,
            ):
                yield _ka
            print("[ORION SUMMARIZE] Initial context summarized successfully.")
        except Exception as e:
            print(
                f"[ORION SUMMARIZE WARNING] Initial summarization failed: {e}. History preserved."
            )
    # >>> END OF AUTO-SUMMARIZE <<<

    # >>> PATCH ORION-4: the fallback default (500) differed from the config
    # default above (20). If orion_config.json was broken/missing, the limit
    # jumped from 20 to 500 without warning. Aligned + given a safety brake.
    max_iter = deps.config.get("max_iterations", 20)
    if not isinstance(max_iter, int) or max_iter < 1:
        max_iter = 20
    max_iter = min(max_iter, 200)   # safety brake: prevent an endless loop

    # >>> NEW: the token budget threshold is read from config, not hardcoded.
    # Match it to the actual model's context window (leave a buffer for output).
    token_budget_threshold = deps.config.get("token_budget_threshold", 12000)
    if not isinstance(token_budget_threshold, int) or token_budget_threshold < 1000:
        token_budget_threshold = 12000

    print(
        f"[ORION CONFIG] Config file read: {os.path.join(deps.tool_executor.project_path, '.orion', 'orion_config.json')}"
    )
    print(f"[ORION CONFIG] max_iterations value in use: {max_iter}")
    print(f"[ORION CONFIG] Token budget threshold: {token_budget_threshold}")

    # >>> UPDATE-014: MCP tools join the built-in TOOLS schema for this turn.
    # Built ONCE per request (the registry is stable while a turn runs).
    # Without healthy MCP servers tools_for_llm is literally TOOLS, so the
    # provider request stays byte-identical to the pre-014 shape.
    tools_for_llm = TOOLS
    if deps.mcp is not None:
        _mcp_tools = deps.mcp.openai_tools()
        if _mcp_tools:
            tools_for_llm = TOOLS + _mcp_tools
            print(f"[ORION MCP] {len(_mcp_tools)} MCP tool(s) added to this turn's schema.")
    # >>> END OF UPDATE-014 tools merge

    try:
        last_tool_calls = []

        # >>> CRITICAL FIX: tracker of files ACTUALLY changed on THIS turn only.
        # Previously the "TASK COMPLETED" checkpoint relied solely on generic
        # keywords ("selesai", "berhasil", etc.) in the model's text — words that are
        # VERY common in ordinary audit/report sentences even when NO
        # edit_file/write_file/create_file tool was actually called. As a result,
        # Orion could "claim completion" without doing any work, or repeat the
        # same audit because it never actually executed the changes. The tracker
        # (state.files_changed_this_turn, Phase C step 2) is a MANDATORY
        # precondition before the completion checkpoint may trigger a pause
        # (see its usage below).

        # >>> ANTI-INFINITE-LOOP GUARD <<<
        consecutive_400_errors = 0
        last_400_error_msg = ""

        # >>> PATCH ORION-1b: cap on how many times the loop may "nudge" the model
        # to continue with the remaining todos. Prevents an endless loop when the
        # model keeps writing "done" without actually doing anything.
        # The counter itself lives in state.nudge_count (Phase C step 2).
        MAX_TODO_NUDGE = int(os.getenv("ORION_MAX_TODO_NUDGE", "10"))

        # >>> PATCH ORION-17a: the "claim verification" counter
        # (state.verifikasi_count, Phase C step 2).
        # Used to challenge a model that claims completion without changing files.
        MAX_VERIFIKASI = int(os.getenv("ORION_MAX_VERIFIKASI", "2"))

        # >>> UPDATE-025 hotfix: per-turn re-anchor state, captured BEFORE
        # the first iteration. `permintaan_turn_ini` = the user's genuine
        # request (main.py stores it in the history before the loop runs);
        # `todo_ids_awal_turn` = which pending todo ids ALREADY existed
        # before this turn. The LANJUT_TODO arm below uses the snapshot to
        # tell "todos of this task" (force-continue, PATCH ORION-1
        # protection) from "stale todos of older sessions" (re-anchor to
        # the current request instead of hijacking the turn). <<<
        state.permintaan_turn_ini = _permintaan_pengguna_terakhir(
            deps.context_manager.get_history()
        )
        # >>> UPDATE-025 commit 13 (owner: Orion answers in English to an
        # Indonesian user): the LANGUAGE RULE in SYSTEM_PROMPT was losing
        # to the loop's own English system-voice (checkpoint template,
        # disclosure blocks). Detect the turn language deterministically;
        # user-facing literals below render in it, and a one-line reminder
        # in the TARGET language rides the per-iteration context message
        # (ephemeral — never stored in history).
        state.bahasa_turn = guard_rules.deteksi_bahasa(state.permintaan_turn_ini)
        if state.bahasa_turn == "id" and isinstance(deps.context_message, dict):
            deps.context_message["content"] = str(
                deps.context_message.get("content", "")
            ) + (
                "\n[BAHASA] Pengguna berbahasa Indonesia pada turn ini: tulis "
                "SELURUH prose Anda — jawaban, laporan checkpoint, pertanyaan "
                "— dalam bahasa Indonesia. Identifier kode, path file, nama "
                "tool, dan keluaran log tetap apa adanya. Pesan yang diawali "
                "'[SYSTEM' atau '[SISTEM' adalah injeksi sistem, BUKAN pesan "
                "user; jangan pernah menjadikannya dasar deteksi bahasa — "
                "bahasa turn ini adalah bahasa permintaan asli user."
            )
        state.todo_ids_awal_turn = {
            str(t.get("id")) for t in state.sisa_todo(deps.todo_manager)
        }
        # >>> UPDATE-025 commit 1: a FRESH delegation ledger every turn, so
        # a role dispatched last turn can never launder this turn's claim.
        state.delegasi_terpakai = set()
        # >>> UPDATE-025 commit 8: same freshness rule for the fate ledger —
        # a death recorded last turn must not colour this turn's disclosure.
        state.nasib_role = {}
        # >>> UPDATE-025 commit 9: the advisory challenge is one-shot PER
        # TURN (the failure THREAD itself stays sticky across turns).
        state.saran_count = 0
        # >>> UPDATE-025 commit 11: per-turn reference-consultation ledger.
        state.lampiran_dibaca_turn_ini = set()
        # >>> UPDATE-025 commit 12: per-turn reconciliation-challenge cap.
        state.nudge_referensi = 0
        _utas_baru = guard_rules.utas_gagal_dari(state.permintaan_turn_ini)
        if _utas_baru:
            state.utas_gagal = _utas_baru
            state.fakta_persistensi_disampaikan = False
        # >>> END OF UPDATE-025 hotfix re-anchor state <<<

        # >>> CONVENTION: `_` below is the agent LOOP COUNTER.
        # Never use `_` as a throwaway variable inside this loop
        # (see test_loop_counter_guard.py which guards this).
        for _ in range(max_iter):
            # >>> STEP 1: DYNAMIC SLIDING WINDOW SUMMARIZE (INSIDE THE LOOP) <<<
            history = deps.context_manager.get_history()
            if len(history) > 20:
                yield deps.kirim_status('🧠 Memory full, compressing context...')
                try:
                    # >>> UPDATE-025 commit 7: keepalive across the silent summarize
                    _hasil_summarize_loop: list = []
                    async for _ka in _keepalive(
                        execute_summarize(
                            deps.context_manager, history, threshold=20
                        ),
                        _hasil_summarize_loop,
                    ):
                        yield _ka
                    print(
                        "[ORION SUMMARIZE] In-loop context summarized successfully."
                    )
                except Exception as e:
                    print(
                        f"[ORION SUMMARIZE WARNING] In-loop summarization failed: {e}. History preserved."
                    )
            # >>> END OF DYNAMIC SLIDING WINDOW SUMMARIZE <<<

            # >>> NEW: SOFT LIMIT CHECKPOINT (Human-in-the-Loop) <<<
            # Once 80% of max_iter is reached, force Orion to report and ask permission
            soft_limit = int(max_iter * 0.8)
            if _ == soft_limit:
                # >>> UPDATE-025 hotfix (InvoiceFlow incident 2026-10-28):
                # the old template shipped the literal placeholder
                # "[mention the last task]" to the user — it was written as
                # if the model would fill it in, but this message is
                # composed by the BACKEND and streamed verbatim, so the
                # placeholder leaked on EVERY soft limit since the feature
                # existed (born as "[sebutkan task terakhir]", translated
                # as-is in phase D). Worse: the same blind text was stored
                # into the history, so the "lanjut" resume had no anchor
                # and the model reported unrelated work. The status is now
                # composed from REAL state: the request this turn must
                # answer, the last tool executed, and the pending todos.
                # The stored copy re-anchors the resume turn. <<<
                _permintaan = state.permintaan_turn_ini.strip()
                if len(_permintaan) > 300:
                    _permintaan = _permintaan[:300] + " …"
                _status_permintaan = (
                    f'"{_permintaan}"' if _permintaan else "(not captured)"
                )
                _status_aksi = (
                    state.last_tool_note
                    or _L(
                        state,
                        "(no tool executed yet this turn)",
                        "(belum ada tool yang jalan turn ini)",
                    )
                )
                _todo_pending = state.sisa_todo(deps.todo_manager)
                if _todo_pending:
                    _baris_todo = [
                        f"[{t.get('id', '?')}] {str(t.get('text', ''))[:80]}"
                        for t in _todo_pending[:4]
                    ]
                    if len(_todo_pending) > 4:
                        _baris_todo.append(f"+{len(_todo_pending) - 4} more")
                    _status_todo = (
                        f"{len(_todo_pending)} pending — " + "; ".join(_baris_todo)
                    )
                else:
                    _status_todo = _L(state, "none", "tidak ada")
                warning_msg = _L(
                    state,
                    f"\n\n**[SYSTEM WARNING - SOFT LIMIT]**\n"
                    f"I have reached {_} iterations (80% of the {max_iter} iteration limit).\n"
                    f"To avoid burning tokens, I will pause here for a moment.\n\n"
                    f"**Current status:**\n"
                    f"- Request I am answering this turn: {_status_permintaan}\n"
                    f"- Last action: {_status_aksi}\n"
                    f"- Todo list: {_status_todo}\n\n"
                    f"**Your options:**\n"
                    f"1. Type **'lanjut'** / **'continue'** if I am on the right track — "
                    f"when I resume, the request quoted above comes first.\n"
                    f"2. Give new instructions if I seem to be going in circles or off track.\n",
                    f"\n\n**[PERINGATAN SISTEM - BATAS LUNAK]**\n"
                    f"Saya mencapai {_} iterasi (80% dari batas {max_iter} iterasi).\n"
                    f"Untuk menghindari pemborosan token, saya jeda sebentar di sini.\n\n"
                    f"**Status saat ini:**\n"
                    f"- Permintaan yang sedang saya jawab turn ini: {_status_permintaan}\n"
                    f"- Aksi terakhir: {_status_aksi}\n"
                    f"- Daftar todo: {_status_todo}\n\n"
                    f"**Pilihan Anda:**\n"
                    f"1. Ketik **'lanjut'** / **'continue'** bila saya di jalur yang benar — "
                    f"saat saya lanjut, permintaan yang dikutip di atas jadi prioritas pertama.\n"
                    f"2. Beri instruksi baru bila saya terlihat berputar-putar atau melenceng.\n",
                )

                # Save to history so Orion remembers it was already warned
                deps.context_manager.add_message(
                    "user", f"[SYSTEM WARNING]: {warning_msg}"
                )

                # Send to the frontend and PAUSE
                yield sse.ev('token', text=warning_msg)
                yield sse.ev('ask_user', question=_L(
                    state,
                    'Soft limit reached. Continue, or give new instructions?',
                    'Batas lunak tercapai. Lanjutkan, atau beri instruksi baru?',
                ))
                yield sse.ev('done')
                print(
                    "[ORION SOFT LIMIT] Reached 80% of iterations. Waiting for user instructions."
                )
                return  # Stop the stream, wait for the user to type 'continue'
            # >>> END OF SOFT LIMIT CHECKPOINT <<<

            # >>> STEP 2: REBUILD THE PAYLOAD FROM THE SOURCE OF TRUTH <<<
            # Do NOT use the accumulating local 'messages' variable. Take it fresh from the summarized history.
            current_history = deps.context_manager.get_history()
            api_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                deps.context_message,
            ] + current_history

            # >>> ATTACHMENT FEATURE (B4b): inject images only on the first iteration.
            # After that the history contains text placeholders, so later
            # iterations do not resend the base64 (saves free-tier quota).
            if _ == 0 and state.pending_image:
                for _i in range(len(api_messages) - 1, -1, -1):
                    if api_messages[_i].get("role") == "user":
                        api_messages[_i] = {**api_messages[_i], "content": state.pending_image}
                        break
                state.pending_image = None   # single use
                if deps.lampiran_desc:
                    yield deps.kirim_status(f'📎 {deps.lampiran_desc}')
            # >>> END OF ATTACHMENT FEATURE

            # >>> UPDATE-018: announce what the inbox drop-zone delivered on
            # this turn. Frozen 'status' event only (the 7-event SSE contract
            # is untouched); emitted once, on the first iteration, and never
            # when deps.inbox_desc is empty — so a project without an inbox
            # produces a byte-identical stream.
            if _ == 0 and deps.inbox_desc:
                yield deps.kirim_status(f'📥 {deps.inbox_desc}')
            # >>> END OF UPDATE-018 inbox frame

            # >>> STEP 3: SHORT REPLY ANTI-AMNESIA GUARD <<<
            if _ == 0:
                last_user_idx = -1
                for i in range(len(api_messages) - 1, -1, -1):
                    msg_content = api_messages[i].get("content", "")
                    if (
                        api_messages[i].get("role") == "user"
                        and "Current project structure" not in msg_content
                    ):
                        last_user_idx = i
                        break

                if last_user_idx >= 0:
                    last_user_msg = api_messages[last_user_idx]
                    user_content = last_user_msg.get("content") or ""
                    # >>> UPDATE-020 adendum-5: with image attachments the
                    # last user content is a vision-parts LIST (injected
                    # above). The short-reply guard below concatenates a
                    # reminder string onto it — list + str = TypeError, the
                    # owner's 2026-10-25 png crash. Length checks use a text
                    # view; the reminder is patched INTO the text part.
                    if isinstance(user_content, list):
                        uc_text = " ".join(
                            str(_p.get("text") or "")
                            for _p in user_content
                            if isinstance(_p, dict) and _p.get("type") == "text"
                        )
                    else:
                        uc_text = user_content

                    last_assistant_msg = None
                    for i in range(last_user_idx - 1, -1, -1):
                        if api_messages[i].get("role") == "assistant":
                            last_assistant_msg = api_messages[i]
                            break

                    # CRITICAL FIX: take the content ONLY IF last_assistant_msg is not None
                    last_assistant_content = (
                        (last_assistant_msg.get("content") or "")
                        if last_assistant_msg
                        else ""
                    )

                    # CRITICAL FIX: make sure last_assistant_msg is not None before checking its length
                    # >>> ANTI-AMNESIA FIX #2: the previous 500-char threshold on the assistant
                    # message defeated the guard exactly for SHORT & FOCUSED clarification
                    # questions (the most common case) — lowered to 30 chars and extended
                    # with the universal "ends with a question mark" detection.
                    if (
                        last_assistant_msg is not None
                        and len(uc_text) < 150
                        and len(uc_text) > 0
                        and len(last_assistant_content) > 30
                    ):
                        assistant_content_lower = last_assistant_content.lower()
                        has_options = last_assistant_content.rstrip().endswith(
                            "?"
                        ) or any(
                            keyword in assistant_content_lower
                            for keyword in [
                                "apakah anda ingin",
                                "opsi",
                                "pilihan",
                                "rekomendasi",
                                "saya sarankan",
                                "atau ",
                                "which would you",
                                "do you want",
                                "mau yang mana",
                                "pilih",
                            ]
                        )

                        if has_options:
                            reminder = (
                                "\n\n[SYSTEM CONTEXT REMINDER - MUST BE OBEYED]:\n"
                                "The user is ANSWERING a question/set of options that YOU yourself gave in your previous message.\n"
                                "1. ANSWER DIRECTLY based on the context of your previous recommendation.\n"
                                "2. Do NOT ask back 'what is it for'.\n"
                                # >>> UPDATE-025 commit 11: the old item 3
                                # banned reading "any documentation file" —
                                # which swallowed the user's OWN design
                                # handoffs (FRONTEND_HANDOFF.md stayed unread
                                # while the model invented a palette). The
                                # ban now covers only stale .orion/ claims;
                                # user references are MANDATORY reading.
                                "3. Do NOT call read_file on `.orion/` context files (stale claims from earlier sessions).\n"
                                "3b. Reference files the USER attached or named (design handoffs, prototypes, specs) are MANDATORY reading via read_file when the task implements them; durable copies live under .orion/attachments/.\n"
                                "4. Focus 100% on what the user chose and execute immediately.\n"
                                "[END OF REMINDER]"
                            )
                            if isinstance(user_content, str):
                                api_messages[last_user_idx]["content"] = (
                                    user_content + reminder
                                )
                            else:
                                # vision-parts list: fold the reminder into
                                # the leading text part (or add one), never
                                # concatenate list + str.
                                _patched = []
                                _done = False
                                for _p in user_content:
                                    if (
                                        not _done
                                        and isinstance(_p, dict)
                                        and _p.get("type") == "text"
                                    ):
                                        _patched.append(
                                            {**_p, "text": str(_p.get("text") or "") + reminder}
                                        )
                                        _done = True
                                    else:
                                        _patched.append(_p)
                                if not _done:
                                    _patched.insert(
                                        0, {"type": "text", "text": reminder.strip()}
                                    )
                                api_messages[last_user_idx]["content"] = _patched
                            print(
                                "[ORION ANTI-AMNESIA] Short reply detected. Context reminder injected."
                            )
            # >>> END OF ANTI-AMNESIA GUARD <<<

            # >>> STEP 4: AGGRESSIVE CONTEXT PRUNING <<<
            compressed_messages = []
            total_msgs = len(api_messages)  # ✅ FIXED: use api_messages

            for i, msg in enumerate(
                api_messages
            ):  # ✅ FIXED: iterate api_messages
                content_len = len(str(msg.get("content") or ""))

                # LAYER 3: trimming old history (safe, does not make the model dumb)
                # If a tool message is very old (>15 messages back), cut it to 500 chars
                if (
                    msg.get("role") == "tool"
                    and i < total_msgs - 15
                    and content_len > 500
                ):
                    compressed_msg = msg.copy()
                    compressed_msg["content"] = (
                        str(compressed_msg["content"])[:500]
                        + "\n\n[...TRUNCATED BECAUSE IT IS OLD; SEE THE ASSISTANT'S CONCLUSION...]"
                    )
                    compressed_messages.append(compressed_msg)

                # LAYER 1: lower the threshold from 5000 to 3000
                # >>> UPDATE-024 Commit 6 exemption: a sub-agent REPORT is
                # exactly the payload this rule used to gut — the placeholder
                # says "see the assistant's conclusion", but conclusions get
                # summarize-compressed and the findings evaporate (owner
                # transcript 2026-10-27). Reports stay whole; the findings
                # ledger holds the durable condensed copy regardless.
                elif (
                    msg.get("role") == "tool"
                    and content_len > 3000
                    and not str(msg.get("content") or "").startswith("[SUBAGENT REPORT")
                ):
                    if i < total_msgs - 2:
                        compressed_msg = msg.copy()
                        compressed_msg["content"] = (
                            "[THE PREVIOUS LONG OUTPUT HAS ALREADY BEEN PROCESSED BY THE ASSISTANT. See the following 'assistant' message for its conclusion. Do not re-read it.]"
                        )
                        compressed_messages.append(compressed_msg)
                    else:
                        compressed_messages.append(msg)
                else:
                    compressed_messages.append(msg)

            api_messages = compressed_messages
            # >>> END OF CONTEXT PRUNING <<<

            # >>> TOKEN BUDGET GUARD (Emergency Check) <<<
            # FIX: the estimate now counts tool_calls (name + arguments) too,
            # not just 'content' — it used to underestimate messages with large diffs/edits.
            estimated_tokens = sum(estimate_message_tokens(m) for m in api_messages)

            if estimated_tokens > token_budget_threshold:
                print(
                    f"[TOKEN GUARD] High token estimate ({estimated_tokens} > {token_budget_threshold}). Performing PERMANENT emergency pruning..."
                )

                # CRITICAL FIX: pruning is now applied DIRECTLY to the
                # context_manager (not just to the local api_messages variable)
                # using safe_split_index, so that:
                #   1) It need not be repeated every iteration (persistent).
                #   2) It never cuts an assistant.tool_calls and its tool response
                #      apart (which could cause the 400 error
                #      "tool_call_id not found").
                try:
                    pruned = prune_history_safely(
                        deps.context_manager,
                        keep_last_n=16,
                        notice=(
                            "[SYSTEM NOTICE]: Part of the old history was automatically pruned "
                            "because it approached the token limit. Important conclusions (files "
                            "changed, errors found) remain in the most recent messages."
                        ),
                    )
                    if pruned:
                        print(
                            "[TOKEN GUARD] context_manager history permanently pruned."
                        )
                except Exception as e:
                    print(
                        f"[TOKEN GUARD WARNING] Failed to prune history permanently: {e}"
                    )

                # Rebuild api_messages from the pruned history
                current_history = deps.context_manager.get_history()
                api_messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    deps.context_message,
                ] + current_history

                new_est = sum(estimate_message_tokens(m) for m in api_messages)
                print(f"[TOKEN GUARD] Successfully reduced to: {new_est} tokens")
            # >>> END OF TOKEN BUDGET GUARD <<<

            # >>> STEP 5: CLEAN CALL TO LLM_ROUTER (RESILIENT) <<<
            yield deps.kirim_status('⏳ Thinking...')

            response = None  # Reset the variable so it cannot use a stale value from the previous iteration
            start_time = asyncio.get_event_loop().time()

            # --- 1. CLEAN & MERGE MESSAGES (MUST BE BUILT FIRST) ---
            merged_messages = []
            for msg in api_messages:
                clean_msg = {"role": msg.get("role")}

                if "content" in msg and msg["content"] is not None:
                    # >>> UPDATE-020 adendum-5: vision-parts lists must stay
                    # LISTS. The blanket str() here stringified image
                    # attachments into a Python repr (latent bug since the
                    # attachment feature; invisible while the attach UI was
                    # dormant, found by the owner's first real png on
                    # 2026-10-25). estimate_message_tokens above already
                    # knows content can be a list - this line forgot.
                    clean_msg["content"] = msg["content"]
                elif clean_msg["role"] == "assistant":
                    clean_msg["content"] = ""

                if "tool_calls" in msg:
                    clean_msg["tool_calls"] = msg["tool_calls"]
                if "tool_call_id" in msg:
                    clean_msg["tool_call_id"] = msg["tool_call_id"]
                if "name" in msg:
                    clean_msg["name"] = msg["name"]

                # >>> CRITICAL FIX #1: NEVER MERGE 'tool' MESSAGES <<<
                # Every tool message MUST have its own tool_call_id matching
                # 1-to-1 with the tool_calls in the preceding assistant message. If two
                # consecutive tool messages are merged, the second one's tool_call_id is LOST
                # and the API will reject the request with a 400 error
                # ("tool_call_id ... not found" / "bad request").
                # >>> UPDATE-020 adendum-5: never merge when either side
                # carries vision parts - f-stringing a list into text would
                # reintroduce the stringification bug one line earlier.
                prev_content = (
                    merged_messages[-1].get("content", "") or ""
                    if merged_messages
                    else ""
                )
                curr_content = clean_msg.get("content", "") or ""
                if (
                    merged_messages
                    and merged_messages[-1]["role"] == clean_msg["role"]
                    and clean_msg["role"] != "tool"
                    and isinstance(prev_content, str)
                    and isinstance(curr_content, str)
                ):
                    merged_messages[-1][
                        "content"
                    ] = f"{prev_content}\n\n{curr_content}".strip()

                    if "tool_calls" in clean_msg:
                        if "tool_calls" not in merged_messages[-1]:
                            merged_messages[-1]["tool_calls"] = []
                        merged_messages[-1]["tool_calls"].extend(
                            clean_msg["tool_calls"]
                        )
                else:
                    merged_messages.append(clean_msg)

            clean_api_messages = merged_messages
            # --- END OF VARIABLE CREATION ---

            # --- 2. DEBUG: CHECK PAYLOAD SIZE (safe now that the variables exist) ---
            print(
                f"[DEBUG] Number of messages to send: {len(clean_api_messages)}"
            )
            print(f"[DEBUG] Number of tools to send: {len(tools_for_llm)}")
            # --- END OF DEBUG ---

            # --- 3. CALL THE LLM ---
            try:
                # >>> PATCH ORION-29: HEARTBEAT WHILE WAITING FOR THE LLM ==========
                # The UI used to be completely silent for 30-60 seconds during this await,
                # and that silence was indistinguishable from "dead" -- the last
                # remnant of the "stops silently" family of complaints.
                # Now a "still thinking... N seconds" status is sent every 15 seconds.
                #
                # The design DELIBERATELY avoids stream=True: assembling tool_calls
                # from a token stream is far more fragile, while a heartbeat provides
                # feedback without touching the response logic at all.
                # If the task raises, .result() re-raises it inside this try,
                # so the entire old recovery path stays intact.
                # ============================================================
                _loop_waktu = asyncio.get_running_loop()
                _tugas_llm = asyncio.create_task(
                    deps.llm_call(messages=clean_api_messages, tools=tools_for_llm)
                )
                _mulai = _loop_waktu.time()
                # >>> UPDATE-013 (SSE robustness): cancel guard. When the
                # client disconnects while we are waiting (VS Code closed,
                # chat refreshed, Stop pressed), uvicorn cancels this
                # response task: a CancelledError/GeneratorExit lands on one
                # of the awaits/yields below. The LLM call used to keep
                # running ORPHANED in the background — burning quota on an
                # answer nobody will ever read and littering the log with
                # "Task was destroyed but it is pending!". Now any abnormal
                # exit cancels the in-flight call before propagating.
                try:
                    while not _tugas_llm.done():
                        # NOTE: do NOT use `_` as a throwaway variable in this function.
                        # `_` is the agent LOOP COUNTER (for _ in range(max_iter));
                        # overwriting it makes `_ > 0` compare a set with an int.
                        # (Real bug ORION-29 v1: asyncio.wait returns two sets.)
                        _selesai, _tunggu_pending = await asyncio.wait({_tugas_llm}, timeout=15)
                        if not _selesai:
                            _detik = int(_loop_waktu.time() - _mulai)
                            yield deps.kirim_status(f'🤔 Still thinking... {_detik} seconds')
                    response = _tugas_llm.result()
                except BaseException:
                    if not _tugas_llm.done():
                        _tugas_llm.cancel()
                    raise
                # >>> END OF UPDATE-013 cancel guard

            except Exception as api_err:
                err_str = str(api_err)
                print(f"[ORION RECOVERY] Error detected: {err_str[:150]}")

                # 1. Check whether this is a 400 error (Context Length / Bad Request)
                is_400_error = (
                    "400" in err_str
                    or "context length" in err_str.lower()
                    or "bad request" in err_str.lower()
                )

                if is_400_error:
                    consecutive_400_errors += 1

                    # Check whether the same error repeats
                    current_error_msg = err_str[:200]
                    if (
                        consecutive_400_errors >= 2
                        and current_error_msg == last_400_error_msg
                    ):
                        # SAME ERROR REPEATED - STOP NOW
                        yield sse.ev('error', text=f'⚠️ The current model is probably INCOMPATIBLE with the request format. The same error occurred twice: {err_str[:250]}. Change DEFAULT_MODEL in your .env file or check the provider documentation.')
                        print(
                            "[ORION RECOVERY] ❌ Same 400 error repeated. Stopping to prevent an infinite loop."
                        )
                        break

                    last_400_error_msg = current_error_msg

                    print(
                        "[ORION RECOVERY] ⚠️ 400 error detected. Forcing emergency context pruning..."
                    )
                    try:
                        prune_history_safely(deps.context_manager, keep_last_n=8)
                        print("[ORION RECOVERY] ✅ History emergency-pruned.")
                    except Exception as e:
                        print(f"[ORION RECOVERY] Failed to prune history: {e}")
                else:
                    consecutive_400_errors = 0  # Reset on success
                    last_400_error_msg = ""

                # 2. Safe Yield (prevent a crash if the frontend already disconnected)
                try:
                    status_text = (
                        "⚠️ Model slow, switching to fallback..."
                        if not is_400_error
                        else "⚠️ Context too large, shrinking..."
                    )
                    yield deps.kirim_status(status_text)
                except (RuntimeError, asyncio.CancelledError):
                    print(
                        "[ORION RECOVERY] Frontend disconnected. Stopping stream safely."
                    )
                    break

                # 3. Inject a system message (only when it is not a 400 error)
                if not is_400_error:
                    deps.context_manager.add_message(
                        "user",
                        "[SYSTEM RECOVERY]: Connection lost. Continue with an alternative model.",
                    )

                continue  # Continue to the next iteration (the fallback will be tried by llm_router)
            # >>> END OF CLEAN CALL <<<

            if response is None:
                continue

            # >>> UPDATE-013 (SSE robustness): a provider that answers 200
            # with an EMPTY choices list used to raise IndexError here — a
            # mid-stream crash whose text ("list index out of range") means
            # nothing to a user. Report a clear error event instead and end
            # the run cleanly (ORION-2 contract: error is always followed by
            # done — the break exits the loop and jamin_done closes it).
            if not getattr(response, "choices", None):
                print("[ORION RECOVERY] Model returned an empty response (no choices).")
                yield sse.ev(
                    'error',
                    text=(
                        '⚠️ The model returned an empty response (no choices). '
                        'Send your message again — if it repeats, switch the model/provider.'
                    ),
                )
                break

            msg = response.choices[0].message

            # >>> PERFORMANCE LOGGING (SUCCESS - REAL TOKENS) <<<
            end_time = asyncio.get_event_loop().time()
            duration_ms = (end_time - start_time) * 1000
            usage = getattr(response, "usage", None)
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0
            actual_model_used = getattr(response, "model", "auto_rotated_model")

            proj_path = deps.tool_executor.project_path if deps.tool_executor else "."
            log_performance(
                proj_path,
                actual_model_used,
                input_tokens,
                output_tokens,
                duration_ms,
                "success",
            )
            # >>> END OF LOGGING <<<

            if getattr(msg, "tool_calls", None):
                yield deps.kirim_status('Processing tool...')

                if msg.content:
                    cleaned = fix_over_escaped_text(
                        strip_thinking_blocks(msg.content)
                    )
                    if cleaned:
                        yield sse.ev('token', text=cleaned + chr(10) + chr(10))

                reasoning_content = getattr(msg, "reasoning_content", None)

                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [],
                }
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                tool_results = []
                # >>> NEW: system notifications that MUST be deferred until AFTER
                # all tool_results are stored (so the message order stays valid:
                # assistant -> tool -> tool -> ... -> user notice).
                state.pending_system_notices.clear()

                for tc in msg.tool_calls:
                    func_name = tc.function.name
                    args_str = tc.function.arguments
                    tc_id = tc.id or f"call_{uuid.uuid4().hex[:10]}"

                    if not args_str or not args_str.strip():
                        args_str = "{}"

                    assistant_msg["tool_calls"].append(
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": func_name, "arguments": args_str},
                        }
                    )

                    try:
                        func_args = json.loads(args_str)
                        if func_args is None:
                            func_args = {}
                    except Exception as e:
                        print(f"[ORION] Error parsing args: {e}")
                        func_args = {}

                    # >>> UPDATE-025 hotfix: remember the most recent tool
                    # call (deterministic one-liner) so the soft-limit
                    # warning can name what this turn was actually doing.
                    # Recorded for EVERY tool, before dispatch — a failing
                    # tool is still the last action taken. <<<
                    state.last_tool_note = _catatan_tool(func_name, func_args)

                    # >>> UPDATE-025 hotfix-2 (external review note,
                    # 2026-10-28): the SHARED pre-call screening gate.
                    # Computed once for every tool; built-ins return ''
                    # (their teeth live inside tool_executor), MCP tools
                    # with mutating-looking names return a notice that is
                    # logged and prepended to the result below. Both
                    # dispatch branches pass through this ONE function so
                    # future shared teeth cannot drift apart again. <<<
                    _pre_notice = tool_guards.pre_call_check(
                        func_name,
                        func_args,
                        mcp=deps.mcp is not None and deps.mcp.has_tool(func_name),
                    )

                    # >>> CRITICAL FIX #2: ask_user MUST FLUSH the tool_results
                    # collected so far BEFORE returning, otherwise the earlier
                    # tool_calls in the same batch would never get their
                    # 'tool' replies in the history -> orphaned tool_call -> 400 error
                    # on the next request. <<<
                    if func_name == "ask_user":
                        question_text = fix_over_escaped_text(
                            func_args.get("question", "")
                        )
                        yield sse.ev('ask_user', question=question_text)

                        deps.context_manager.add_message(
                            "assistant",
                            msg.content,
                            tool_calls=assistant_msg["tool_calls"],
                            reasoning_content=reasoning_content,
                        )

                        # Flush all tool_results ALREADY processed in this batch
                        for tr in tool_results:
                            deps.context_manager.add_message(
                                "tool",
                                tr["content"],
                                tool_call_id=tr["tool_call_id"],
                            )

                        # IMPORTANT: store a placeholder tool message for the ask_user itself
                        deps.context_manager.add_message(
                            "tool",
                            "[AWAITING USER ANSWER]",
                            tool_call_id=tc_id,
                        )

                        # Flush any anti-loop notices that may have terkumpul
                        # accumulated BEFORE ask_user was called in the same tool_calls batch,
                        # so they are not lost due to the 'return' below.
                        for notice in state.pending_system_notices:
                            deps.context_manager.add_message("user", notice)

                        return  # STOP THE STREAM RIGHT NOW. DO NOT CONTINUE THE LOOP!
                    # >>> END OF FIX #2 (ask_user) <<<

                    try:
                        # >>> NEW: update_todos does not need tool_executor,
                        # so it is checked FIRST, before the tool_executor-is-None guard.
                        if func_name == "update_todos":
                            if deps.todo_manager is None:
                                result = "Info: The todo-list feature is currently unavailable (module not installed); continue without update_todos."
                            else:
                                # >>> UPDATE-025 hotfix-2: snapshot todo
                                # statuses BEFORE the rewrite, so every item
                                # that flips to 'completed' (or is created
                                # already completed) gets an evidence
                                # snapshot at that exact moment — see
                                # SessionState.bukti_todo and the checkpoint
                                # disclosure below. <<<
                                _status_sebelum = {
                                    str(t.get("id")): t.get("status")
                                    for t in deps.todo_manager.get_todos()
                                }
                                rendered = deps.todo_manager.set_todos(
                                    func_args.get("todos", [])
                                )
                                for _t in deps.todo_manager.get_todos():
                                    _tid = str(_t.get("id"))
                                    if _t.get("status") == "completed" and (
                                        _status_sebelum.get(_tid) != "completed"
                                    ):
                                        state.bukti_todo[_tid] = (
                                            len(state.files_changed_this_turn),
                                            state.terminal_ok,
                                            state.subagent_calls,
                                        )
                                yield sse.ev('todo_update', markdown=rendered, todos=deps.todo_manager.get_todos())
                                result = f"Todo list updated:\n{rendered}"

                        # >>> UPDATE-014: MCP tools — one dynamic branch for
                        # every namespaced tool (mcp__<server>__<tool>). Placed
                        # BEFORE the tool_executor guard because MCP tools do
                        # not need the built-in executor. The manager never
                        # raises: timeouts, dead servers and tool-level errors
                        # all come back as [MCP ...] strings, which the existing
                        # anti-loop guard (3 consecutive failures) protects
                        # exactly like any other tool result.
                        elif deps.mcp is not None and deps.mcp.has_tool(func_name):
                            yield deps.kirim_status(
                                f'🔌 MCP {deps.mcp.status_label(func_name)} ...'
                            )
                            result = await deps.mcp.call(func_name, func_args)
                            # >>> UPDATE-025 hotfix-2: surface the shared
                            # pre-call notice (logging-first, per the
                            # reviewer's suggestion): log it AND prepend
                            # it to the tool result so the unscreened
                            # path is visible in the transcript. <<<
                            if _pre_notice:
                                deps.logger.info(
                                    "[ORION PRE-CALL] %s", _pre_notice
                                )
                                result = _pre_notice + "\n" + str(result)
                        # >>> END OF UPDATE-014 MCP branch

                        elif deps.tool_executor is None:
                            result = "Error: Tool executor not initialized."

                        # >>> PATCH ORION-38 (Phase B): 14 pure tools are handled by
                        # the dispatch table in app/services/tool_dispatch.py.
                        # The complex/interactive branches remain below.
                        elif tool_dispatch.tool_sederhana(func_name):
                            _lm, _ls = tool_dispatch.label(func_name, func_args)
                            yield deps.kirim_status(_lm)
                            result = tool_dispatch.jalankan(func_name, deps.tool_executor, func_args)
                            # >>> UPDATE-025 hotfix-2: count successful
                            # terminal runs as work evidence for the
                            # todo-evidence disclosure (a command that
                            # errored or was rejected proves nothing). <<<
                            if func_name == "run_terminal" and isinstance(result, str) and (
                                not result.startswith(("Error", "❌", "[REJECTED"))
                            ):
                                state.terminal_ok += 1
                            if _ls:
                                yield deps.kirim_status(_ls)

                        # >>> UPDATE-016: sub-agent delegation (explorer /
                        # verifier / parallel workers). One shared branch:
                        # subagent_manager enforces the enabled flag, the
                        # per-turn budget, worker caps and disjoint write
                        # scopes, and NEVER raises - every failure arrives
                        # as an ordinary [SUBAGENT ...] tool-result string,
                        # so the existing anti-loop guard covers it too.
                        # Visibility uses the frozen 'status' event only.
                        elif subagent_manager.is_subagent_tool(func_name):
                            _sa_role = subagent_manager.role_of_tool(func_name)
                            if func_name == subagent_manager.TOOL_WORKERS:
                                _sa_tugas = func_args.get("tasks") or []
                                _sa_snippet = f"{len(_sa_tugas)} parallel task(s)"
                            else:
                                _sa_snippet = str(func_args.get("task") or "")[:80]
                            yield deps.kirim_status(
                                f'🤖 Sub-agent {_sa_role}: {_sa_snippet} ...'
                            )
                            # >>> UPDATE-025 commit 7 (the owner's "backend
                            # terputus padahal tidak"): a sub-run bundles
                            # several 60-160 s provider calls behind ONE bare
                            # await — minutes of zero bytes, past undici's
                            # ~300 s bodyTimeout. The keepalive wrapper keeps
                            # the SSE body flowing with comment bytes and, as
                            # a bonus, cancels the dispatch if the client
                            # really is gone (no more orphan sub-runs).
                            _hasil_dispatch: list = []
                            async for _ka in _keepalive(
                                subagent_manager.dispatch(
                                    func_name,
                                    func_args,
                                    state=state,
                                    llm_call=deps.llm_call,
                                    tool_executor=deps.tool_executor,
                                    config=deps.config,
                                    project_map=deps.context_manager.get_project_map(),
                                    logger=deps.logger,
                                ),
                                _hasil_dispatch,
                            ):
                                yield _ka
                            result = _hasil_dispatch[0]
                            yield deps.kirim_status(
                                f'🤖 Sub-agent {_sa_role} finished.'
                            )
                        # >>> END OF UPDATE-016 sub-agent branch

                        elif func_name == "read_file":
                            file_path = func_args.get("file_path", "")
                            # >>> UPDATE-025 commit 11: remember when the
                            # saved reference attachments were consulted,
                            # so the checkpoint can disclose UI work that
                            # shipped without ever opening them.
                            if ".orion/attachments/" in str(file_path).replace(
                                "\\", "/"
                            ):
                                state.lampiran_dibaca_turn_ini.add(str(file_path))
                                # >>> reference copies are EXEMPT from the
                                # redundancy guard: after summarization the
                                # inline copy is gone, re-reading the durable
                                # copy is the whole point.
                                _referensi = True
                            else:
                                _referensi = False
                            if file_path in state.analyzed_files and not _referensi:
                                result = f"[SYSTEM WARNING] REDUNDANCY DETECTED: You already read '{file_path}' in this session. The information is already in your memory. Do NOT re-read it. Continue the analysis or execution based on what you already know."
                            else:
                                yield deps.kirim_status(f' Reading file: {file_path}')
                                result = deps.tool_executor.read_file(**func_args)
                                state.analyzed_files.add(file_path)
                                yield deps.kirim_status(f'✅ File {file_path} read')

                        elif func_name == "read_file_range":
                            file_path = func_args.get("file_path", "")
                            start = func_args.get("start_line", 1)
                            end = func_args.get("end_line", 50)

                            # Build a unique key based on the file AND its line range
                            range_key = f"{file_path}:{start}-{end}"

                            # >>> UPDATE-025 commit 11: same reference ledger
                            # + redundancy exemption as the read_file branch.
                            _referensi = ".orion/attachments/" in str(
                                file_path
                            ).replace("\\", "/")
                            if _referensi:
                                state.lampiran_dibaca_turn_ini.add(str(file_path))

                            if range_key in state.analyzed_files and not _referensi:
                                result = f"[SYSTEM WARNING] REDUNDANCY DETECTED: You already read lines {start}-{end} of '{file_path}' in this session. Use the information you already have."
                            else:
                                yield deps.kirim_status(f'📖 Reading {file_path} (lines {start}-{end})')
                                result = deps.tool_executor.read_file_range(**func_args)

                                # Store this unique key, not just the file name
                                state.analyzed_files.add(range_key)

                                yield deps.kirim_status(f'✅ File {file_path} read')

                        elif func_name == "write_file":
                            file_path = func_args.get("file_path", "unknown")

                            # >>> NEW: PERMISSION/SANDBOXING GUARD <<<
                            # Check path traversal BEFORE touching the disk at all.
                            path_ok, path_reason = is_path_safe(
                                deps.tool_executor.project_path, file_path
                            )
                            if not path_ok:
                                result = f"🚫 [REJECTED BY SYSTEM - UNSAFE PATH]: {path_reason}"
                                # >>> UPDATE-022: a rejected write is a FAILED
                                # edit — the ledger must see it, or the model
                                # can claim "written" unchallenged.
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - UNSAFE PATH"
                                )
                                yield deps.kirim_status('🚫 File operation denied (outside the project folder)')
                            elif is_sensitive_file(file_path):
                                result = (
                                    f"🚫 [REJECTED BY SYSTEM - SENSITIVE FILE]: '{file_path}' was detected "
                                    f"as a sensitive file (credentials/keys). Not overwritten automatically. "
                                    f"If this is really necessary, ask the user to do it manually or "
                                    f"get explicit confirmation via ask_user first."
                                )
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - SENSITIVE FILE"
                                )
                                yield deps.kirim_status('🚫 Denied: sensitive file')
                            else:
                                yield deps.kirim_status(f'📝 Creating/overwriting file: {file_path}')
                                result = deps.tool_executor.write_file(**func_args)
                                # Only record as proof of real work when it truly succeeded
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    # >>> UPDATE-022: the log line used to say
                                    # "Wrote successfully" unconditionally — even
                                    # when write_file returned an Error string
                                    # (forensics noise, InvoiceFlow M1).
                                    print(
                                        f"[ORION WRITE_FILE] Wrote successfully: {file_path}"
                                    )
                                    state.catat_file_berubah(
                                        f"- `{file_path}` (write_file)"
                                    )
                                    state.catat_edit_sukses(file_path)
                                    # >>> NEW: AUTOMATIC POST-WRITE VERIFICATION <<<
                                    try:
                                        full_path = os.path.join(
                                            deps.tool_executor.project_path, file_path
                                        )
                                        verification_msg = verify_file_write(
                                            full_path,
                                            expected_content=func_args.get(
                                                "content"
                                            ),
                                        )
                                        result = f"{result}\n{verification_msg}"
                                    except Exception as verify_err:
                                        # A failed verification MUST NOT invalidate the original result --
                                        # this is an extra layer, not a replacement for write_file.
                                        print(
                                            f"[POST_EDIT_VERIFIER WARNING] {verify_err}"
                                        )
                                else:
                                    # >>> UPDATE-022: failure branch — record the
                                    # attempt in the ledger so a later completion
                                    # claim naming this file gets challenged.
                                    state.catat_edit_gagal(
                                        file_path,
                                        str(result).splitlines()[0][:200] if result
                                        else "tool returned no result",
                                    )
                                    print(
                                        f"[ORION WRITE_FILE] FAILED for {file_path}: "
                                        f"{str(result).splitlines()[0][:200] if result else 'no result'}"
                                    )
                                state.analyzed_files.discard(
                                    file_path
                                )  # FIX: invalidate the cache so it can be re-read if needed
                                # >>> UPDATE-022: the status frame used to claim
                                # "✅ created" even when the write failed — the UI
                                # layer of the same no-op-claim defect class.
                                if isinstance(result, str) and result.lower().startswith("success"):
                                    yield deps.kirim_status(f'✅ File {file_path} created')
                                else:
                                    yield deps.kirim_status(f'❌ File {file_path} NOT created (write failed)')

                        elif func_name == "edit_file":
                            file_path = func_args.get("file_path", "unknown")

                            # >>> NEW: PERMISSION/SANDBOXING GUARD <<<
                            path_ok, path_reason = is_path_safe(
                                deps.tool_executor.project_path, file_path
                            )
                            if not path_ok:
                                result = f"🚫 [REJECTED BY SYSTEM - UNSAFE PATH]: {path_reason}"
                                # >>> UPDATE-022: a rejected edit is a FAILED
                                # edit — see the write_file branch.
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - UNSAFE PATH"
                                )
                                yield deps.kirim_status('🚫 File operation denied (outside the project folder)')
                            elif is_sensitive_file(file_path):
                                result = (
                                    f"🚫 [REJECTED BY SYSTEM - SENSITIVE FILE]: '{file_path}' was detected "
                                    f"as a sensitive file (credentials/keys). Not edited automatically. "
                                    f"If this is really necessary, ask the user to do it manually or "
                                    f"get explicit confirmation via ask_user first."
                                )
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - SENSITIVE FILE"
                                )
                                yield deps.kirim_status('🚫 Denied: sensitive file')
                            else:
                                yield deps.kirim_status(f'🔧 Editing file: {file_path}')
                                result = deps.tool_executor.edit_file(**func_args)
                                # Only record as proof of real work when it truly succeeded
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    # >>> UPDATE-022: honest log — this line used
                                    # to print "Edited successfully" even when
                                    # edit_file returned an Error string.
                                    print(
                                        f"[ORION EDIT_FILE] Edited successfully: {file_path}"
                                    )
                                    state.catat_file_berubah(
                                        f"- `{file_path}` (edit_file)"
                                    )
                                    state.catat_edit_sukses(file_path)
                                    # >>> NEW: AUTOMATIC POST-EDIT VERIFICATION <<<
                                    try:
                                        full_path = os.path.join(
                                            deps.tool_executor.project_path, file_path
                                        )
                                        snippet = extract_replace_snippet(
                                            func_args.get("diff", "")
                                        )
                                        verification_msg = verify_file_edit(
                                            full_path, snippet
                                        )
                                        result = f"{result}\n{verification_msg}"
                                    except Exception as verify_err:
                                        print(
                                            f"[POST_EDIT_VERIFIER WARNING] {verify_err}"
                                        )
                                else:
                                    # >>> UPDATE-022: the InvoiceFlow M1 no-op —
                                    # "SEARCH block not found" and friends land
                                    # here; the ledger makes a later completion
                                    # claim naming this file challengeable.
                                    state.catat_edit_gagal(
                                        file_path,
                                        str(result).splitlines()[0][:200] if result
                                        else "tool returned no result",
                                    )
                                    print(
                                        f"[ORION EDIT_FILE] FAILED for {file_path}: "
                                        f"{str(result).splitlines()[0][:200] if result else 'no result'}"
                                    )
                                state.analyzed_files.discard(
                                    file_path
                                )  # FIX: invalidate the cache so it can be re-read if needed
                                # >>> UPDATE-022: failure-aware status frame —
                                # "✅ edited" on a failed edit was the UI layer
                                # of the no-op-claim defect class.
                                if isinstance(result, str) and result.lower().startswith("success"):
                                    yield deps.kirim_status(f'✅ File {file_path} edited')
                                else:
                                    yield deps.kirim_status(f'❌ File {file_path} NOT edited (edit failed)')

                        elif func_name == "run_terminal":
                            cmd_to_run = func_args.get("command", "")

                            try:
                                yield deps.kirim_status(f'⚙️ Running: {cmd_to_run}')

                                # 1. Check for a dangerous command
                                if is_dangerous_command(cmd_to_run):
                                    # >>> CRITICAL FIX #2 (continued): flush the earlier
                                    # tool_results first, then the assistant message,
                                    # then the confirmation placeholder. <<<
                                    deps.context_manager.add_message(
                                        "assistant",
                                        msg.content,
                                        tool_calls=assistant_msg["tool_calls"],
                                        reasoning_content=reasoning_content,
                                    )
                                    for tr in tool_results:
                                        deps.context_manager.add_message(
                                            "tool",
                                            tr["content"],
                                            tool_call_id=tr["tool_call_id"],
                                        )
                                    deps.context_manager.add_message(
                                        "tool",
                                        f"[AWAITING CONFIRMATION]: {cmd_to_run}",
                                        tool_call_id=tc_id,
                                    )
                                    # Flush any anti-loop notices that may have
                                    # accumulated before this confirmation was triggered.
                                    for notice in state.pending_system_notices:
                                        deps.context_manager.add_message("user", notice)
                                    yield sse.ev('confirm_terminal', command=cmd_to_run)
                                    result = "[AWAITING USER CONFIRMATION]"
                                    return  # Exit the current tool loop, let the frontend wait for the user

                                # 1b. Check for long-running server commands (HARD GUARD, not just a prompt plea)
                                # The is_long_running_server() function existed before but was
                                # never called — the LLM could simply ignore the prohibition
                                # in the system prompt. Now it is force-rejected at code level,
                                # BEFORE subprocess.Popen is called, so no server process is ever
                                # actually spawned and swelling the resources.
                                if is_long_running_server(cmd_to_run):
                                    result = (
                                        f"🚫 [REJECTED BY SYSTEM]: Command '{cmd_to_run}' was detected as "
                                        f"a server/process that runs forever (dev server, watch mode, etc). "
                                        f"This command was NOT executed, to prevent a hanging process and "
                                        f"system resource exhaustion.\n\n"
                                        f"[SYSTEM HINT]: If you need to verify that the server can run, use "
                                        f"a quick non-blocking check (e.g. a free-port check, `--version`, or "
                                        f"just a compile-check like `python -m py_compile`/`npm run build`). "
                                        f"If the user explicitly asked to run a server, tell the user to "
                                        f"run it themselves manually in their own terminal."
                                    )
                                    yield deps.kirim_status('🚫 Server command denied (prevents a hanging process)')

                                else:
                                    # 2. RUN WITH THE HEARTBEAT MECHANISM
                                    loop = asyncio.get_event_loop()
                                    # >>> PATCH ORION-9 (real bug, B023):
                                    # the lambda captured `cmd_to_run` BY REFERENCE.
                                    # If the executor only ran it after the loop moved on
                                    # to the next tool_call, the command executed would be
                                    # the WRONG one. Bound explicitly as a default argument
                                    # so its value is frozen.
                                    future = loop.run_in_executor(
                                        None,
                                        lambda _cmd=cmd_to_run: deps.tool_executor.run_terminal(
                                            command=_cmd, timeout=60
                                        ),
                                    )

                                    # 3. HEARTBEAT LOOP: send a signal to the frontend every 10 seconds
                                    while not future.done():
                                        yield deps.kirim_status('⏳ Process still running, please wait... (heartbeat)')
                                        await asyncio.sleep(10)

                                    # 4. Fetch the result after the process has truly finished
                                    result = future.result()
                                    yield deps.kirim_status('✅ Terminal command finished')

                            except (RuntimeError, asyncio.CancelledError):
                                # Caught when VS Code closes the chat/refreshes mid-process
                                print(
                                    "[ORION] Frontend connection lost during tool execution. Stopping process safely."
                                )
                                return  # Stop the generator entirely, do not continue to tool_results

                            except Exception as e:
                                # Catch other errors so result stays filled and can be reported to the LLM
                                result = f"Terminal execution error: {e!s}"
                                print(f"[ORION TOOL ERROR] {e}")
                                import traceback

                                traceback.print_exc()

                        # >>> PATCH ORION-14 (REAL BUG) ======================
                        # These three tools were DECLARED in the TOOLS schema and
                        # their methods EXIST in tool_executor.py, but they never had
                        # a dispatch branch -- so every time the model called them,
                        # what came back was "Unknown tool."
                        #
                        # Ironically, the system prompt around line ~956 REQUIRES the
                        # model to use web_search for unknown errors.
                        # Model obeys -> tool is dead -> model gets confused -> sometimes
                        # gives up without reporting. This was one of the causes of
                        # "stops silently" that had gone undetected until now.
                        # ==========================================================
                        elif func_name == "git_diff":
                            yield deps.kirim_status('🔀 Checking git diff...')
                            result = deps.tool_executor.git_diff(**func_args)

                        else:
                            # >>> PATCH ORION-14b: an actionable message.
                            # "Unknown tool." alone left the model guessing.
                            # Now the model gets the list of tools that actually work.
                            # UPDATE-014: healthy MCP tools join the list (with no
                            # MCP servers the sorted output is byte-identical to
                            # the pre-014 message).
                            _nama_tersedia = set(tool_dispatch.NAMA_TOOL_AKTIF)
                            if deps.mcp is not None:
                                _nama_tersedia |= deps.mcp.tool_names()
                            result = (
                                f"Error: tool '{func_name}' is NOT AVAILABLE. "
                                f"Tools that actually work: {sorted(_nama_tersedia)}. "
                                "Use one of those, or accomplish the goal another way. "
                                "Do NOT call the same tool again."
                            )
                            deps.logger.error("[DEAD TOOL] Model called %r which has no dispatch branch.", func_name)

                    except (RuntimeError, asyncio.CancelledError):
                        # CRITICAL FIX: prevent a crash/terminate when VS Code (frontend) drops the connection
                        print(
                            "[ORION] Frontend connection lost during tool execution. Stopping process safely (Graceful Shutdown)."
                        )
                        return

                    except Exception as e:
                        result = f"Error executing tool {func_name}: {e!s}"
                        print(f"[ORION TOOL ERROR] {e}")
                        traceback.print_exc()

                    res_str = (
                        str(result) if result is not None else "Tool returned None"
                    )
                    if not res_str.strip():
                        res_str = "Executed successfully, no output."

                    # >>> SMART TRUNCATION <<<
                    if len(res_str) > 15000:
                        res_str = res_str[:15000] + (
                            "\n\n[⚠️ SYSTEM WARNING: Output was too long and has been truncated.]"
                            "\n[Do NOT use 'read_file' again for this file.]"
                            "\n[CORRECT APPROACH: Use 'search_code' to find keywords/lines, "
                            "then use 'read_file_range' with specific start_line and end_line.]"
                        )

                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": func_name,
                            "content": res_str,
                        }
                    )

                    # >>> ANTI-LOOP CHECK (IMPROVED) <<<
                    current_action = f"{func_name}"
                    last_tool_calls.append(
                        {
                            "action": current_action,
                            "success": not (
                                "Error" in res_str
                                or "Traceback" in res_str
                                or "NOT_FOUND" in res_str
                            ),
                        }
                    )

                    # Keep only the last 3 actions
                    if len(last_tool_calls) > 3:
                        last_tool_calls.pop(0)

                    # If the last 3 actions are run_terminal/search_code etc. and ALL FAILED
                    if len(last_tool_calls) == 3:
                        all_failed = all(
                            not item["success"] for item in last_tool_calls
                        )
                        all_same_action = (
                            len(set(item["action"] for item in last_tool_calls))
                            == 1
                        )

                        if all_failed and all_same_action:
                            print(
                                f"[ORION ANTI-LOOP] Detected {func_name} failed 3x in a row. Forcing stop."
                            )
                            # >>> CRITICAL FIX #3: do NOT append to the local 'messages'
                            # variable (dead code - never sent to the LLM because
                            # api_messages is always rebuilt from context_manager
                            # every iteration). Defer this notice until assistant_msg +
                            # tool_results are stored, so the message order stays
                            # valid. <<<
                            state.tambah_notice(
                                f"[SYSTEM EMERGENCY STOP]: The command '{func_name}' has failed 3 times in a row. STOP trying variations of the same thing. Read the error message in the previous output carefully, report the core problem to the user, and ask for new instructions. Do NOT try running this command again."
                            )
                            # >>> PATCH ORION-35: AUTOMATIC RESEARCH WHEN STUCK ====
                            # This anti-loop moment used to mean "give up and report
                            # to the user". Now the backend performs the web search
                            # ITSELF on the last error and injects its findings, so
                            # Orion gets one chance at a different, findings-based
                            # approach before truly giving up. This is the code's
                            # answer to "when its knowledge is stuck, it browses":
                            # do not rely on the model remembering to search, because
                            # exactly when stuck it is most likely to forget
                            # to search.
                            # ==================================================
                            try:
                                _kueri_riset = research_rules.rancang_kueri_riset(
                                    func_name, res_str)
                                _temuan_riset = deps.tool_executor.web_search(
                                    _kueri_riset, max_results=3)
                                deps.logger.info("[AUTO RESEARCH] query=%s", _kueri_riset)
                            except Exception as _err_riset:  # noqa: BLE001
                                _temuan_riset = f"(automatic research failed: {_err_riset})"
                            state.tambah_notice(
                                "[SYSTEM - AUTOMATIC RESEARCH]: Before giving up and reporting to the "
                                "user, read the following web findings, then try ONE approach "
                                "that is DIFFERENT from what already failed, based on these findings:\n"
                                + str(_temuan_riset)[:2500]
                                + "\n\nOnly if these findings are inadequate, THEN report to the user: "
                                "what was tried, what was found, and what you recommend."
                            )
                            # >>> END OF PATCH ORION-35 =========================
                            last_tool_calls = []  # Reset after the warning
                    # >>> END OF ANTI-LOOP CHECK <<<

                # SAVE TO MEMORY IN REAL TIME
                deps.context_manager.add_message(
                    "assistant",
                    msg.content,
                    tool_calls=assistant_msg["tool_calls"],
                    reasoning_content=reasoning_content,
                )
                for tr in tool_results:
                    deps.context_manager.add_message(
                        "tool", tr["content"], tool_call_id=tr["tool_call_id"]
                    )
                    # The auto-checkpoint system message was removed to save tokens.
                    # The file state is already recorded in tool_calls and tool_results.

                # >>> Emergency (anti-loop) notices are only added NOW,
                # after assistant+tool are stored in the correct order. <<<
                for notice in state.pending_system_notices:
                    deps.context_manager.add_message("user", notice)

                # Self-Correction Injection (aggressive but controlled)
                has_terminal_error = False
                error_snippet = ""
                for tr in tool_results:
                    if tr["name"] == "run_terminal" and is_terminal_failure(
                        tr["content"]
                    ):
                        has_terminal_error = True
                        # 🛡️ SAFEGUARD: take only the first 3 lines of the error to save input tokens
                        error_snippet = "\n".join(
                            tr["content"].split("\n")[:3]
                        ).strip()
                        break

                if has_terminal_error:
                    deps.context_manager.add_message(
                        "user",
                        f"[SYSTEM AUTO-CORRECTION MODE]: A terminal command failed.\nError Snippet: {error_snippet}\n\nTASK: do NOT ask the user. Analyze this error. Do NOT use full read_file. Use read_file_range if you need to see context, then use edit_file to fix it RIGHT NOW. Perform at most 2 automatic fix iterations.",
                    )
                    print(
                        "[ORION SELF-CORRECT] Error detected. Forcing auto-fix mode (token-efficient)."
                    )

            else:
                # >>> FIX: fallback models (OpenRouter/Cloudflare) sometimes return the
                # two-character literal '\n' in ordinary answer text (not just inside
                # tool-call JSON arguments). fix_over_escaped_text() was already used for
                # the ask_user path, but this plain-text path was missed — which is why literal '\n'
                # still appeared on the user's screen for normal replies like the example above.
                content = fix_over_escaped_text(msg.content or "")
                reasoning_content = getattr(msg, "reasoning_content", None)

                # >>> PHASE C STEP 3: the four end-of-iteration gates (todo
                # nudge, claim challenge, checkpoint/pause, normal answer)
                # collapsed into ONE pure decision function; this branch is
                # now a match on its verdict. The gate rules and their
                # incident history live in loop_guards.py. Every side
                # effect (message storage, SSE yields, counters) stays in
                # the arms below, unchanged.
                keputusan = loop_guards.evaluasi_akhir_iterasi(
                    state,
                    content,
                    iterasi=_,
                    max_iter=max_iter,
                    sisa_todo=state.sisa_todo(deps.todo_manager),
                    maks_nudge=MAX_TODO_NUDGE,
                    maks_verifikasi=MAX_VERIFIKASI,
                )
                # >>> UPDATE-025 commit 12 (owner incident 2026-10-30: the
                # checkpoint disclosure FIRED — "references NOT consulted" —
                # and Orion still opened with "semua tugas selesai dan
                # terverifikasi", because every evidence check ran AFTER the
                # claim was already spoken: the blindfold came off past the
                # finish line). The reconciliation demand now lands INSIDE
                # the turn, at the exact second Orion tries to close it:
                # one challenge per turn; read the references or carry the
                # escalated disclosure out with you. <<<
                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                ) and _perlu_nudge_referensi(state, deps.tool_executor):
                    state.nudge_referensi += 1
                    _refs = _referensi_belum_dibaca(
                        state, getattr(deps.tool_executor, "project_path", ".")
                    )
                    deps.context_manager.add_message(
                        "assistant", content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - REFERENCE RECONCILIATION]: you are closing "
                            "a turn that changed UI files while the user's saved "
                            "reference attachments were never read: "
                            + ", ".join(f"`{r}`" for r in _refs)
                            + ". Before this turn may end, EITHER read them with "
                            "read_file and reconcile the design tokens (colors, "
                            "fonts, spacing, layout) against what you built, OR "
                            "state PLAINLY in your closing report, token by "
                            "token, WHERE your result deviates from the user's "
                            "reference and WHY. A completion claim without "
                            "either is a false claim.",
                            "[SISTEM - REKONSILIASI REFERENSI]: Anda hendak "
                            "menutup turn yang mengubah file UI sementara "
                            "lampiran referensi tersimpan milik user belum "
                            "dibaca sama sekali: "
                            + ", ".join(f"`{r}`" for r in _refs)
                            + ". Sebelum turn ini boleh berakhir, BACA dulu "
                            "dengan read_file lalu rekonsiliasi token desain "
                            "(warna, font, spasi, layout) terhadap apa yang "
                            "Anda bangun, ATAU nyatakan TERANG-TERANGAN di "
                            "laporan penutup, token per token, DI MANA hasil "
                            "Anda menyimpang dari referensi user dan MENGAPA. "
                            "Klaim selesai tanpa salah satu dari keduanya "
                            "adalah klaim palsu.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            '📖 UI changed with saved references unread - '
                            'reconciliation owed before this turn can close...',
                            '📖 UI berubah sementara referensi tersimpan belum '
                            'dibaca - rekonsiliasi wajib sebelum turn ini '
                            'boleh ditutup...',
                        )
                    )
                    continue
                match keputusan:
                    case loop_guards.Keputusan.TERUSKAN:
                        print(
                            "[ORION] Orion produced short text without a tool. Continuing iteration..."
                        )
                        deps.context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )

                        state.current_iter = _ + 1
                        continue
                    case loop_guards.Keputusan.PAUSE_ITERASI:
                        print(
                            "[ORION] Orion produced short text without a tool. Continuing iteration..."
                        )
                        deps.context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )

                        state.current_iter = _ + 1
                        pause_msg = f"\n\n[SYSTEM PAUSE]: I have reached the iteration limit ({max_iter}) for this session for token safety. However, I still remember every step I have taken. Please type **'lanjutkan'** or **'continue'** so I can finish this task."
                        yield sse.ev('token', text=pause_msg)
                        yield sse.ev('done')
                        print(
                            f"[ORION PAUSE] Reached iteration limit {max_iter}. Waiting for user instructions to continue."
                        )
                        break
                    case loop_guards.Keputusan.LANJUT_TODO:
                        _pending_items = state.sisa_todo(deps.todo_manager)
                        _next_todo = _pending_items[0]
                        state.nudge_count += 1
                        _next_id = str(_next_todo.get("id", "?"))
                        _next_text = str(_next_todo.get("text", ""))[:300]
                        _sisa = len(_pending_items)

                        # save this turn's progress so it is not lost from the context
                        deps.context_manager.add_message(
                            "assistant", content,
                            reasoning_content=reasoning_content,
                        )
                        # >>> UPDATE-025 hotfix (todo-hijack incident):
                        # a pending todo that ALREADY existed before this
                        # turn is NOT automatically the user's current task.
                        # Forcing "Work on item NOW" on such a stale item
                        # hijacked a frontend-pricing question into old
                        # Decimal work (owner transcript + [ORION NUDGE
                        # 1/10] log line, InvoiceFlow 2026-10-28). Stale
                        # next-item -> RE-ANCHOR notice: answer the current
                        # request first, surface the old todos, let the
                        # user decide. Todos created THIS turn keep the
                        # original forced continuation, byte-identical
                        # (PATCH ORION-1 "only does the first task"
                        # protection). <<<
                        if _next_id in state.todo_ids_awal_turn:
                            deps.context_manager.add_message(
                                "user",
                                "[SYSTEM - RE-ANCHOR]: The next pending todo item "
                                f"(`[{_next_id}]` {_next_text}) was ALREADY pending before "
                                "this turn started — it is not automatically the user's "
                                "current task.\n"
                                "The user's CURRENT request this turn is: "
                                f'"{state.permintaan_turn_ini[:300]}".\n'
                                "If that request is a continuation instruction "
                                "('lanjut'/'lanjutkan'/'continue'/'ya'), work on item "
                                f"`[{_next_id}]` now. Otherwise: answer the CURRENT request "
                                f"FIRST, then mention the {_sisa} older pending todo item(s) "
                                "and ASK the user whether to resume them. Do NOT silently "
                                "work on old todo items.",
                            )
                            yield deps.kirim_status(
                                f'🧭 Re-anchoring to your request (older todo [{_next_id}] deferred)...'
                            )
                            deps.logger.info(
                                "[ORION RE-ANCHOR %d/%d] next todo [%s] predates this turn; "
                                "re-anchoring to the current request instead of forcing it.",
                                state.nudge_count, MAX_TODO_NUDGE, _next_id,
                            )
                        else:
                            deps.context_manager.add_message(
                                "user",
                                "[SYSTEM - CONTINUE]: The first part has been done, but "
                                f"the user's task is NOT finished. There are still {_sisa} items on the todo list.\n"
                                f"Next item: `[{_next_id}]` {_next_text}\n\n"
                                "INSTRUCTION: do NOT conclude the task is complete and do NOT ask "
                                f"the user anything. Work on item `[{_next_id}]` NOW. Once it is truly "
                                "done, call the `update_todos` tool to mark it 'completed', "
                                "then continue to the next item.",
                            )
                            yield deps.kirim_status(f'Continuing with todo [{_next_id}]...')
                            deps.logger.info("[ORION NUDGE %d/%d] %d todos still pending. Continuing with [%s].",
                                        state.nudge_count, MAX_TODO_NUDGE, _sisa, _next_id)
                        yield sse.ev('todo_update', markdown=deps.todo_manager.render_markdown(), todos=deps.todo_manager.get_todos())
                        continue
                    case loop_guards.Keputusan.TANTANG_SARAN:
                        # >>> UPDATE-025 commit 9 (owner: "Orion harus bisa
                        # proaktif … tidak boleh seperti kuda yang ditutup
                        # matanya"): an OPEN FAILURE THREAD is waiting for a
                        # root cause + options, and this closing answer
                        # carried neither. One answer-first challenge per
                        # turn; the thread itself stays sticky across turns
                        # until advisory wording actually appears.
                        state.saran_count += 1
                        deps.logger.warning(
                            "[OPEN FAILURE THREAD] closing answer without "
                            "root-cause/options wording; challenge %d/1: %s",
                            state.saran_count, state.utas_gagal[:120],
                        )
                        deps.context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )
                        deps.context_manager.add_message(
                            "user",
                            _L(
                                state,
                                "[SYSTEM - PROACTIVE ADVISORY]: the user has an "
                            "OPEN FAILURE THREAD from an earlier message: "
                            f"\"{state.utas_gagal[:200]}\". Before closing this "
                            "turn, answer the current question FIRST in the "
                            "user's language, and inside that same answer add:\n"
                            "- the most likely ROOT CAUSE of that reported "
                            "failure, with the concrete evidence you actually "
                            "read (file + line, tool output, or an explicit "
                            "'I checked X and found Y');\n"
                            "- TWO or THREE concrete OPTIONS the user can pick, "
                            "smallest first, including the honest 'do nothing' "
                            "option where it applies.\n"
                            "This is not a ritual: weave both into the answer "
                            "as a senior engineer would. Do not defer with "
                            "'let me know if you want me to look'.",
                                "[SISTEM - PENASIHAT PROAKTIF]: user punya "
                                "UTAS KEGAGALAN TERBUKA dari pesan sebelumnya: "
                                f"\"{state.utas_gagal[:200]}\". Sebelum menutup "
                                "turn ini, jawab dulu pertanyaan saat ini DALAM "
                                "bahasa user, dan di dalam jawaban yang sama "
                                "tambahkan:\n"
                                "- PENYEBAB paling mungkin dari kegagalan yang "
                                "dilaporkan itu, dengan bukti konkret yang "
                                "benar-benar Anda baca (file + baris, keluaran "
                                "tool, atau pernyataan eksplisit 'saya memeriksa "
                                "X dan menemukan Y');\n"
                                "- DUA atau TIGA OPSI konkret yang bisa dipilih "
                                "user, dari yang terkecil, termasuk opsi jujur "
                                "'tidak melakukan apa-apa' bila relevan.\n"
                                "Ini bukan ritual: rajut keduanya ke dalam "
                                "jawaban seperti engineer senior. Jangan menunda "
                                "dengan 'beri tahu saya bila Anda ingin saya "
                                "memeriksa'.",
                            ),
                        )
                        yield deps.kirim_status(
                            '💡 Open failure thread: root cause + options owed...'
                        )
                        continue
                    case loop_guards.Keputusan.CHECKPOINT:
                        checkpoint_report = _L(
                            state,
                            "\n\n---\n\n"
                            "## ✅ TASK COMPLETED - CHECKPOINT REPORT\n\n"
                            "**Status:** Task completed\n\n"
                            "**Files changed:**\n",
                            "\n\n---\n\n"
                            "## ✅ TUGAS SELESAI - LAPORAN CHECKPOINT\n\n"
                            "**Status:** Tugas selesai\n\n"
                            "**File yang berubah:**\n",
                        )

                        # Use the list of files ACTUALLY changed ON THIS TURN ONLY,
                        # not a scan of the whole session history (which could show
                        # old changes from a previous task as if they just happened).
                        if state.files_changed_this_turn:
                            checkpoint_report += "\n".join(
                                state.files_changed_this_turn[-10:]
                            )  # At most the last 10 files
                        else:
                            checkpoint_report += _L(
                                state,
                                "- No files changed",
                                "- Tidak ada file yang berubah",
                            )

                        # >>> CRITICAL FIX: prevent todo items from silently "disappearing".
                        # Previously, if the user asked for 3 features but Orion only
                        # did 2, the checkpoint still said "task complete" without
                        # ever mentioning the missed 3rd item. Now, when todos are
                        # still pending/in_progress, the checkpoint MUST display them
                        # explicitly -- so the user (not just Orion) decides whether
                        # that really is enough or whether the work must
                        # continue.
                        pending_warning = ""
                        pending_items = state.sisa_todo(deps.todo_manager)
                        if pending_items:
                            pending_list = "\n".join(
                                f"- ⚠️ `[{t['id']}]` {t['text']} (status: {t['status']})"
                                for t in pending_items
                            )
                            pending_warning = (
                                _L(
                                    state,
                                    "\n\n**⚠️ WARNING: There are todo items still NOT finished:**\n",
                                    "\n\n**⚠️ PERINGATAN: Masih ada item todo yang BELUM selesai:**\n",
                                )
                                + f"{pending_list}\n\n"
                                + _L(
                                    state,
                                    "This task is NOT 100% complete -- the items above have not been done.",
                                    "Tugas ini BELUM 100% selesai -- item di atas belum dikerjakan.",
                                )
                            )

                        checkpoint_report += pending_warning

                        # >>> UPDATE-022: when the edit ledger recorded ANY
                        # failed attempt this turn, the checkpoint must show it
                        # (per-file, with the recovery state) — the user sees
                        # the same evidence the claim gate saw. Empty ledger →
                        # empty string → the report stays byte-identical to the
                        # pre-022 shape (SSE harness contract, §4 gate).
                        checkpoint_report += state.ringkasan_ledger()

                        # >>> UPDATE-025 hotfix-2 (commit 5 v1, InvoiceFlow
                        # incident): DISCLOSE todo items completed THIS turn
                        # with zero work evidence at their completion moment
                        # (no file change, no successful terminal run, no
                        # sub-agent run). Disclosure, not rejection — the
                        # user sees the same ledger the gates saw; hard
                        # per-item rejection comes after the evidence model
                        # proves itself in the field. <<<
                        _nol_bukti = [
                            tid for tid, ev in state.bukti_todo.items()
                            if not any(ev)
                        ]
                        if _nol_bukti:
                            _daftar_nol = "\n".join(
                                f"- ⚠️ `[{tid}]`" for tid in _nol_bukti
                            )
                            checkpoint_report += (
                                "\n\n**⚠️ Items marked complete with NO work "
                                "evidence this turn:**\n"
                                f"{_daftar_nol}\n\n"
                                "At the moment each item above was marked completed, "
                                "this turn had recorded no file change, no successful "
                                "terminal command and no sub-agent run. The work may "
                                "exist from an earlier turn or outside Orion — verify "
                                "these items manually before trusting this checkpoint."
                            )

                        # >>> UPDATE-025 commit 1 (InvoiceFlow round 2,
                        # 2026-10-28): the user's OWN message named sub-agent
                        # roles ("panggil delegate_verifier sebelum melaporkan
                        # selesai") yet the turn ended with zero successful
                        # dispatches to them, while the reply claimed
                        # "diverifikasi" on the strength of self-run builds.
                        # The checkpoint now discloses the gap: requested
                        # roles minus the turn's delegation ledger.
                        # Disclosure, not rejection — same v1 philosophy as
                        # the zero-evidence block above. <<<
                        # >>> UPDATE-025 commit 10: the checkpoint ending
                        # uses the same single merged block (claim=True by
                        # construction here) — no duplicate gap statement.
                        checkpoint_report += _blok_delegasi_akhir(
                            state, klaim=True
                        )
                        # >>> UPDATE-025 commit 11: UI work vs unread refs.
                        checkpoint_report += _blok_referensi_belum_dibaca(
                            state,
                            getattr(deps.tool_executor, "project_path", "."),
                        )
                        # >>> UPDATE-025 commit 14: unrecovered failed edits.
                        checkpoint_report += _blok_edit_gagal_akhir(state)
                        # >>> UPDATE-025 commit 9: same persistence fact and
                        # same thread-closing rule on the checkpoint ending.
                        checkpoint_report += _blok_persistensi_bila_perlu(
                            state, deps.tool_executor
                        )
                        _tutup_utas_bila_disarankan(state, content)

                        checkpoint_report += _L(
                            state,
                            "\n\n**Next steps:**\n"
                            "Please review the changes above. Is there anything to adjust, or shall I continue to the next task?\n\n"
                            "---\n",
                            "\n\n**Langkah berikutnya:**\n"
                            "Silakan tinjau perubahan di atas. Ada yang perlu disesuaikan, atau saya lanjut ke tugas berikut?\n\n"
                            "---\n",
                        )

                        # Show the checkpoint report in chat
                        yield sse.ev('token', text=strip_thinking_blocks(content) + checkpoint_report)

                        # Save to context
                        deps.context_manager.add_message(
                            "assistant",
                            content + checkpoint_report,
                            reasoning_content=reasoning_content,
                        )

                        # PAUSE: wait for the user's response
                        yield sse.ev('ask_user', question=_L(
                            state,
                            'Task complete. Is there anything to adjust, or shall I continue to the next task?',
                            'Tugas selesai. Ada yang perlu disesuaikan, atau saya lanjut ke tugas berikutnya?',
                        ))
                        yield sse.ev('done')
                        print(
                            "[ORION TASK COMPLETE] Task finished. Waiting for user response to continue."
                        )
                        break
                    case loop_guards.Keputusan.TANTANG_KLAIM:
                        state.verifikasi_count += 1
                        deps.logger.warning(
                            "[CLAIM WITHOUT EVIDENCE] Orion claims completion but 0 files changed "
                            "(soft one-shot challenge, UPDATE-023).",
                        )
                        deps.context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )
                        # >>> UPDATE-023: answer-first soft challenge. The old
                        # ORION-17b message forced a numbered evidence ritual
                        # ("1. state file+line 2. prove it 3. report it was
                        # done previously") and forbade asking the user
                        # anything — which produced two turns of ceremony
                        # that never answered the user's actual question
                        # (owner transcript, InvoiceFlow). The correction now
                        # (a) puts the user's question first, (b) teaches the
                        # fact-vs-claim wording that prevents re-triggering,
                        # and (c) fires at most ONCE per turn (loop_guards
                        # gate 4). Genuine failed-work claims are Gate 2's
                        # job (TANTANG_EDIT_GAGAL), which keeps full quota.
                        deps.context_manager.add_message(
                            "user",
                            _L(
                                state,
                                "[SYSTEM - EVIDENCE CHECK]: You used completion wording, but NO "
                                "file changed on this turn. The user's actual request this turn "
                                f"was: \"{(state.permintaan_turn_ini or '')[:300]}\" — answer IT "
                                "FIRST, directly, in their language. Then, inside that same "
                                "answer:\n"
                                "- If you were describing the EXISTING state of the project: cite "
                                "the concrete evidence inline (file + line, or the tool output) "
                                "and use factual wording ('the code exists at …', '7 tests pass') "
                                "instead of completion claims.\n"
                                "- If you were claiming work you should have done THIS turn: it "
                                "did not happen. Either do it now with edit_file/write_file, or "
                                "say plainly that it is not done yet.\n"
                                "Do not repeat completion wording without either the evidence or "
                                "the work, and do not perform numbered evidence rituals at the "
                                "user. This challenge is generated deterministically from this "
                                "turn's ledger — not a simulation, not a test, not stale context.",
                                "[SISTEM - PEMERIKSAAN BUKTI]: Anda memakai kata-kata penyelesaian, "
                                "tetapi TIDAK ADA file yang berubah pada turn ini. Permintaan asli "
                                f"user turn ini: \"{(state.permintaan_turn_ini or '')[:300]}\" — "
                                "jawab ITU lebih dulu, secara langsung, dalam bahasa user. Kemudian, "
                                "di dalam jawaban yang sama:\n"
                                "- Bila Anda menggambarkan kondisi proyek yang SUDAH ADA: kutip "
                                "bukti konkret inline (file + baris, atau keluaran tool) dan pakai "
                                "kata-kata faktual ('kode ada di …', '7 tes lolos'), bukan klaim "
                                "penyelesaian.\n"
                                "- Bila Anda mengklaim pekerjaan yang seharusnya dikerjakan TURN "
                                "INI: itu tidak terjadi. Kerjakan sekarang dengan edit_file/"
                                "write_file, atau katakan terus terang bahwa itu belum selesai.\n"
                                "Jangan mengulang kata-kata penyelesaian tanpa bukti atau "
                                "pekerjaan, dan jangan lakukan ritual bukti bernomor di hadapan "
                                "user. Tantangan ini dihasilkan deterministik dari ledger turn ini "
                                "— bukan simulasi, bukan tes, bukan konteks basi.",
                            ),
                        )
                        yield deps.kirim_status(_L(
                            state,
                            '🔍 Verifying completion claim (0 files changed)...',
                            '🔍 Memverifikasi klaim selesai (0 file berubah)...',
                        ))
                        continue
                    case loop_guards.Keputusan.TANTANG_EDIT_GAGAL:
                        # >>> UPDATE-022 (InvoiceFlow M1 §2C): the claim names
                        # work whose edit FAILED this turn and never succeeded
                        # afterwards. Other real edits do NOT excuse it — the
                        # challenge names every unrecovered file so the model
                        # cannot wave at the turn's successes in general.
                        # Shares the TANTANG_KLAIM quota (bounded loop) and
                        # mechanism; only the message and status text differ.
                        state.verifikasi_count += 1
                        _edit_gagal = state.edit_gagal_belum_pulih()
                        _daftar_gagal = "\n".join(
                            f"- `{g['path']}` — {g['alasan']}" for g in _edit_gagal
                        )
                        deps.logger.warning(
                            "[CLAIM WITH FAILED EDITS] Orion claims completion but %d "
                            "file edit(s) FAILED and never succeeded afterwards "
                            "(verification %d/%d). Challenging the claim: %s",
                            len(_edit_gagal), state.verifikasi_count, MAX_VERIFIKASI,
                            ", ".join(g["path"] for g in _edit_gagal),
                        )
                        deps.context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )
                        deps.context_manager.add_message(
                            "user",
                            _L(
                                state,
                                "[SYSTEM - YOUR CLAIM IS REJECTED]: You reported the work as "
                                "complete, but YOUR OWN EDITS FAILED this turn and never succeeded "
                                f"afterwards:\n{_daftar_gagal}\n\n"
                                "A completion claim that contradicts the edit ledger is not "
                                "accepted. Other files you DID change successfully do not excuse "
                                "the failures above.\n\n"
                                "The user's actual request this turn was: \""
                                f"{(state.permintaan_turn_ini or '')[:300]}\" — deliver EXACTLY "
                                "that, in their language; answer it directly FIRST. Then do the "
                                "following, in order:\n"
                                "1. For EACH failed file listed above: re-read its current content "
                                "(`read_file_range`), identify why the edit failed (stale SEARCH "
                                "block, wrong path, guard rejection), and retry with `edit_file`/"
                                "`write_file`.\n"
                                "2. Judge every retry ONLY by its tool result: only a result "
                                "starting with 'Success' counts; an 'Error:' result means still "
                                "failed.\n"
                                "3. If a file genuinely cannot be changed, say so PLAINLY in your "
                                "report (which file, which error) instead of claiming it is "
                                "done.\n\n"
                                "Do NOT repeat the completion claim while any file above is still "
                                "unfixed. This rejection is generated deterministically from YOUR "
                                "OWN edit ledger this turn — not a simulation, not a test, not "
                                "stale context.",
                                "[SISTEM - KLAIM ANDA DITOLAK]: Anda melaporkan pekerjaan selesai, "
                                "tetapi EDIT ANDA SENDIRI GAGAL pada turn ini dan tidak pernah "
                                f"sukses sesudahnya:\n{_daftar_gagal}\n\n"
                                "Klaim selesai yang bertentangan dengan ledger edit tidak "
                                "diterima. File lain yang BERHASIL Anda ubah tidak menebus "
                                "kegagalan di atas.\n\n"
                                "Permintaan asli user turn ini: \""
                                f"{(state.permintaan_turn_ini or '')[:300]}\" — kirimkan PERSIS "
                                "itu, dalam bahasa user; jawab langsung lebih dulu. Kemudian "
                                "lakukan berurutan:\n"
                                "1. Untuk SETIAP file gagal di atas: baca ulang isi terkini "
                                "(`read_file_range`), identifikasi kenapa edit gagal (SEARCH block "
                                "basi, path salah, penolakan guard), lalu ulangi dengan "
                                "`edit_file`/`write_file`.\n"
                                "2. Nilai setiap percobaan HANYA dari hasil tool-nya: hanya hasil "
                                "diawali 'Success' yang dihitung; hasil 'Error:' berarti masih "
                                "gagal.\n"
                                "3. Bila file benar-benar tidak bisa diubah, katakan TERUS TERANG "
                                "di laporan (file mana, error apa) alih-alih mengklaim selesai.\n\n"
                                "JANGAN mengulang klaim selesai selama file di atas belum beres. "
                                "Penolakan ini dihasilkan deterministik dari ledger edit ANDA "
                                "SENDIRI turn ini — bukan simulasi, bukan tes, bukan konteks basi.",
                            ),
                        )
                        _nama_gagal = ", ".join(f"`{g['path']}`" for g in _edit_gagal[:5])
                        if len(_edit_gagal) > 5:
                            _nama_gagal += f" +{len(_edit_gagal) - 5} more"
                        yield deps.kirim_status(_L(
                            state,
                            f'🔍 Verifying completion claim (failed edits: {_nama_gagal})...',
                            f'🔍 Memverifikasi klaim selesai (edit gagal: {_nama_gagal})...',
                        ))
                        continue
                    case loop_guards.Keputusan.JAWAB_NORMAL:
                        # Not a task completion, just a normal response
                        # >>> UPDATE-025 commits 5+8+10: claims routed here
                        # (read-only turns, post-pause resumes) carry the
                        # single merged delegation block — fate-accurate,
                        # claim-aware, never duplicated. <<<
                        _teks_jawab = strip_thinking_blocks(content)
                        # >>> UPDATE-025 commit 9: the persistence fact rides
                        # BEFORE the thread can close; closing needs the
                        # model's own advisory wording, checked on `content`.
                        _teks_jawab += _blok_persistensi_bila_perlu(
                            state, deps.tool_executor
                        )
                        _tutup_utas_bila_disarankan(state, content)
                        # >>> UPDATE-025 commit 10: ONE merged delegation
                        # block (fate-accurate, claim-aware) replaces the
                        # old pair — pipeline-broke (commit 8) plus the
                        # claim disclosure (commit 5) — which could state
                        # the same gap twice and contradict each other.
                        _teks_jawab += _blok_delegasi_akhir(
                            state, klaim=guard_rules.kata_klaim(content)
                        )
                        # >>> UPDATE-025 commit 14: unrecovered failed edits
                        # named at the normal ending too (single emitter,
                        # both endings — no duplicate voice).
                        _teks_jawab += _blok_edit_gagal_akhir(state)
                        yield sse.ev('token', text=_teks_jawab)
                        deps.context_manager.add_message(
                            "assistant",
                            _teks_jawab,
                            reasoning_content=reasoning_content,
                        )
                        yield sse.ev('done')
                        break
                    # >>> END OF TASK COMPLETION DETECTED <<<

    except Exception as e:
        error_msg = str(e)
        # >>> PATCH ORION-2: use the logger, not print (stdout = pipe to the extension)
        deps.logger.exception("[ORION EXCEPTION] agent loop failed")
        yield sse.ev('error', text=error_msg)
        # >>> PATCH ORION-2 (CRITICAL): MUST send 'done' after 'error'.
        # Previously only 'error' was sent, so the webview never got a
        # completion signal -> the UI hung and the user saw the assistant
        # "stop silently".
        yield sse.ev('done')

