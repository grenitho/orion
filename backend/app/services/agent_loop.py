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
import traceback
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import tiktoken

from app.services import guard_rules, loop_guards, research_rules
from app.services import sse, stream_utils, tool_dispatch
from app.services import subagent_manager, tool_guards
from app.services.llm_router import get_ai_response
from app.services.perf_log import log_performance
from app.services.prompt_cache import ekstrak_cached_tokens
from app.services.prompts import SYSTEM_PROMPT
from app.services.session_state import SessionState
from app.services.text_cleaner import (
    estimate_tokens,
    fix_over_escaped_text,
    strip_thinking_blocks,
)
from app.services.tools_schema import TOOLS

_THINKING_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_token_encoder = tiktoken.get_encoding("cl100k_base")


def estimate_message_tokens(msg: dict) -> int:
    """Estimate tokens for 1 message, INCLUDING tool_calls (name + arguments).
    Previously it only counted 'content', so messages with large tool_calls
    (e.g. a long 'diff' in edit_file) were underestimated."""
    _c = msg.get("content")
    if isinstance(_c, list):
        _teks = " ".join(
            str(p.get("text", ""))
            for p in _c
            if isinstance(p, dict) and p.get("type") == "text"
        )
        # Gemini charges +/- 258 tokens per image, far cheaper than its base64
        _n_img = sum(
            1 for p in _c if isinstance(p, dict) and p.get("type") == "image_url"
        )
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


SSE_KEEPALIVE_SEC = float(os.getenv("ORION_SSE_KEEPALIVE_SEC", "20"))


async def _keepalive(coro, hasil: list, interval: float | None = None):
    """Run ``coro`` as a task, yielding SSE keepalive comments while it is
    silent; append its result to ``hasil``."""
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


from app.services.agent_disclosures import (  # noqa: E402, F401
    _KLAIM_KERJA_ORANG_PERTAMA,
    _L,
    _REFUSED_MCP,
    _TOOL_MUTASI,
    _URL_ARG_KEYS,
    _blok_debt_verifikasi,
    _blok_delegasi_akhir,
    _blok_edit_gagal_akhir,
    _blok_gaya_tanpa_verifikasi,
    _blok_persistensi_bila_perlu,
    _blok_referensi_belum_dibaca,
    _blok_todo_nol_bukti,
    _browser_hasil_sukses,
    _catatan_tool,
    _fallback_ipv4_bila_ditolak,
    _kalimat_referensi_ada,
    _perlu_nudge_gaya,
    _perlu_nudge_referensi,
    _perlu_tantang_klaim_implementasi,
    _perlu_tantang_klaim_visual,
    _perlu_tantang_nihil_mutasi,
    _perlu_tantang_perintah_tool,
    _permintaan_pengguna_terakhir,
    _pesanan_verbatim,
    _referensi_belum_dibaca,
    _teks_user,
    _token_desain_dari_referensi,
    _tool_browser_mcp,
    _tutup_utas_bila_disarankan,
    _ui_berubah_dan_referensi,
    _url_fallback_ipv4,
    blok_jangkar_tugas,
)


async def summarize_old_history(old_messages: list) -> str:
    """Call the LLM to summarize old chat history.
    IMPORTANT: this function does NOT swallow exceptions. If the API fails, the error is raised to the caller.
    """
    prompt = (
        "Summarize the following conversation into 1 dense paragraph containing "
        "the main context, the decisions made, and the current task status. "
        "List changed files WITH their paths. Quote VERBATIM (between quotes) "
        "any user order that is still unfinished. Ignore pleasantries.\n\n"
    )
    for msg in old_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if content:
            prompt += f"{role}: {content[:500]}\n"

    response = await get_ai_response(
        messages=[{"role": "user", "content": prompt}], tools=None
    )
    return response.choices[0].message.content


async def execute_summarize(context_manager, history, threshold=15):
    """Helper that runs the sliding-window summarize. Returns True on success."""
    if len(history) <= threshold:
        return False

    split_idx = context_manager.safe_split_index(history, 10)
    messages_to_summarize = history[:split_idx]
    recent_messages = history[split_idx:]

    prior = ""
    if messages_to_summarize and str(
        messages_to_summarize[0].get("content", "")
    ).startswith("[SUMMARY OF PREVIOUS SESSION]"):
        prior = str(messages_to_summarize[0].get("content", ""))[:2400]
        messages_to_summarize = messages_to_summarize[1:]

    prompt = "Summarize the following conversation and tool results into 1 dense paragraph. Focus on: which files were read/changed, which errors were found, and what the next step is. Ignore pleasantries.\n\n"
    for msg in messages_to_summarize:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))[:1000]
        prompt += f"{role}: {content}\n"

    context_manager.create_backup()

    if messages_to_summarize:
        summary_text = await summarize_old_history(messages_to_summarize)
    else:
        summary_text = "(no new window; prior summary carried verbatim)"
    pesanan = _pesanan_verbatim(messages_to_summarize)

    gabung = "[SUMMARY OF PREVIOUS SESSION]: "
    if prior:
        gabung += f"{prior}\n\nSINCE THEN: {summary_text}"
    else:
        gabung += summary_text
    if pesanan:
        gabung += (
            "\n\nUNFINISHED USER ORDERS from the compressed window, quoted "
            "verbatim (context for continuity — the latest user message "
            "stays the authority):\n" + pesanan
        )
    context_manager.clear_history()
    context_manager.add_message("user", gabung)
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
    using safe_split_index."""
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


@dataclass
class Deps:
    """Explicit dependencies of the agent loop (Phase C step 4)."""

    context_manager: object
    todo_manager: object | None
    tool_executor: object | None
    llm_call: Callable  # get_ai_response (heartbeat wrapper stays in the loop)
    kirim_status: Callable[[str], str]  # teks -> SSE 'status' frame (sse.ev partial)
    config: dict  # orion_config: max_iterations, token_budget_threshold
    logger: object
    context_message: dict  # "Current project structure" + editor context
    lampiran_desc: str = ""  # one-line attachment description (may be empty)
    mcp: object | None = None
    inbox_desc: str = ""


def _rakit_api_messages(
    system_prompt: str, context_message: dict, history: list
) -> list:
    """Tempatkan context_message dinamis tepat sebelum pesan user terakhir
    agar prefix system + riwayat chat tetap stabil untuk prompt caching."""
    if not history:
        return [{"role": "system", "content": system_prompt}, context_message]

    idx_user_terakhir = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            idx_user_terakhir = i
            break

    if idx_user_terakhir < 0:
        return [{"role": "system", "content": system_prompt}, context_message] + history

    return (
        [{"role": "system", "content": system_prompt}]
        + history[:idx_user_terakhir]
        + [context_message]
        + history[idx_user_terakhir:]
    )


async def aliran_agent(state: SessionState, deps: Deps) -> AsyncIterator[str]:
    """The agent loop: one /chat turn as an async iterator of SSE frames."""
    history = deps.context_manager.get_history()
    if len(history) > 15:
        yield deps.kirim_status("🧠 Memory full, compressing context...")
        try:
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

    max_iter = deps.config.get("max_iterations", 20)
    if not isinstance(max_iter, int) or max_iter < 1:
        max_iter = 20
    max_iter = min(max_iter, 200)
    max_iter = min(max(max_iter, state.cap_iterasi_maks(max_iter)), 200)

    token_budget_threshold = deps.config.get("token_budget_threshold", 12000)
    if not isinstance(token_budget_threshold, int) or token_budget_threshold < 1000:
        token_budget_threshold = 12000

    print(
        f"[ORION CONFIG] Config file read: {os.path.join(deps.tool_executor.project_path, '.orion', 'orion_config.json')}"
    )
    print(f"[ORION CONFIG] max_iterations value in use: {max_iter}")
    print(f"[ORION CONFIG] Token budget threshold: {token_budget_threshold}")

    tools_for_llm = TOOLS
    if deps.mcp is not None:
        _mcp_tools = deps.mcp.openai_tools()
        if _mcp_tools:
            tools_for_llm = TOOLS + _mcp_tools
            print(
                f"[ORION MCP] {len(_mcp_tools)} MCP tool(s) added to this turn's schema."
            )
    if subagent_manager.is_strict_worker_coding(deps.config):
        tools_for_llm = [
            t
            for t in tools_for_llm
            if t.get("function", {}).get("name") not in ("write_file", "edit_file")
        ]
        print(
            "[ORION STRICT WORKER MODE] Removed write_file/edit_file from main-agent tool schema."
        )

    try:
        last_tool_calls = []

        consecutive_400_errors = 0
        last_400_error_msg = ""

        MAX_TODO_NUDGE = int(os.getenv("ORION_MAX_TODO_NUDGE", "10"))
        MAX_VERIFIKASI = int(os.getenv("ORION_MAX_VERIFIKASI", "2"))

        state.permintaan_turn_ini = _permintaan_pengguna_terakhir(
            deps.context_manager.get_history()
        )
        _teks_murni, state.lampiran_blok = guard_rules.pisahkan_lampiran(
            state.permintaan_turn_ini
        )
        state.permintaan_turn_ini = _teks_murni

        if guard_rules.adalah_kata_lanjut(_teks_murni):
            for _m_prev in reversed(deps.context_manager.get_history()):
                _teks = _teks_user(_m_prev)
                if not _teks:
                    continue
                _t_prev_murni = guard_rules.pisahkan_lampiran(_teks)[0]
                if _t_prev_murni and not guard_rules.adalah_kata_lanjut(_t_prev_murni):
                    _teks_murni = _t_prev_murni
                    state.permintaan_turn_ini = _t_prev_murni
                    break

        state.bahasa_turn = guard_rules.deteksi_bahasa(state.permintaan_turn_ini)

        _tool_hilang = guard_rules.tool_dinamai_tak_tersedia(
            state.permintaan_turn_ini,
            deps.mcp.tool_names() if getattr(deps, "mcp", None) else set(),
        )
        if _tool_hilang and not state.probe_tool_hilang:
            state.probe_tool_hilang = 1
            _daftar_hilang = ", ".join(f"`{n}`" for n in _tool_hilang)
            deps.context_manager.add_message(
                "user",
                _L(
                    state,
                    f"[SYSTEM - TOOL AVAILABILITY]: this turn's order names "
                    f"tool(s) {_daftar_hilang} which are NOT available in the "
                    "MCP configuration connected right now. Do not attempt "
                    "them; decline that part honestly in your first answer "
                    "and serve the rest of the order.",
                    f"[SISTEM - KETERSEDIAAN TOOL]: perintah turn ini menyebut "
                    f"tool {_daftar_hilang} yang TIDAK tersedia di konfigurasi "
                    "MCP yang terhubung sekarang. Jangan mencoba memanggilnya; "
                    "tolak bagian itu dengan jujur pada jawaban pertama dan "
                    "kerjakan sisa perintah.",
                ),
            )
            yield deps.kirim_status(
                _L(
                    state,
                    f"🔌 Tool not connected this turn: {_daftar_hilang}",
                    f"🔌 Tool tidak terhubung turn ini: {_daftar_hilang}",
                )
            )

        if state.lampiran_blok and not state.lampiran_note_turn:
            state.lampiran_note_turn = 1
            deps.context_manager.add_message(
                "user",
                _L(
                    state,
                    "[SYSTEM - ATTACHMENT]: the block 'ATTACHED FILE CONTENT' "
                    "inside the user message IS the attached file (text "
                    "attachments are pasted inline by design). Study that "
                    "block directly; NEVER answer 'the file does not exist "
                    "on disk' for it.",
                    "[SISTEM - LAMPIRAN]: blok 'ATTACHED FILE CONTENT' di "
                    "dalam pesan user ADALAH file lampiran itu sendiri "
                    "(lampiran teks ditempel inline sesuai desain). Pelajari "
                    "blok itu langsung; JANGAN pernah menjawab 'file tidak "
                    "ada di disk' untuknya.",
                ),
            )
            yield deps.kirim_status(
                _L(
                    state,
                    "📎 Attachment content read from the message block",
                    "📎 Isi lampiran dibaca dari blok pesan",
                )
            )
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

        if subagent_manager.is_strict_worker_coding(deps.config) and isinstance(
            deps.context_message, dict
        ):
            deps.context_message["content"] = str(
                deps.context_message.get("content", "")
            ) + (
                "\n[STRICT WORKER CODING MODE]: Direct `write_file` and `edit_file` "
                "calls by the main agent are BLOCKED in this project. Use the main "
                "agent (or `delegate_explorer`) ONLY for reading files, inspecting "
                "references, searching code, running terminal/verification checks, "
                "and planning. For EVERY code creation or edit — even a 1-line fix "
                "— delegate to `delegate_workers` with exact `files` scopes and "
                "complete, self-contained instructions (exact paths, design tokens, "
                "and required changes) so the worker can execute without extra discovery."
            )

        state.todo_ids_awal_turn = {
            str(t.get("id")) for t in state.sisa_todo(deps.todo_manager)
        }
        state.delegasi_terpakai = set()
        state.nasib_role = {}
        state.nudge_count = 0
        state.verifikasi_count = 0
        state.saran_count = 0
        state.lampiran_dibaca_turn_ini = set()
        state.nudge_referensi = 0
        state.gaya_diperiksa_turn_ini = False
        state.nudge_gaya = 0
        state.tantang_klaim_visual = 0
        state.tantang_klaim_implementasi = 0
        state.tantang_perintah_tool = 0
        state.tantang_nihil_mutasi = 0
        state.fallback_ipv4_count = 0
        state.panggilan_browser_turn_ini = 0
        state.turn_ke += 1
        state.nir_mutasi_beruntun = 0
        state.tulis_akhir_iterasi = 0
        state.edit_gagal_beruntun = 0
        state.tantang_stagnasi = 0
        state.gaya_bukti_turn = []
        state.nudge_ledger = 0

        # Derive explicit tool orders and zero-mutation hard blocks from
        # `_teks_murni`, which already strips attachments and resolves
        # short "lanjut" continuations back to the substantive order.
        _p29, _butuh29 = guard_rules.perintah_verifikasi_gaya(_teks_murni)
        state.perintah_tool_eksplisit = _p29
        state.perintah_butuh_gaya = _butuh29

        _zm31, _q31 = guard_rules.zero_mutasi_diperintah(_teks_murni)
        state.zero_mutasi_turn = _zm31
        state.kutipan_zero_mutasi = _q31

        _utas_baru = guard_rules.utas_gagal_dari(_teks_murni)
        if _utas_baru:
            state.utas_gagal = _utas_baru
            state.fakta_persistensi_disampaikan = False

        # >>> CONVENTION: `_` below is the agent LOOP COUNTER.
        # Never use `_` as a throwaway variable inside this loop
        # (see test_loop_counter_guard.py which guards this).
        for _ in range(max_iter):
            _tulis_kini = len(state.files_changed_this_turn)
            if _ == 0 or _tulis_kini != state.tulis_akhir_iterasi:
                state.nir_mutasi_beruntun = 0
            else:
                state.nir_mutasi_beruntun += 1
            state.tulis_akhir_iterasi = _tulis_kini

            # Circuit breaker: 12+ no-mutation iterations AND 3+ consecutive
            # failed writes = thrash, not work. Emit a clear closing message so
            # the user never sees an empty response bubble.
            if loop_guards.stagnasi_penuh(state):
                state.catat_debt_verifikasi(
                    "stall",
                    state.permintaan_turn_ini or state.perintah_tool_eksplisit,
                )
                yield deps.kirim_status(
                    _L(
                        state,
                        "🛑 Stagnation breaker: 12+ iterations without a "
                        "successful mutation and 3+ consecutive failed "
                        "writes - stopping the thrash, debt recorded...",
                        "🛑 Pemutus stagnasi: 12+ iterasi tanpa mutasi sukses "
                        "dan 3+ tulis gagal beruntun - thrash dihentikan, "
                        "utang dicatat...",
                    )
                )
                _teks_stagnasi = _L(
                    state,
                    "⚠️ **Execution stopped (Stagnation Breaker):** 12+ iterations elapsed "
                    "without a successful file change and 3+ consecutive write/edit attempts "
                    "failed. Verification debt `[stall]` has been recorded so you can review "
                    "or give narrower instructions.",
                    "⚠️ **Eksekusi dihentikan (Pemutus Stagnasi):** 12+ iterasi berlalu "
                    "tanpa perubahan file yang berhasil dan 3+ percobaan tulis/edit gagal "
                    "beruntun. Utang verifikasi `[stall]` telah dicatat agar Anda dapat "
                    "meninjau atau memberi instruksi yang lebih spesifik.",
                ) + _blok_edit_gagal_akhir(state)
                yield sse.ev("token", text=_teks_stagnasi)
                deps.context_manager.add_message("assistant", _teks_stagnasi)
                yield sse.ev("done")
                break

            # >>> STEP 1: DYNAMIC SLIDING WINDOW SUMMARIZE (INSIDE THE LOOP) <<<
            history = deps.context_manager.get_history()
            if len(history) > 20:
                yield deps.kirim_status("🧠 Memory full, compressing context...")
                try:
                    _hasil_summarize_loop: list = []
                    async for _ka in _keepalive(
                        execute_summarize(deps.context_manager, history, threshold=20),
                        _hasil_summarize_loop,
                    ):
                        yield _ka
                    print("[ORION SUMMARIZE] In-loop context summarized successfully.")
                except Exception as e:
                    print(
                        f"[ORION SUMMARIZE WARNING] In-loop summarization failed: {e}. History preserved."
                    )

            # >>> NEW: SOFT LIMIT CHECKPOINT (Human-in-the-Loop) <<<
            soft_limit = int(max_iter * 0.8)
            if _ == soft_limit:
                _permintaan = state.permintaan_turn_ini.strip()
                if len(_permintaan) > 300:
                    _permintaan = _permintaan[:300] + " …"
                _status_permintaan = (
                    f'"{_permintaan}"' if _permintaan else "(not captured)"
                )
                _status_aksi = state.last_tool_note or _L(
                    state,
                    "(no tool executed yet this turn)",
                    "(belum ada tool yang jalan turn ini)",
                )
                _todo_pending = state.sisa_todo(deps.todo_manager)
                if _todo_pending:
                    _baris_todo = [
                        f"[{t.get('id', '?')}] {str(t.get('text', ''))[:80]}"
                        for t in _todo_pending[:4]
                    ]
                    if len(_todo_pending) > 4:
                        _baris_todo.append(f"+{len(_todo_pending) - 4} more")
                    _status_todo = f"{len(_todo_pending)} pending — " + "; ".join(
                        _baris_todo
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

                deps.context_manager.add_message(
                    "user", f"[SYSTEM WARNING]: {warning_msg}"
                )

                yield sse.ev("token", text=warning_msg)
                yield sse.ev(
                    "ask_user",
                    question=_L(
                        state,
                        "Soft limit reached. Continue, or give new instructions?",
                        "Batas lunak tercapai. Lanjutkan, atau beri instruksi baru?",
                    ),
                )
                yield sse.ev("done")
                print(
                    "[ORION SOFT LIMIT] Reached 80% of iterations. Waiting for user instructions."
                )
                return

            # >>> STEP 2: REBUILD THE PAYLOAD FROM THE SOURCE OF TRUTH <<<
            current_history = deps.context_manager.get_history()
            api_messages = _rakit_api_messages(
                SYSTEM_PROMPT, deps.context_message, current_history
            )

            if _ == 0 and state.pending_image:
                for _i in range(len(api_messages) - 1, -1, -1):
                    if api_messages[_i].get("role") == "user":
                        api_messages[_i] = {
                            "role": "user",
                            "content": state.pending_image,
                        }
                        break
                state.pending_image = None
                if deps.lampiran_desc:
                    yield deps.kirim_status(f"📎 {deps.lampiran_desc}")

            if _ == 0 and deps.inbox_desc:
                yield deps.kirim_status(f"📥 {deps.inbox_desc}")

            # >>> STEP 3: SHORT REPLY ANTI-AMNESIA GUARD <<<
            if _ == 0:
                last_user_idx = -1
                for i in range(len(api_messages) - 1, -1, -1):
                    msg_content = api_messages[i].get("content", "")
                    if api_messages[i].get(
                        "role"
                    ) == "user" and "Current project structure" not in str(msg_content):
                        last_user_idx = i
                        break

                if last_user_idx >= 0:
                    last_user_msg = api_messages[last_user_idx]
                    user_content = last_user_msg.get("content") or ""
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

                    last_assistant_content = (
                        (last_assistant_msg.get("content") or "")
                        if last_assistant_msg
                        else ""
                    )

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
                                _patched = []
                                _done = False
                                for _p in user_content:
                                    if (
                                        not _done
                                        and isinstance(_p, dict)
                                        and _p.get("type") == "text"
                                    ):
                                        _patched.append(
                                            {
                                                **_p,
                                                "text": str(_p.get("text") or "")
                                                + reminder,
                                            }
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

            # >>> STEP 4: AGGRESSIVE CONTEXT PRUNING <<<
            compressed_messages = []
            total_msgs = len(api_messages)

            for i, msg in enumerate(api_messages):
                content_len = len(str(msg.get("content") or ""))

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

            # >>> TOKEN BUDGET GUARD (Emergency Check) <<<
            estimated_tokens = sum(estimate_message_tokens(m) for m in api_messages)

            if estimated_tokens > token_budget_threshold:
                print(
                    f"[TOKEN GUARD] High token estimate ({estimated_tokens} > {token_budget_threshold}). Performing PERMANENT emergency pruning..."
                )
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

                current_history = deps.context_manager.get_history()
                api_messages = _rakit_api_messages(
                    SYSTEM_PROMPT, deps.context_message, current_history
                )

                new_est = sum(estimate_message_tokens(m) for m in api_messages)
                print(f"[TOKEN GUARD] Successfully reduced to: {new_est} tokens")

            # >>> STEP 5: CLEAN CALL TO LLM_ROUTER (RESILIENT) <<<
            yield deps.kirim_status("⏳ Thinking...")

            response = None
            start_time = asyncio.get_event_loop().time()

            merged_messages = []
            for msg in api_messages:
                clean_msg = {"role": msg.get("role")}

                if "content" in msg and msg["content"] is not None:
                    clean_msg["content"] = msg["content"]
                elif clean_msg["role"] == "assistant":
                    clean_msg["content"] = ""

                if "tool_calls" in msg:
                    clean_msg["tool_calls"] = msg["tool_calls"]
                if "tool_call_id" in msg:
                    clean_msg["tool_call_id"] = msg["tool_call_id"]
                if "name" in msg:
                    clean_msg["name"] = msg["name"]

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

            print(f"[DEBUG] Number of messages to send: {len(clean_api_messages)}")
            print(f"[DEBUG] Number of tools to send: {len(tools_for_llm)}")

            try:
                _loop_waktu = asyncio.get_running_loop()
                _tugas_llm = asyncio.create_task(
                    deps.llm_call(messages=clean_api_messages, tools=tools_for_llm)
                )
                _mulai = _loop_waktu.time()
                try:
                    while not _tugas_llm.done():
                        _selesai, _tunggu_pending = await asyncio.wait(
                            {_tugas_llm}, timeout=15
                        )
                        if not _selesai:
                            _detik = int(_loop_waktu.time() - _mulai)
                            yield deps.kirim_status(
                                f"🤔 Still thinking... {_detik} seconds"
                            )
                    response = _tugas_llm.result()
                except BaseException:
                    if not _tugas_llm.done():
                        _tugas_llm.cancel()
                    raise

            except Exception as api_err:
                err_str = str(api_err)
                print(f"[ORION RECOVERY] Error detected: {err_str[:150]}")

                is_400_error = (
                    "400" in err_str
                    or "context length" in err_str.lower()
                    or "bad request" in err_str.lower()
                )

                if is_400_error:
                    consecutive_400_errors += 1

                    current_error_msg = err_str[:200]
                    if (
                        consecutive_400_errors >= 2
                        and current_error_msg == last_400_error_msg
                    ):
                        yield sse.ev(
                            "error",
                            text=f"⚠️ The current model is probably INCOMPATIBLE with the request format. The same error occurred twice: {err_str[:250]}. Change DEFAULT_MODEL in your .env file or check the provider documentation.",
                        )
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
                    consecutive_400_errors = 0
                    last_400_error_msg = ""

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

                if not is_400_error:
                    deps.context_manager.add_message(
                        "user",
                        "[SYSTEM RECOVERY]: Connection lost. Continue with an alternative model.",
                    )

                continue

            if response is None:
                continue

            if not getattr(response, "choices", None):
                print("[ORION RECOVERY] Model returned an empty response (no choices).")
                yield sse.ev(
                    "error",
                    text=(
                        "⚠️ The model returned an empty response (no choices). "
                        "Send your message again — if it repeats, switch the model/provider."
                    ),
                )
                break

            msg = response.choices[0].message

            end_time = asyncio.get_event_loop().time()
            duration_ms = (end_time - start_time) * 1000

            usage = getattr(response, "usage", None)
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0
            cached_tokens = ekstrak_cached_tokens(usage)
            actual_model_used = getattr(response, "model", "auto_rotated_model")

            proj_path = deps.tool_executor.project_path if deps.tool_executor else "."
            log_performance(
                proj_path,
                actual_model_used,
                input_tokens,
                output_tokens,
                duration_ms,
                "success",
                cached_tokens=cached_tokens,
            )

            if getattr(msg, "tool_calls", None):
                yield deps.kirim_status("Processing tool...")

                if msg.content:
                    cleaned = fix_over_escaped_text(strip_thinking_blocks(msg.content))
                    if cleaned and stream_utils.catatan_untuk_user(cleaned):
                        yield sse.ev("token", text=cleaned + chr(10) + chr(10))

                reasoning_content = getattr(msg, "reasoning_content", None)

                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [],
                }
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                tool_results = []
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

                    state.last_tool_note = _catatan_tool(func_name, func_args)

                    _pre_notice = tool_guards.pre_call_check(
                        func_name,
                        func_args,
                        mcp=deps.mcp is not None and deps.mcp.has_tool(func_name),
                    )
                    if _pre_notice:
                        if deps.logger is not None:
                            deps.logger.warning(_pre_notice)
                        if _pre_notice.startswith("[MCP HARD BLOCK]"):
                            tool_results.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "name": func_name,
                                    "content": _pre_notice,
                                }
                            )
                            continue

                    if func_name == "ask_user":
                        question_text = fix_over_escaped_text(
                            func_args.get("question", "")
                        )
                        yield sse.ev("ask_user", question=question_text)

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
                            "[AWAITING USER ANSWER]",
                            tool_call_id=tc_id,
                        )

                        for notice in state.pending_system_notices:
                            deps.context_manager.add_message("user", notice)

                        return

                    try:
                        if func_name == "update_todos":
                            if deps.todo_manager is None:
                                result = "Info: The todo-list feature is currently unavailable (module not installed); continue without update_todos."
                            else:
                                _status_sebelum = {
                                    str(t.get("id")): t.get("status")
                                    for t in deps.todo_manager.get_todos()
                                }
                                _tolak_kerja = [
                                    str(_t.get("id"))
                                    for _t in func_args.get("todos", [])
                                    if str(_t.get("status")) == "completed"
                                    and _status_sebelum.get(str(_t.get("id")))
                                    != "completed"
                                    and any(
                                        k in str(_t.get("text", "")).lower()
                                        for k in guard_rules.KATA_KERJA_MUTASI
                                    )
                                    and not state.files_changed_this_turn
                                ]
                                if _tolak_kerja:
                                    result = (
                                        "Error: todo(s) "
                                        + ", ".join(_tolak_kerja)
                                        + " marked 'completed' carry "
                                        "edit/work verbs but this turn's "
                                        "ledger holds ZERO mutated files. "
                                        "Perform the edit with "
                                        "edit_file/write_file first, keep "
                                        "the item pending/in_progress, or "
                                        "mark it 'cancelled' with a reason "
                                        "in its text. A completed edit-todo "
                                        "without a mutated file is a claim, "
                                        "not work."
                                    )
                                    tool_results.append(
                                        {
                                            "role": "tool",
                                            "tool_call_id": tc_id,
                                            "name": func_name,
                                            "content": result,
                                        }
                                    )
                                    yield deps.kirim_status(
                                        "🛑 Todo completion refused: edit "
                                        "verbs with an empty mutation ledger..."
                                    )
                                    continue
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
                                yield sse.ev(
                                    "todo_update",
                                    markdown=rendered,
                                    todos=deps.todo_manager.get_todos(),
                                )
                                result = f"Todo list updated:\n{rendered}"

                        elif deps.mcp is not None and deps.mcp.has_tool(func_name):
                            yield deps.kirim_status(
                                f"🔌 MCP {deps.mcp.status_label(func_name)} ..."
                            )
                            result = await deps.mcp.call(func_name, func_args)
                            result = await _fallback_ipv4_bila_ditolak(
                                state, deps, func_name, func_args, result
                            )
                            if (
                                "__browser_evaluate" in str(func_name)
                                and "getComputedStyle" in str(func_args)
                                and _browser_hasil_sukses(result)
                                and "about:blank" not in str(result)
                            ):
                                state.gaya_diperiksa_turn_ini = True
                                state.gaya_bukti_turn.append(
                                    str(result)[:180].replace("\n", " ")
                                )
                                state.tutup_debt_verifikasi("gaya")
                            if "__browser_" in str(func_name) and (
                                _browser_hasil_sukses(result)
                            ):
                                state.panggilan_browser_turn_ini += 1
                                if guard_rules.perintah_tool_boleh_tutup(state):
                                    state.tutup_debt_verifikasi("perintah_tool")
                                if guard_rules.perlu_nudge_ledger(state):
                                    state.nudge_ledger += 1
                                    result = (
                                        (
                                            "📐 [ORION LEDGER GAP] The user's "
                                            "order requires getComputedStyle "
                                            "evidence: '"
                                            + str(state.perintah_tool_eksplisit)[:160]
                                            + "'. As of this call NO "
                                            "browser_evaluate carrying "
                                            "getComputedStyle has landed in "
                                            "the ledger. Your NEXT browser "
                                            "action MUST be browser_evaluate "
                                            "whose arguments carry "
                                            "getComputedStyle(color)/"
                                            "backgroundColor/borderTopWidth/"
                                            "borderRadius as ordered — or "
                                            "close with an explicit decline."
                                        )
                                        + "\n"
                                        + str(result)
                                    )
                            if _pre_notice:
                                deps.logger.info("[ORION PRE-CALL] %s", _pre_notice)
                                result = _pre_notice + "\n" + str(result)

                        elif deps.tool_executor is None:
                            result = "Error: Tool executor not initialized."

                        # >>> HARD BLOCK FOR ZERO-MUTATION TURNS <<<
                        # Placed BEFORE tool_dispatch.tool_sederhana so delete_file
                        # and move_file cannot bypass the zero-mutation block.
                        elif (
                            func_name in _TOOL_MUTASI
                            and getattr(state, "zero_mutasi_turn", False)
                        ) or (
                            func_name == "run_terminal"
                            and getattr(state, "zero_mutasi_turn", False)
                            and guard_rules.perintah_build_terlarang(str(func_args))
                        ):
                            if func_name == "run_terminal":
                                result = (
                                    "🚫 [ORION BLOCK] run_terminal DITOLAK: "
                                    "turn verifikasi murni melarang "
                                    "memulai server / production build via "
                                    "terminal — '"
                                    + str(getattr(state, "kutipan_zero_mutasi", ""))
                                    + "'. Verifikasi lewat browser MCP "
                                    "saja, atau tolak terang-terangan."
                                )
                                yield deps.kirim_status(
                                    "🚫 Build/server diblok: run_terminal"
                                )
                            else:
                                result = (
                                    "🚫 [ORION BLOCK] Panggilan "
                                    + str(func_name)
                                    + " DITOLAK: instruksi turn ini "
                                    "melarang semua mutasi file — '"
                                    + str(getattr(state, "kutipan_zero_mutasi", ""))
                                    + "'. Tidak ada byte yang ditulis. "
                                    "Verifikasi baca-saja saja, atau "
                                    "tolak terang-terangan bila tugas "
                                    "mustahil tanpa mutasi."
                                )
                                yield deps.kirim_status(
                                    "🚫 Mutasi diblok: " + str(func_name)
                                )

                        elif tool_dispatch.tool_sederhana(func_name):
                            _lm, _ls = tool_dispatch.label(func_name, func_args)
                            yield deps.kirim_status(_lm)
                            result = tool_dispatch.jalankan(
                                func_name, deps.tool_executor, func_args
                            )
                            if (
                                func_name in ("delete_file", "move_file")
                                and isinstance(result, str)
                                and result.lower().startswith("success")
                            ):
                                _target_m = func_args.get(
                                    "file_path",
                                    func_args.get("to_path", "unknown"),
                                )
                                state.catat_file_berubah(
                                    f"- `{_target_m}` ({func_name})"
                                )
                            if _ls:
                                yield deps.kirim_status(_ls)

                        elif subagent_manager.is_subagent_tool(func_name):
                            _sa_role = subagent_manager.role_of_tool(func_name)
                            if func_name == subagent_manager.TOOL_WORKERS:
                                _sa_tugas = func_args.get("tasks") or []
                                _sa_snippet = f"{len(_sa_tugas)} parallel task(s)"
                            else:
                                _sa_snippet = str(func_args.get("task") or "")[:80]
                            yield deps.kirim_status(
                                f"🤖 Sub-agent {_sa_role}: {_sa_snippet} ..."
                            )
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
                                f"🤖 Sub-agent {_sa_role} finished."
                            )

                        elif func_name in (
                            "write_file",
                            "edit_file",
                        ) and subagent_manager.is_strict_worker_coding(deps.config):
                            _target_w = func_args.get("file_path", "unknown")
                            result = (
                                f"🚫 [ORION STRICT WORKER MODE] Direct `{func_name}` on '{_target_w}' "
                                "is BLOCKED. Even for a 1-line edit, delegate ALL code writes/edits "
                                "to `delegate_workers` with explicit `files` scope and full instructions."
                            )
                            yield deps.kirim_status(
                                f"🤖 Redirecting {func_name} to worker sub-agent..."
                            )

                        elif func_name == "read_file":
                            file_path = func_args.get("file_path", "")
                            if ".orion/attachments/" in str(file_path).replace(
                                "\\", "/"
                            ):
                                state.lampiran_dibaca_turn_ini.add(str(file_path))
                                _referensi = True
                            else:
                                _referensi = False
                            if file_path in state.analyzed_files and not _referensi:
                                result = f"[SYSTEM WARNING] REDUNDANCY DETECTED: You already read '{file_path}' in this session. The information is already in your memory. Do NOT re-read it. Continue the analysis or execution based on what you already know."
                            else:
                                yield deps.kirim_status(f" Reading file: {file_path}")
                                result = deps.tool_executor.read_file(**func_args)
                                state.analyzed_files.add(file_path)
                                yield deps.kirim_status(f"✅ File {file_path} read")

                        elif func_name == "read_file_range":
                            file_path = func_args.get("file_path", "")
                            start = func_args.get("start_line", 1)
                            end = func_args.get("end_line", 50)

                            range_key = f"{file_path}:{start}-{end}"

                            _referensi = ".orion/attachments/" in str(
                                file_path
                            ).replace("\\", "/")
                            if _referensi:
                                state.lampiran_dibaca_turn_ini.add(str(file_path))

                            if range_key in state.analyzed_files and not _referensi:
                                result = f"[SYSTEM WARNING] REDUNDANCY DETECTED: You already read lines {start}-{end} of '{file_path}' in this session. Use the information you already have."
                            else:
                                yield deps.kirim_status(
                                    f"📖 Reading {file_path} (lines {start}-{end})"
                                )
                                result = deps.tool_executor.read_file_range(**func_args)
                                state.analyzed_files.add(range_key)
                                yield deps.kirim_status(f"✅ File {file_path} read")

                        elif func_name == "write_file":
                            file_path = func_args.get("file_path", "unknown")

                            path_ok, path_reason = is_path_safe(
                                deps.tool_executor.project_path, file_path
                            )
                            if not path_ok:
                                result = f"🚫 [REJECTED BY SYSTEM - UNSAFE PATH]: {path_reason}"
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - UNSAFE PATH"
                                )
                                yield deps.kirim_status(
                                    "🚫 File operation denied (outside the project folder)"
                                )
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
                                yield deps.kirim_status("🚫 Denied: sensitive file")
                            else:
                                yield deps.kirim_status(
                                    f"📝 Creating/overwriting file: {file_path}"
                                )
                                result = deps.tool_executor.write_file(**func_args)
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    print(
                                        f"[ORION WRITE_FILE] Wrote successfully: {file_path}"
                                    )
                                    state.catat_file_berubah(
                                        f"- `{file_path}` (write_file)"
                                    )
                                    state.catat_edit_sukses(file_path)
                                    try:
                                        full_path = os.path.join(
                                            deps.tool_executor.project_path, file_path
                                        )
                                        verification_msg = verify_file_write(
                                            full_path,
                                            expected_content=func_args.get("content"),
                                        )
                                        result = f"{result}\n{verification_msg}"
                                    except Exception as verify_err:
                                        print(
                                            f"[POST_EDIT_VERIFIER WARNING] {verify_err}"
                                        )
                                else:
                                    state.catat_edit_gagal(
                                        file_path,
                                        (
                                            str(result).splitlines()[0][:200]
                                            if result
                                            else "tool returned no result"
                                        ),
                                    )
                                    print(
                                        f"[ORION WRITE_FILE] FAILED for {file_path}: "
                                        f"{str(result).splitlines()[0][:200] if result else 'no result'}"
                                    )
                                state.analyzed_files.discard(file_path)
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    yield deps.kirim_status(
                                        f"✅ File {file_path} created"
                                    )
                                else:
                                    yield deps.kirim_status(
                                        f"❌ File {file_path} NOT created (write failed)"
                                    )

                        elif func_name == "edit_file":
                            file_path = func_args.get("file_path", "unknown")

                            path_ok, path_reason = is_path_safe(
                                deps.tool_executor.project_path, file_path
                            )
                            if not path_ok:
                                result = f"🚫 [REJECTED BY SYSTEM - UNSAFE PATH]: {path_reason}"
                                state.catat_edit_gagal(
                                    file_path, "REJECTED BY SYSTEM - UNSAFE PATH"
                                )
                                yield deps.kirim_status(
                                    "🚫 File operation denied (outside the project folder)"
                                )
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
                                yield deps.kirim_status("🚫 Denied: sensitive file")
                            else:
                                yield deps.kirim_status(f"🔧 Editing file: {file_path}")
                                result = deps.tool_executor.edit_file(**func_args)
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    print(
                                        f"[ORION EDIT_FILE] Edited successfully: {file_path}"
                                    )
                                    state.catat_file_berubah(
                                        f"- `{file_path}` (edit_file)"
                                    )
                                    state.catat_edit_sukses(file_path)
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
                                    state.catat_edit_gagal(
                                        file_path,
                                        (
                                            str(result).splitlines()[0][:200]
                                            if result
                                            else "tool returned no result"
                                        ),
                                    )
                                    print(
                                        f"[ORION EDIT_FILE] FAILED for {file_path}: "
                                        f"{str(result).splitlines()[0][:200] if result else 'no result'}"
                                    )
                                state.analyzed_files.discard(file_path)
                                if isinstance(
                                    result, str
                                ) and result.lower().startswith("success"):
                                    yield deps.kirim_status(
                                        f"✅ File {file_path} edited"
                                    )
                                else:
                                    yield deps.kirim_status(
                                        f"❌ File {file_path} NOT edited (edit failed)"
                                    )

                        elif func_name == "run_terminal":
                            cmd_to_run = func_args.get("command", "")

                            try:
                                yield deps.kirim_status(f"⚙️ Running: {cmd_to_run}")

                                if is_dangerous_command(cmd_to_run):
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
                                    for notice in state.pending_system_notices:
                                        deps.context_manager.add_message("user", notice)
                                    yield sse.ev("confirm_terminal", command=cmd_to_run)
                                    result = "[AWAITING USER CONFIRMATION]"
                                    return

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
                                    yield deps.kirim_status(
                                        "🚫 Server command denied (prevents a hanging process)"
                                    )

                                else:
                                    loop = asyncio.get_event_loop()
                                    future = loop.run_in_executor(
                                        None,
                                        lambda _cmd=cmd_to_run: deps.tool_executor.run_terminal(
                                            command=_cmd, timeout=60
                                        ),
                                    )

                                    while not future.done():
                                        yield deps.kirim_status(
                                            "⏳ Process still running, please wait... (heartbeat)"
                                        )
                                        await asyncio.sleep(10)

                                    result = future.result()
                                    if (
                                        isinstance(result, str)
                                        and not result.startswith(
                                            ("Error", "❌", "[REJECTED")
                                        )
                                        and not is_terminal_failure(result)
                                    ):
                                        state.terminal_ok += 1
                                        state.tutup_debt_verifikasi("stall")
                                    yield deps.kirim_status(
                                        "✅ Terminal command finished"
                                    )

                            except (RuntimeError, asyncio.CancelledError):
                                print(
                                    "[ORION] Frontend connection lost during tool execution. Stopping process safely."
                                )
                                return

                            except Exception as e:
                                result = f"Terminal execution error: {e!s}"
                                print(f"[ORION TOOL ERROR] {e}")
                                traceback.print_exc()

                        elif func_name == "git_diff":
                            yield deps.kirim_status("🔀 Checking git diff...")
                            result = deps.tool_executor.git_diff(**func_args)

                        else:
                            _nama_tersedia = set(tool_dispatch.NAMA_TOOL_AKTIF)
                            if deps.mcp is not None:
                                _nama_tersedia |= deps.mcp.tool_names()
                            result = (
                                f"Error: tool '{func_name}' is NOT AVAILABLE. "
                                f"Tools that actually work: {sorted(_nama_tersedia)}. "
                                "Use one of those, or accomplish the goal another way. "
                                "Do NOT call the same tool again."
                            )
                            deps.logger.error(
                                "[DEAD TOOL] Model called %r which has no dispatch branch.",
                                func_name,
                            )

                    except (RuntimeError, asyncio.CancelledError):
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
                        res_str = (
                            f"Tool '{func_name}' returned EMPTY output and no "
                            "error. Empty is not proof of success: verify the "
                            "effect on disk or state (read the target back, "
                            "git_status, re-run with corrected quoting) before "
                            "claiming any result."
                        )

                    if len(res_str) > 15000:
                        res_str = res_str[:15000] + (
                            "\n\n[⚠️ SYSTEM WARNING: Output was too long and has been truncated.]"
                            "\n[Do NOT use 'read_file' again for this file.]"
                            "\n[CORRECT APPROACH: Use 'search_code' to find keywords/lines, "
                            "then use 'read_file_range' with specific start_line and end_line.]"
                            "\n[An edit_file SEARCH block built from this truncated view WILL "
                            "miss beyond the cut - re-read the exact region first.]"
                        )

                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": func_name,
                            "content": res_str,
                        }
                    )

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

                    if len(last_tool_calls) > 3:
                        last_tool_calls.pop(0)

                    if len(last_tool_calls) == 3:
                        all_failed = all(
                            not item["success"] for item in last_tool_calls
                        )
                        all_same_action = (
                            len(set(item["action"] for item in last_tool_calls)) == 1
                        )

                        if all_failed and all_same_action:
                            print(
                                f"[ORION ANTI-LOOP] Detected {func_name} failed 3x in a row. Forcing stop."
                            )
                            state.tambah_notice(
                                f"[SYSTEM EMERGENCY STOP]: The command '{func_name}' has failed 3 times in a row. STOP trying variations of the same thing. Read the error message in the previous output carefully, report the core problem to the user, and ask for new instructions. Do NOT try running this command again."
                            )
                            try:
                                _kueri_riset = research_rules.rancang_kueri_riset(
                                    func_name, res_str
                                )
                                _temuan_riset = deps.tool_executor.web_search(
                                    _kueri_riset, max_results=3
                                )
                                deps.logger.info(
                                    "[AUTO RESEARCH] query=%s", _kueri_riset
                                )
                            except Exception as _err_riset:  # noqa: BLE001
                                _temuan_riset = (
                                    f"(automatic research failed: {_err_riset})"
                                )
                            state.tambah_notice(
                                "[SYSTEM - AUTOMATIC RESEARCH]: Before giving up and reporting to the "
                                "user, read the following web findings, then try ONE approach "
                                "that is DIFFERENT from what already failed, based on these findings:\n"
                                + str(_temuan_riset)[:2500]
                                + "\n\nOnly if these findings are inadequate, THEN report to the user: "
                                "what was tried, what was found, and what you recommend."
                            )
                            last_tool_calls = []

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

                for notice in state.pending_system_notices:
                    deps.context_manager.add_message("user", notice)

                has_terminal_error = False
                error_snippet = ""
                for tr in tool_results:
                    if tr["name"] == "run_terminal" and is_terminal_failure(
                        tr["content"]
                    ):
                        has_terminal_error = True
                        error_snippet = "\n".join(tr["content"].split("\n")[:3]).strip()
                        break

                if has_terminal_error:
                    _fix_action = (
                        "delegate to `delegate_workers` with exact `files` scope to fix it RIGHT NOW"
                        if subagent_manager.is_strict_worker_coding(deps.config)
                        else "use edit_file to fix it RIGHT NOW"
                    )
                    deps.context_manager.add_message(
                        "user",
                        f"[SYSTEM AUTO-CORRECTION MODE]: A terminal command failed.\nError Snippet: {error_snippet}\n\nTASK: do NOT ask the user. Analyze this error. Do NOT use full read_file. Use read_file_range if you need to see context, then {_fix_action}. Perform at most 2 automatic fix iterations.",
                    )
                    print(
                        "[ORION SELF-CORRECT] Error detected. Forcing auto-fix mode (token-efficient)."
                    )

            else:
                content = fix_over_escaped_text(msg.content or "")
                clean_content = strip_thinking_blocks(content)
                reasoning_content = getattr(msg, "reasoning_content", None)

                keputusan = loop_guards.evaluasi_akhir_iterasi(
                    state,
                    clean_content,
                    iterasi=_,
                    max_iter=max_iter,
                    sisa_todo=state.sisa_todo(deps.todo_manager),
                    maks_nudge=MAX_TODO_NUDGE,
                    maks_verifikasi=MAX_VERIFIKASI,
                )
                if (
                    keputusan == loop_guards.Keputusan.JAWAB_NORMAL
                    and stream_utils.prose_perencanaan(clean_content)
                    and _ + 1 < max_iter
                ):
                    keputusan = loop_guards.Keputusan.TERUSKAN

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                ) and _perlu_nudge_referensi(state, deps.tool_executor):
                    state.nudge_referensi += 1
                    _refs = _referensi_belum_dibaca(
                        state, getattr(deps.tool_executor, "project_path", ".")
                    )
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - REFERENCE RECONCILIATION]: you are closing "
                            "a turn that changed UI files while the user's saved "
                            "reference attachments were never read: "
                            + ", ".join(f"`.orion/attachments/{r}`" for r in _refs)
                            + ". "
                            + _kalimat_referensi_ada(state)
                            + " Before this turn may end, EITHER read them with "
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
                            + ", ".join(f"`.orion/attachments/{r}`" for r in _refs)
                            + ". "
                            + _kalimat_referensi_ada(state)
                            + " Sebelum turn ini boleh berakhir, BACA dulu "
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
                            "📖 UI changed with saved references unread - "
                            "reconciliation owed before this turn can close...",
                            "📖 UI berubah sementara referensi tersimpan belum "
                            "dibaca - rekonsiliasi wajib sebelum turn ini "
                            "boleh ditutup...",
                        )
                    )
                    continue

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                ) and _perlu_nudge_gaya(state, deps):
                    state.nudge_gaya += 1
                    state.catat_debt_verifikasi("gaya", state.permintaan_turn_ini)
                    _proj = getattr(deps.tool_executor, "project_path", ".")
                    _daftar_ref = ", ".join(
                        f"`.orion/attachments/{r}`"
                        for r in _ui_berubah_dan_referensi(state, _proj)
                    )
                    _token = _token_desain_dari_referensi(_proj)
                    if _token:
                        _hexes = ", ".join(_token)
                        _baris_token = _L(
                            state,
                            f" The references carry these hex tokens: {_hexes}.",
                            f" Referensi memuat token hex ini: {_hexes}.",
                        )
                    else:
                        _baris_token = ""
                    _alat = sorted(_tool_browser_mcp(deps))
                    _navigate = next(
                        (a for a in _alat if a.endswith("__browser_navigate")),
                        _alat[0],
                    )
                    _evaluate = next(
                        (a for a in _alat if a.endswith("__browser_evaluate")),
                        _alat[0],
                    )
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - COMPUTED-STYLE VERIFICATION]: you are "
                            "closing a turn that changed UI files while "
                            "browser tools are connected, and the RENDERED "
                            "styles were never inspected this turn. The "
                            "user's saved references: "
                            + _daftar_ref
                            + "."
                            + _baris_token
                            + " Before this turn may end, EITHER open the "
                            "rendered result with `" + _navigate + "` and "
                            "extract computed styles with `" + _evaluate + "` "
                            "(getComputedStyle → color, backgroundColor, "
                            "fontFamily, fontSize, borderRadius) on the "
                            "elements you changed and compare them token by "
                            "token. If the rendered app is not reachable, "
                            "start its dev server with run_terminal and "
                            "navigate to the printed URL; operate the "
                            "controls you changed (browser_click — a toggle "
                            "that never flips is a bug no build can see). A "
                            "failed navigation is not a licence to close "
                            "silently. OR state PLAINLY in your closing "
                            "report which visual properties remain "
                            "UNVERIFIED against the rendered result. Claiming visual "
                            "compliance from reading source code alone — or "
                            "from a passing build — is self-certification, "
                            "not verification.",
                            "[SISTEM - VERIFIKASI GAYA TERKOMPUTASI]: Anda "
                            "hendak menutup turn yang mengubah file UI "
                            "sementara tool browser terhubung, dan gaya "
                            "RENDER tidak pernah diperiksa turn ini. "
                            "Referensi tersimpan user: "
                            + _daftar_ref
                            + "."
                            + _baris_token
                            + " Sebelum turn ini boleh berakhir, BUKA hasil "
                            "render dengan `" + _navigate + "` lalu ekstrak "
                            "gaya terkomputasi dengan `" + _evaluate + "` "
                            "(getComputedStyle → color, backgroundColor, "
                            "fontFamily, fontSize, borderRadius) pada elemen "
                            "yang Anda ubah dan bandingkan token per token. "
                            "Bila app render tidak terjangkau, nyalakan dev "
                            "server-nya dengan run_terminal lalu navigate ke "
                            "URL yang dicetak; operasikan kontrol yang Anda "
                            "ubah (browser_click — toggle yang tidak pernah "
                            "berubah adalah bug yang tak terlihat oleh build "
                            "apa pun). Navigasi yang gagal bukan izin untuk "
                            "menutup diam-diam. ATAU nyatakan "
                            "TERANG-TERANGAN di laporan "
                            "penutup properti visual mana yang masih BELUM "
                            "TERVERIFIKASI terhadap hasil render. Mengklaim "
                            "kepatuhan visual hanya dari membaca kode "
                            "sumber — atau dari build yang lulus — adalah "
                            "sertifikasi diri, bukan verifikasi.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "🎨 UI changed with browser tools connected - "
                            "computed-style verification owed before this "
                            "turn can close...",
                            "🎨 UI berubah sementara tool browser terhubung - "
                            "verifikasi gaya terkomputasi wajib sebelum turn "
                            "ini boleh ditutup...",
                        )
                    )
                    continue

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                ) and _perlu_tantang_klaim_visual(state, deps, clean_content):
                    state.tantang_klaim_visual += 1
                    _kutip = guard_rules.kalimat_klaim_visual(clean_content)
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - VISUAL CLAIM CHALLENGE]: your closing "
                            'claims verification — "' + _kutip + '" — but '
                            "this turn's ledger holds no getComputedStyle "
                            "evidence while browser tools are connected. "
                            "Reading a file or passing a build is not visual "
                            "verification. BEFORE this turn may end, EITHER "
                            "run the computed-style check now (start the dev "
                            "server with run_terminal if the app is not "
                            "reachable, then browser_navigate + "
                            "browser_evaluate with getComputedStyle), OR "
                            "rewrite your closing to state PLAINLY which "
                            "visual properties remain UNVERIFIED. This "
                            "challenge is generated from the turn's own "
                            "ledger — not a simulation.",
                            "[SISTEM - TANTANGAN KLAIM VISUAL]: penutup Anda "
                            'mengklaim verifikasi — "' + _kutip + '" — '
                            "tetapi ledger turn ini tidak memuat bukti "
                            "getComputedStyle sementara tool browser "
                            "terhubung. Membaca file atau build yang lulus "
                            "bukan verifikasi visual. SEBELUM turn ini boleh "
                            "berakhir, JALANKAN pemeriksaan computed-style "
                            "sekarang (nyalakan dev server dengan run_terminal "
                            "bila app tidak terjangkau, lalu browser_navigate "
                            "+ browser_evaluate dengan getComputedStyle), "
                            "ATAU tulis ulang penutup Anda tanpa klaim itu: "
                            "nyatakan TERANG-TERANGAN properti visual mana "
                            "yang masih BELUM TERVERIFIKASI. Tantangan ini "
                            "dihasilkan dari ledger turn ini sendiri — "
                            "bukan simulasi.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "🎯 Visual verification claimed without "
                            "rendered-style evidence - the claim must be "
                            "earned or withdrawn...",
                            "🎯 Klaim verifikasi visual tanpa bukti gaya "
                            "render - klaim harus dibuktikan atau "
                            "ditarik...",
                        )
                    )
                    continue

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                    loop_guards.Keputusan.TANTANG_KLAIM,
                ) and _perlu_tantang_klaim_implementasi(state, clean_content):
                    state.tantang_klaim_implementasi += 1
                    state.catat_debt_verifikasi(
                        "implementasi", state.permintaan_turn_ini
                    )
                    _kutip = guard_rules.kalimat_klaim_implementasi(clean_content)
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - IMPLEMENTATION CLAIM CHALLENGE]: your closing "
                            'claims implementation - "' + _kutip + '" - but this '
                            "turn's ledger holds ZERO successful write/edit operations "
                            "on project files. A claim of work without a single mutated "
                            "file is not work. BEFORE this turn may end, EITHER perform "
                            "the edits now with write_file/edit_file, OR rewrite your "
                            "closing to state PLAINLY that nothing was changed this "
                            "turn. This challenge is generated from the turn's own "
                            "ledger - not a simulation.",
                            "[SISTEM - TANTANGAN KLAIM IMPLEMENTASI]: penutup Anda "
                            'mengklaim implementasi - "' + _kutip + '" - tetapi '
                            "ledger turn ini memuat NOL operasi tulis/edit yang sukses "
                            "pada file proyek. Klaim kerja tanpa satu pun file berubah "
                            "bukan kerja. SEBELUM turn ini boleh berakhir, LAKUKAN "
                            "suntingannya sekarang dengan write_file/edit_file, ATAU "
                            "tulis ulang penutup Anda yang menyatakan TERANG-TERANGAN "
                            "bahwa tidak ada yang diubah turn ini. Tantangan ini "
                            "dihasilkan dari ledger turn ini sendiri - bukan simulasi.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "✍️ Implementation claimed with zero mutated files - "
                            "edit now or withdraw the claim...",
                            "✍️ Klaim implementasi tanpa satu pun file berubah - "
                            "sunting sekarang atau tarik klaim...",
                        )
                    )
                    continue

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                    loop_guards.Keputusan.TANTANG_KLAIM,
                ) and _perlu_tantang_perintah_tool(state):
                    state.tantang_perintah_tool += 1
                    state.catat_debt_verifikasi(
                        "perintah_tool", state.perintah_tool_eksplisit
                    )
                    _kutip = state.perintah_tool_eksplisit
                    _shot2 = (
                        _L(
                            state,
                            " This is the SECOND and final chance this turn; "
                            "close again without the ordered call and this "
                            "debt rides the next turn's TASK ANCHOR.",
                            " Ini kesempatan KEDUA dan terakhir turn ini; "
                            "menutup lagi tanpa panggilan yang diperintahkan "
                            "mengirim utang ini ke TASK ANCHOR turn berikut.",
                        )
                        if state.tantang_perintah_tool == 2
                        else ""
                    )
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - EXPLICIT TOOL ORDER]: the user's instruction "
                            'this turn ordered a tool by name - "' + _kutip + '" - '
                            "but the ledger holds ZERO successful calls to browser/MCP "
                            "tools. Silently ignoring a named tool is not allowed. "
                            "BEFORE this turn may end, EITHER run the ordered tool now, "
                            "OR state PLAINLY in your closing why it was not used and "
                            "what you did instead. This challenge is generated from the "
                            "turn's own ledger - not a simulation.",
                            "[SISTEM - PERINTAH TOOL EKSPLISIT]: instruksi user turn "
                            'ini menyebut tool berdasarkan namanya - "' + _kutip + '" '
                            "- tetapi ledger memuat NOL panggilan sukses ke tool "
                            "browser/MCP. Mengabaikan tool yang disebut namanya secara "
                            "diam-diam tidak diizinkan. SEBELUM turn ini boleh berakhir, "
                            "JALANKAN tool yang diperintahkan sekarang, ATAU nyatakan "
                            "TERANG-TERANGAN di penutup Anda mengapa tool itu tidak "
                            "dipakai dan apa yang Anda lakukan sebagai gantinya. "
                            "Tantangan ini dihasilkan dari ledger turn ini sendiri - "
                            "bukan simulasi.",
                        )
                        + _shot2,
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "🔧 Explicit tool order unexecuted - run it or explain "
                            "plainly...",
                            "🔧 Perintah tool eksplisit belum dijalankan - jalankan "
                            "atau jelaskan terang-terangan...",
                        )
                    )
                    continue

                if keputusan in (
                    loop_guards.Keputusan.CHECKPOINT,
                    loop_guards.Keputusan.JAWAB_NORMAL,
                    loop_guards.Keputusan.TANTANG_KLAIM,
                ) and _perlu_tantang_nihil_mutasi(state, clean_content):
                    state.tantang_nihil_mutasi += 1
                    state.catat_debt_verifikasi(
                        "nihil_mutasi", state.permintaan_turn_ini
                    )
                    _kutip = guard_rules.kalimat_perintah_kerja(
                        state.permintaan_turn_ini
                    )
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - ZERO-MUTATION CHALLENGE]: this turn's "
                            'instruction ordered mutating work - "'
                            + _kutip
                            + '" - but the ledger holds ZERO '
                            "mutated files, and your closing does not plainly "
                            "decline either. A verification ritual (builds, "
                            "syntax checks, reads) is not the ordered work. "
                            "BEFORE this turn may end, EITHER perform the "
                            "edits now with write_file/edit_file, OR state "
                            "PLAINLY that nothing was changed and why. This "
                            "challenge is generated from the turn's own "
                            "ledger and instruction - not a simulation.",
                            "[SISTEM - TANTANGAN NIHIL-MUTASI]: instruksi turn "
                            'ini memerintahkan kerja bersifat mutasi - "'
                            + _kutip
                            + '" - tetapi ledger memuat NOL file '
                            "berubah, dan penutup Anda juga tidak menyatakan "
                            "penolakan terang-terangan. Ritual verifikasi "
                            "(build, cek sintaks, pembacaan) bukan kerja yang "
                            "diperintahkan. SEBELUM turn ini boleh berakhir, "
                            "LAKUKAN suntingannya sekarang dengan "
                            "write_file/edit_file, ATAU nyatakan "
                            "TERANG-TERANGAN bahwa tidak ada yang diubah dan "
                            "mengapa. Tantangan ini dihasilkan dari ledger dan "
                            "instruksi turn ini sendiri - bukan simulasi.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "🛑 Work ordered but zero files mutated and no "
                            "plain decline - edit now or decline plainly...",
                            "🛑 Kerja diperintahkan tetapi nol file berubah "
                            "dan tiada penolakan terang - sunting sekarang "
                            "atau tolak terang-terangan...",
                        )
                    )
                    continue

                if keputusan == loop_guards.Keputusan.TANTANG_STAGNASI:
                    state.tantang_stagnasi += 1
                    state.catat_debt_verifikasi("stagnasi", state.permintaan_turn_ini)
                    _kutip_kerja = guard_rules.kalimat_perintah_kerja(
                        state.permintaan_turn_ini
                    )
                    deps.context_manager.add_message(
                        "assistant", clean_content, reasoning_content=reasoning_content
                    )
                    deps.context_manager.add_message(
                        "user",
                        _L(
                            state,
                            "[SYSTEM - STAGNATION CHALLENGE]: this turn shows "
                            f"{state.nir_mutasi_beruntun} consecutive iterations "
                            f"without ONE successful mutation and "
                            f"{state.edit_gagal_beruntun} consecutive failed "
                            "writes, while the instruction orders mutating work "
                            '- "' + _kutip_kerja + '". Repeating the same '
                            "read-edit cycle is not progress. BEFORE this turn "
                            "may end, EITHER change strategy now (re-read the "
                            "WHOLE region, use a smaller exact search block, or "
                            "write_file the file fresh) and land the mutation, "
                            "OR state PLAINLY which blocker stops it. This "
                            "challenge is generated from the turn's own ledger "
                            "- not a simulation.",
                            "[SISTEM - TANTANGAN STAGNASI]: turn ini menunjukkan "
                            f"{state.nir_mutasi_beruntun} iterasi beruntun tanpa "
                            "SATU pun mutasi sukses dan "
                            f"{state.edit_gagal_beruntun} tulis gagal beruntun, "
                            'padahal instruksi memerintahkan kerja bermutasi - "'
                            + _kutip_kerja
                            + '". Mengulang siklus baca-edit yang '
                            "sama bukan kemajuan. SEBELUM turn ini boleh "
                            "berakhir, GANTI strategi sekarang (baca ULANG "
                            "seluruh regio, pakai blok search kecil yang persis, "
                            "atau write_file ulang filenya) dan daratkan "
                            "mutasinya, ATAU nyatakan TERANG-TERANGAN blocker "
                            "apa yang menghentikannya. Tantangan ini dihasilkan "
                            "dari ledger turn ini sendiri - bukan simulasi.",
                        ),
                    )
                    yield deps.kirim_status(
                        _L(
                            state,
                            "🐌 Stagnation detected - change strategy or name "
                            "the blocker plainly...",
                            "🐌 Stagnasi terdeteksi - ganti strategi atau sebut "
                            "blocker terang-terangan...",
                        )
                    )
                    continue

                match keputusan:
                    case loop_guards.Keputusan.TERUSKAN:
                        print(
                            "[ORION] Orion produced short text without a tool. Continuing iteration..."
                        )
                        deps.context_manager.add_message(
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
                        )

                        state.current_iter = _ + 1
                        continue
                    case loop_guards.Keputusan.PAUSE_ITERASI:
                        print(
                            "[ORION] Orion produced short text without a tool. Continuing iteration..."
                        )
                        deps.context_manager.add_message(
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
                        )

                        state.current_iter = _ + 1
                        pause_msg = f"\n\n[SYSTEM PAUSE]: I have reached the iteration limit ({max_iter}) for this session for token safety. However, I still remember every step I have taken. Please type **'lanjutkan'** or **'continue'** so I can finish this task."
                        yield sse.ev("token", text=pause_msg)
                        yield sse.ev("done")
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

                        deps.context_manager.add_message(
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
                        )
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
                                f"🧭 Re-anchoring to your request (older todo [{_next_id}] deferred)..."
                            )
                            deps.logger.info(
                                "[ORION RE-ANCHOR %d/%d] next todo [%s] predates this turn; "
                                "re-anchoring to the current request instead of forcing it.",
                                state.nudge_count,
                                MAX_TODO_NUDGE,
                                _next_id,
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
                            yield deps.kirim_status(
                                f"Continuing with todo [{_next_id}]..."
                            )
                            deps.logger.info(
                                "[ORION NUDGE %d/%d] %d todos still pending. Continuing with [%s].",
                                state.nudge_count,
                                MAX_TODO_NUDGE,
                                _sisa,
                                _next_id,
                            )
                        yield sse.ev(
                            "todo_update",
                            markdown=deps.todo_manager.render_markdown(),
                            todos=deps.todo_manager.get_todos(),
                        )
                        continue
                    case loop_guards.Keputusan.TANTANG_SARAN:
                        state.saran_count += 1
                        deps.logger.warning(
                            "[OPEN FAILURE THREAD] closing answer without "
                            "root-cause/options wording; challenge %d/1: %s",
                            state.saran_count,
                            state.utas_gagal[:120],
                        )
                        deps.context_manager.add_message(
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
                        )
                        deps.context_manager.add_message(
                            "user",
                            _L(
                                state,
                                "[SYSTEM - PROACTIVE ADVISORY]: the user has an "
                                "OPEN FAILURE THREAD from an earlier message: "
                                f'"{state.utas_gagal[:200]}". Before closing this '
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
                                f'"{state.utas_gagal[:200]}". Sebelum menutup '
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
                            "💡 Open failure thread: root cause + options owed..."
                        )
                        continue
                    case loop_guards.Keputusan.CHECKPOINT:
                        _ada_utang_saat_laporan = bool(state.utang_verifikasi_terbuka())
                        _hdr_en31, _hdr_id31 = guard_rules.teks_status_checkpoint(
                            _ada_utang_saat_laporan
                        )
                        checkpoint_report = _L(state, _hdr_en31, _hdr_id31)

                        if state.files_changed_this_turn:
                            checkpoint_report += "\n".join(
                                state.files_changed_this_turn[-10:]
                            )
                        else:
                            checkpoint_report += _L(
                                state,
                                "- No files changed",
                                "- Tidak ada file yang berubah",
                            )

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
                        checkpoint_report += state.ringkasan_ledger()
                        checkpoint_report += _blok_todo_nol_bukti(state)
                        checkpoint_report += _blok_debt_verifikasi(state)
                        checkpoint_report += _blok_delegasi_akhir(state, klaim=True)
                        checkpoint_report += _blok_referensi_belum_dibaca(
                            state,
                            getattr(deps.tool_executor, "project_path", "."),
                        )
                        checkpoint_report += _blok_gaya_tanpa_verifikasi(state, deps)
                        checkpoint_report += _blok_edit_gagal_akhir(state)
                        checkpoint_report += _blok_persistensi_bila_perlu(
                            state, deps.tool_executor
                        )
                        _tutup_utas_bila_disarankan(state, clean_content)

                        checkpoint_report += _L(
                            state,
                            "\n\n**Next steps:**\n"
                            "Please review the changes above. Is there anything to adjust, or shall I continue to the next task?\n\n"
                            "---\n",
                            "\n\n**Langkah berikutnya:**\n"
                            "Silakan tinjau perubahan di atas. Ada yang perlu disesuaikan, atau saya lanjut ke tugas berikut?\n\n"
                            "---\n",
                        )

                        _teks_tutup = stream_utils.tambah_ledger_gaya(
                            stream_utils.bersih_closing(
                                strip_thinking_blocks(content),
                                len(state.files_changed_this_turn),
                            ),
                            state.gaya_bukti_turn,
                        )
                        stream_utils.rekam_respon_mentah(content, _teks_tutup)
                        yield sse.ev(
                            "token",
                            text=_teks_tutup + checkpoint_report,
                        )

                        deps.context_manager.add_message(
                            "assistant",
                            _teks_tutup + checkpoint_report,
                            reasoning_content=reasoning_content,
                        )

                        _belum_tuntas = bool(
                            pending_items
                            or _ada_utang_saat_laporan
                            or state.utang_verifikasi_terbuka()
                        )
                        yield sse.ev(
                            "ask_user",
                            question=(
                                _L(
                                    state,
                                    "Task not yet complete (pending items or open verification debt remain). Shall I continue resolving them?",
                                    "Tugas belum sepenuhnya selesai (masih ada item tertunda atau utang verifikasi terbuka). Apakah saya lanjut menyelesaikannya?",
                                )
                                if _belum_tuntas
                                else _L(
                                    state,
                                    "Task complete. Is there anything to adjust, or shall I continue to the next task?",
                                    "Tugas selesai. Ada yang perlu disesuaikan, atau saya lanjut ke tugas berikutnya?",
                                )
                            ),
                        )
                        yield sse.ev("done")
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
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
                        )
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
                        yield deps.kirim_status(
                            _L(
                                state,
                                "🔍 Verifying completion claim (0 files changed)...",
                                "🔍 Memverifikasi klaim selesai (0 file berubah)...",
                            )
                        )
                        continue
                    case loop_guards.Keputusan.TANTANG_EDIT_GAGAL:
                        state.verifikasi_count += 1
                        _edit_gagal = state.edit_gagal_belum_pulih()
                        _daftar_gagal = "\n".join(
                            f"- `{g['path']}` — {g['alasan']}" for g in _edit_gagal
                        )
                        deps.logger.warning(
                            "[CLAIM WITH FAILED EDITS] Orion claims completion but %d "
                            "file edit(s) FAILED and never succeeded afterwards "
                            "(verification %d/%d). Challenging the claim: %s",
                            len(_edit_gagal),
                            state.verifikasi_count,
                            MAX_VERIFIKASI,
                            ", ".join(g["path"] for g in _edit_gagal),
                        )
                        deps.context_manager.add_message(
                            "assistant",
                            clean_content,
                            reasoning_content=reasoning_content,
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
                                'Permintaan asli user turn ini: "'
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
                        _nama_gagal = ", ".join(
                            f"`{g['path']}`" for g in _edit_gagal[:5]
                        )
                        if len(_edit_gagal) > 5:
                            _nama_gagal += f" +{len(_edit_gagal) - 5} more"
                        yield deps.kirim_status(
                            _L(
                                state,
                                f"🔍 Verifying completion claim (failed edits: {_nama_gagal})...",
                                f"🔍 Memverifikasi klaim selesai (edit gagal: {_nama_gagal})...",
                            )
                        )
                        continue
                    case loop_guards.Keputusan.JAWAB_NORMAL:
                        _teks_jawab = stream_utils.tambah_ledger_gaya(
                            stream_utils.bersih_closing(
                                strip_thinking_blocks(content),
                                len(state.files_changed_this_turn),
                            ),
                            state.gaya_bukti_turn,
                        )
                        stream_utils.rekam_respon_mentah(content, _teks_jawab)
                        _teks_jawab += _blok_persistensi_bila_perlu(
                            state, deps.tool_executor
                        )
                        _tutup_utas_bila_disarankan(state, clean_content)
                        _teks_jawab += _blok_delegasi_akhir(
                            state, klaim=guard_rules.kata_klaim(clean_content)
                        )
                        _teks_jawab += _blok_referensi_belum_dibaca(
                            state,
                            getattr(deps.tool_executor, "project_path", "."),
                        )
                        _teks_jawab += _blok_gaya_tanpa_verifikasi(state, deps)
                        _teks_jawab += _blok_todo_nol_bukti(state)
                        _teks_jawab += _blok_debt_verifikasi(state)
                        _teks_jawab += _blok_edit_gagal_akhir(state)
                        yield sse.ev("token", text=_teks_jawab)
                        deps.context_manager.add_message(
                            "assistant",
                            _teks_jawab,
                            reasoning_content=reasoning_content,
                        )
                        yield sse.ev("done")
                        break

    except Exception as e:
        error_msg = str(e)
        if deps.logger is not None:
            deps.logger.exception("[ORION EXCEPTION] agent loop failed")
        else:
            print(f"[ORION EXCEPTION] {error_msg}")
        yield sse.ev("error", text=error_msg)
        yield sse.ev("done")
