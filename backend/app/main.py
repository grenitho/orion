import os
import sys

sys.stdout = open(
    os.path.join(os.path.expanduser("~"), "orion_stdout.log"),
    "a",
    encoding="utf-8",
    buffering=1,
)
sys.stderr = sys.stdout

# >>> FIX KRITIS: load_dotenv() HARUS dipanggil DI SINI, PALING AWAL, SEBELUM
# modul apa pun di bawah ini di-import. Sebelumnya load_dotenv() dipanggil
# jauh di bawah (setelah `from app.services.llm_router import get_ai_response`).
# llm_router.py membaca os.getenv("GEMINI_API_KEY_1/2/3", "DEFAULT_MODEL", dst)
# LANGSUNG di level atas modul (dieksekusi SEKALI saat modul di-import, bukan
# di dalam fungsi) -- jadi kalau load_dotenv() belum dipanggil pada saat itu,
# .env belum masuk ke os.environ sama sekali, dan GEMINI_API_KEYS akan
# menjadi list KOSONG. Akibatnya kondisi `if GEMINI_API_KEYS and PRIMARY_MODEL`
# di llm_router.py gagal, dan Gemini (Tier 1) DILEWATI TOTAL tanpa pernah
# dicoba sama sekali -- router langsung loncat ke Tier 2 (OpenRouter) / Tier 3
# (Cloudflare) walau kuota Gemini sama sekali belum habis. Ini penyebab nyata
# dari laporan "Orion tidak pernah pakai Gemini, langsung loncat ke fallback".
from dotenv import load_dotenv

load_dotenv()
# >>> AKHIR FIX KRITIS <<<

import asyncio
import json
import re
import uuid

import tiktoken

_THINKING_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)

_token_encoder = tiktoken.get_encoding("cl100k_base")


def validate_project_structure(project_path: str) -> list:
    """Cek dan laporkan struktur folder yang mencurigakan (duplikat, sampah, dll)."""
    warnings = []

    # Cek duplikasi folder assets
    for root, dirs, _files in os.walk(project_path):
        # Abaikan folder venv dan target agar tidak false positive
        if "venv" in root or "target" in root or ".git" in root:
            continue

        if "assets" in dirs:
            assets_path = os.path.join(root, "assets")
            # Jika ada folder assets di dalam folder lain (bukan root project), warning
            expected_assets = os.path.join(project_path, "assets")
            if os.path.normpath(assets_path) != os.path.normpath(expected_assets):
                warnings.append(f"⚠️ Folder duplikat terdeteksi: {assets_path}")

    return warnings


def fix_over_escaped_text(text) -> str:
    """Beberapa model (terutama tier fallback OpenRouter/Cloudflare) kadang
    men-double-escape newline saat generate JSON 'arguments' untuk tool call
    yang berisi teks multi-baris (misal 'question' di ask_user) — menulis
    '\\\\n' alih-alih '\\n' di dalam JSON. Setelah json.loads(), ini muncul
    sebagai literal dua-karakter '\\n' (bukan newline asli) di layar user.
    Fungsi ini menormalkan itu tanpa merusak string yang memang sudah benar.

    >>> FIX: versi lama cuma .replace("\\n", "\n") sekali jalan (1 pass).
    Itu cukup untuk over-escape 1 lapis, tapi kalau modelnya over-escape
    2x lipat atau lebih (mis. JSON mentah berisi banyak backslash beruntun
    sebelum 'n'), 1 pass replace / bahkan replace berulang tetap menyisakan
    backslash nyasar di depan newline (mis. jadi '\\\\n' bukan newline
    bersih) karena replace() mencocokkan tepat 1 backslash + 'n', bukan
    RANGKAIAN backslash + 'n'. Sekarang pakai regex \\+n / \\+t / \\+r yang
    menyapu SELURUH rentetan backslash di depan n/t/r sekaligus jadi satu
    whitespace asli, berapa pun lapisan escape-nya, dalam 1 pass — tidak
    ada lagi sisa backslash."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\\+n", "\n", text)
    text = re.sub(r"\\+t", "\t", text)
    text = re.sub(r"\\+r", "\r", text)
    return text


def strip_thinking_blocks(text: str) -> str:
    """Hapus blok <thinking>...</thinking> sebelum ditampilkan ke user di frontend.
    Versi RAW (dengan thinking) tetap disimpan ke context_manager untuk memori
    internal Orion — hanya yang di-yield ke stream yang dibersihkan."""
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
    """Estimasi token untuk 1 pesan, TERMASUK tool_calls (name + arguments).
    Sebelumnya hanya menghitung 'content', sehingga pesan dengan tool_calls
    besar (misal 'diff' panjang di edit_file) diremehkan estimasinya."""
    # >>> FITUR LAMPIRAN: content bisa berupa LIST part (multimodal).
    # Tanpa ini, str(list) menghasilkan repr Python yang memuat seluruh base64
    # -> estimasi token meledak dan auto-summarize salah memotong riwayat.
    _c = msg.get("content")
    if isinstance(_c, list):
        _teks = " ".join(
            str(p.get("text", "")) for p in _c
            if isinstance(p, dict) and p.get("type") == "text"
        )
        # Gemini menagih +/- 258 token per gambar, jauh lebih murah dari base64-nya
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


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services import guard_rules, research_rules, stream_utils
from app.services.prompts import SYSTEM_PROMPT
from app.services.tools_schema import TOOLS
from app.services.context_manager import ContextManager
from app.services.dependency_scanner import DependencyScanner
from app.services.llm_router import get_ai_response  # <-- Satu-satunya gerbang ke LLM
from app.services.tool_executor import ToolExecutor

# >>> FIX KRITIS: 3 modul tambahan (todo tracking, verifikasi pasca-edit,
# permission/sandboxing) SEBELUMNYA hard-import -- kalau salah satu file ini
# belum ditaruh di app/services/ (atau typo/rusak), SELURUH backend akan
# ModuleNotFoundError dan gagal start TOTAL sebelum sempat listen di port
# mana pun (persis gejala "backend tidak bisa dipakai sama sekali").
# Sekarang jadi soft-dependency: kalau gagal import, fitur terkait saja yang
# nonaktif (dengan fallback aman), backend tetap menyala normal.
try:
    from app.services.todo_manager import TodoManager
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] todo_manager.py gagal diimpor ({e}). Fitur todo-list dinonaktifkan."
    )
    TodoManager = None

try:
    from app.services.post_edit_verifier import (
        extract_replace_snippet,
        verify_file_edit,
        verify_file_write,
    )
except Exception as e:
    print(
        f"[ORION STARTUP WARNING] post_edit_verifier.py gagal diimpor ({e}). Verifikasi pasca-edit dinonaktifkan."
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
        f"[ORION STARTUP WARNING] permission_manager.py gagal diimpor ({e}). Path-safety guard DINONAKTIFKAN (fallback: semua path dianggap aman -- SEGERA pasang file ini kembali)."
    )

    def is_path_safe(project_path, file_path):
        return True, "OK (permission_manager tidak tersedia, guard dilewati)"

    def is_sensitive_file(file_path):
        return False


import logging
from datetime import datetime

logging.basicConfig(
    filename=os.path.join(os.path.expanduser("~"), "orion_crash.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

# (load_dotenv() sudah dipanggil di paling atas file, sebelum import
# app.services.* -- lihat komentar FIX KRITIS di baris ~11)

# --- DAFTAR PERINTAH BERBAHAYA ---
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
    "tauri dev",  # <-- TAMBAHKAN INI
    "cargo run",  # <-- TAMBAHKAN INI
]


def is_long_running_server(cmd: str) -> bool:
    """Cek apakah perintah ini menjalankan server yang berjalan selamanya."""
    cmd_lower = cmd.lower()
    return any(pattern in cmd_lower for pattern in BLOCKING_SERVER_PATTERNS)


def is_dangerous_command(cmd: str) -> bool:
    """Cek apakah perintah terminal mengandung kata kunci berbahaya."""
    cmd_lower = cmd.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd_lower:
            return True
    return False


def is_terminal_failure(output: str) -> bool:
    """Mendeteksi apakah output terminal mengandung error fatal yang butuh perbaikan kode."""
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


async def summarize_old_history(old_messages: list) -> str:
    """Memanggil LLM untuk merangkum riwayat chat lama.
    PENTING: Fungsi ini TIDAK MENELAN EXCEPTION. Jika API gagal, error akan dilempar ke pemanggil.
    """
    prompt = "Rangkum percakapan berikut menjadi 1 paragraf padat yang berisi konteks utama, keputusan yang diambil, dan status tugas saat ini. Abaikan basa-basi.\n\n"
    for msg in old_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if content:
            prompt += f"{role}: {content[:500]}\n"

    # HAPUS try/except di sini. Biarkan exception naik ke main.py agar clear_history() tidak dipanggil saat gagal.
    response = await get_ai_response(
        messages=[{"role": "user", "content": prompt}], tools=None
    )
    return response.choices[0].message.content


def log_performance(
    project_path: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: float,
    status: str,
):
    """Mencatat statistik penggunaan API ke file .orion/performance_log.jsonl"""
    try:
        orion_dir = os.path.join(project_path, ".orion")
        os.makedirs(orion_dir, exist_ok=True)
        log_file = os.path.join(orion_dir, "performance_log.jsonl")

        # >>> HARGA REALISTIS (DeepSeek V3/R1 via API) <<<
        price_input_per_million = 0.14
        price_output_per_million = 0.28

        estimated_cost_usd = (input_tokens / 1_000_000) * price_input_per_million + (
            output_tokens / 1_000_000
        ) * price_output_per_million

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(estimated_cost_usd, 6),
            "duration_ms": round(duration_ms, 2),
            "status": status,
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    except Exception as e:
        print(f"[ORION PERF LOG WARNING] Gagal mencatat log: {e}")


# >>> PATCH ORION-5: logging terstruktur ke FILE.
# CATATAN KOREKSI: dugaan awal saya bahwa 122 print() menyebabkan backpressure
# stdout TIDAK TEPAT -- baris 4-10 file ini sudah mengalihkan sys.stdout/stderr
# ke ~/orion_stdout.log dengan buffering=1, jadi pipe induk tidak pernah dibanjiri.
# Masalah itu sudah Anda selesaikan sendiri lebih dulu.
#
# Yang MASIH benar dan diperbaiki di sini:
#   1. ~/orion_stdout.log dibuka mode "a" TANPA ROTASI -> tumbuh selamanya.
#      RotatingFileHandler di bawah membatasi 5 MB x 5 file.
#   2. print() tidak punya level/timestamp/nama modul -> sulit menyaring.
#   3. Lokasinya di HOME, bukan per-proyek -> log beberapa proyek tercampur.
# Redirect stdout yang lama SENGAJA dibiarkan supaya tidak ada perubahan perilaku
# yang tidak perlu; keduanya bisa hidup berdampingan.
try:
    from app.logging_setup import get_logger, setup_logging
except ImportError:  # pragma: no cover
    from logging_setup import get_logger, setup_logging  # type: ignore[no-redef]

setup_logging()
logger = get_logger("orion.main")

app = FastAPI(title="Project Orion Backend", version="0.4.1")
app.add_middleware(
    CORSMiddleware,
    # >>> PATCH ORION-37 (KEAMANAN): allow_origins=["*"] + /execute-terminal tanpa
    # auth = lubang RCE lokal: selagi backend hidup, halaman web jahat yang dibuka
    # user bisa POST lintas-origin ke 127.0.0.1:8000 dan menjalankan perintah shell,
    # melewati modal persetujuan VS Code. Request JSON lintas-origin memicu preflight;
    # dengan origin tidak diizinkan, browser tidak mengirim request itu sama sekali.
    # Klien sah (extension host / Node) tidak tunduk pada CORS, jadi tidak ada yang rusak.
    allow_origins=[],
    allow_origin_regex=r"^vscode-webview://",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
context_manager = ContextManager()
tool_executor = None
todo_manager = (
    TodoManager() if TodoManager else None
)  # >>> FIX: None-safe kalau import gagal
orion_config = {"max_iterations": 20}  # Default config

# File untuk menyimpan project terakhir
LAST_PROJECT_FILE = os.path.join(os.path.expanduser("~"), ".orion_last_project.json")


def save_last_project(path: str):
    """Simpan path project terakhir ke disk."""
    try:
        with open(LAST_PROJECT_FILE, "w", encoding="utf-8") as f:
            json.dump({"path": path}, f)
        print(f"[ORION PERSIST] Project path disimpan ke {LAST_PROJECT_FILE}")
    except Exception as e:
        print(f"[ORION PERSIST WARNING] Gagal menyimpan last project: {e}")


def load_last_project():
    """Load project path terakhir dari disk."""
    try:
        if os.path.exists(LAST_PROJECT_FILE):
            with open(LAST_PROJECT_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("path")
    except Exception as e:
        print(f"[ORION PERSIST WARNING] Gagal memuat last project: {e}")
    return None


def load_orion_config(project_path: str):
    """Membaca atau membuat file config default di .orion/orion_config.json"""
    global orion_config
    config_path = os.path.join(project_path, ".orion", "orion_config.json")

    print(f"[ORION CONFIG DEBUG] Mencoba membaca config dari: {config_path}")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)
                orion_config = {**orion_config, **loaded_config}
                print(f"[ORION CONFIG] ✅ Berhasil dimuat: {orion_config}")
                print(
                    f"[ORION CONFIG] Nilai max_iterations: {orion_config.get('max_iterations')}"
                )
        except Exception as e:
            print(
                f"[ORION CONFIG WARNING] Gagal membaca config, pakai default. Error: {e}"
            )
    else:
        default_config = {
            "max_iterations": 20,
            # >>> BARU: threshold token budget bisa dikonfigurasi per-project.
            # Sesuaikan dengan context window model yang benar-benar Anda pakai
            # (jangan asumsikan 8K jika model Anda mendukung context lebih besar).
            "token_budget_threshold": 12000,
            "description": "Pengaturan Orion. Ubah max_iterations untuk membatasi loop berpikir AI. token_budget_threshold mengatur kapan emergency pruning context terjadi.",
        }
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2)
            orion_config = default_config
            print(f"[ORION CONFIG] File config default dibuat di {config_path}")
        except Exception as e:
            print(f"[ORION CONFIG WARNING] Gagal membuat file config: {e}")






# >>> PATCH ORION-15: PEMERIKSA KONSISTENSI TOOL =====================
# Bug "3 tool mati" terjadi karena skema TOOLS dan rantai dispatch di /chat
# dikelola terpisah, jadi keduanya bisa menyimpang tanpa terdeteksi.
# Pemeriksa ini berjalan sekali saat startup dan MELAPORKAN ke log kalau ada
# tool yang dideklarasikan tapi tidak punya method di tool_executor.
# (Dispatch-nya sendiri tetap harus ditambahkan manual di /chat -- lihat
#  pesan peringatannya.)
# =====================================================================
_NAMA_TOOL_AKTIF = {
    "ask_user", "update_todos", "list_files", "read_file", "read_file_range",
    "write_file", "edit_file", "search_code", "read_json_path", "run_terminal",
    "web_search", "web_fetch", "git_status", "git_diff", "verify_html",
    "verify_code",
    "restore_backup", "list_backups", "delete_file", "move_file", "git_commit",
    "run_tests",
}


def periksa_konsistensi_tools() -> None:
    """Bandingkan skema TOOLS vs daftar dispatch. Log peringatan bila menyimpang."""
    dideklarasikan = set()
    for t in TOOLS:
        nama = (t.get("function") or {}).get("name")
        if nama:
            dideklarasikan.add(nama)

    mati = dideklarasikan - _NAMA_TOOL_AKTIF
    berlebih = _NAMA_TOOL_AKTIF - dideklarasikan

    if mati:
        logger.error(
            "[TOOLS] %d tool DIDEKLARASIKAN tapi TIDAK punya cabang dispatch di /chat: %s. "
            "Model akan selalu menerima 'Tool tidak tersedia'. "
            "Tambahkan cabang `elif func_name == ...` di generate_stream().",
            len(mati), sorted(mati),
        )
    if berlebih:
        logger.warning(
            "[TOOLS] %d tool punya dispatch tapi TIDAK dideklarasikan di skema TOOLS: %s. "
            "Model tidak akan pernah memanggilnya.",
            len(berlebih), sorted(berlebih),
        )
    if not mati and not berlebih:
        logger.info("[TOOLS] Konsisten: %d tool terdeklarasi & terdispatch.", len(dideklarasikan))


periksa_konsistensi_tools()
# >>> AKHIR PATCH ORION-15


class ProjectPathRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    message: str
    # >>> FITUR LAMPIRAN: daftar {name, mime, data(base64)} dari extension.
    # Default [] supaya request lama (tanpa field ini) tetap valid.
    attachments: list = []
    # >>> PATCH ORION-20c: "mata" Orion dari VS Code.
    # {activeFile, selection, diagnostics[]}. Kosong kalau extension lama.
    editor: dict = {}


@app.get("/")
async def root():
    return {"status": "Orion Backend is running!", "version": "0.4.1"}


@app.post("/set-project")
async def set_project(request: ProjectPathRequest):
    global tool_executor

    if not os.path.isdir(request.path):
        return {"success": False, "error": "Folder tidak ditemukan."}

    # 1. Validasi struktur folder
    structure_warnings = validate_project_structure(request.path)

    # 2. Set path dan inisialisasi tool executor
    context_manager.set_project_path(request.path)
    tool_executor = ToolExecutor(request.path)
    if todo_manager:
        todo_manager.set_project_path(request.path)  # >>> FIX: guard None-safe

    # 3. Simpan ke disk untuk auto-restore
    save_last_project(request.path)

    # 4. Inisialisasi folder .orion/
    orion_dir = os.path.join(request.path, ".orion")
    if not os.path.exists(orion_dir):
        os.makedirs(orion_dir)
        print(f"[ORION INIT] Membuat folder .orion/ di {orion_dir}")

        default_files = {
            "master_context.md": "# Master Context\n\nProject: [Nama Project]\nVersi: 0.1\nTeknologi: \n",
            "decision_log.md": "# Decision Log\n\nBelum ada keputusan arsitektur.\n",
            "coding_rules.md": "# Coding Rules\n\nBelum ada aturan coding.\n",
            "current_sprint.md": "# Current Sprint\n\nSprint: Inisialisasi\nStatus: Baru dimulai\n",
        }
        for filename, content in default_files.items():
            file_path = os.path.join(orion_dir, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

    # 5. Scan dependency dan simpan ke project_map
    try:
        scanner = DependencyScanner(request.path)
        scan_result = scanner.scan()
        print(
            f"[ORION INIT] Dependency Map di-generate. Total {scan_result['total_files']} file terpindai."
        )

        # Buat string struktur project dan simpan ke context_manager
        map_lines = [f"📁 {path}" for path in scan_result["files"].keys()]
        project_map_str = "Struktur Project:\n" + "\n".join(map_lines)
        context_manager.set_project_map(project_map_str)

    except Exception as e:
        print(f"[ORION INIT WARNING] Gagal generate Dependency Map: {e}")
        context_manager.set_project_map("Struktur Project: Gagal memindai.")

    # 6. Load config
    load_orion_config(request.path)

    return {
        "success": True,
        "message": "Project berhasil dipindai.",
        "project_map_preview": context_manager.get_project_map()[:500] + "...",
        "structure_warnings": structure_warnings,  # Kirim warning ke frontend
    }


async def execute_summarize(context_manager, history, threshold=15):
    """Helper untuk menjalankan sliding window summarize. Mengembalikan True jika berhasil."""
    if len(history) <= threshold:
        return False

    # Gunakan safe_split_index agar tidak memutus pasangan assistant+tool
    split_idx = context_manager.safe_split_index(history, 10)
    messages_to_summarize = history[:split_idx]
    recent_messages = history[split_idx:]

    prompt = "Rangkum percakapan dan hasil tool berikut menjadi 1 paragraf padat. Fokus pada: file apa yang sudah dibaca/diubah, error apa yang ditemukan, dan apa langkah selanjutnya. Abaikan basa-basi.\n\n"
    for msg in messages_to_summarize:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))[:1000]
        prompt += f"{role}: {content}\n"

    # Buat backup sebelum operasi berisiko
    context_manager.create_backup()

    # Panggil API (akan raise exception jika gagal, berkat perbaikan Kritikal #1)
    summary_text = await summarize_old_history(messages_to_summarize)

    # Hapus history lama dan isi dengan ringkasan + pesan terbaru
    context_manager.clear_history()
    context_manager.add_message("user", f"[RINGKASAN SESI SEBELUMNYA]: {summary_text}")
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
    """Helper terpusat untuk memangkas history context_manager secara PERMANEN
    menggunakan safe_split_index (agar pasangan assistant tool_calls + tool
    response tidak pernah terputus / menjadi 'yatim piatu').
    Dipakai baik oleh recovery error 400 maupun oleh token budget guard,
    supaya pemangkasan konsisten dan tidak perlu diulang tiap iterasi."""
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


@app.on_event("startup")
async def auto_restore_project():
    """Auto-restore project terakhir saat backend baru menyala."""
    global tool_executor
    last_path = load_last_project()
    if last_path and os.path.isdir(last_path):
        print(f"[ORION STARTUP] 🔄 Auto-restore project sebelumnya: {last_path}")
        context_manager.set_project_path(last_path)
        tool_executor = ToolExecutor(last_path)
        if todo_manager:
            todo_manager.set_project_path(last_path)  # >>> FIX: guard None-safe
        load_orion_config(last_path)
        print("[ORION STARTUP] ✅ Project berhasil dipulihkan. Tool executor siap.")
    else:
        print(
            "[ORION STARTUP] Tidak ada project terakhir yang ditemukan. Menunggu /set-project..."
        )


@app.post("/chat")
async def chat_with_orion(request: ChatRequest):
    if not context_manager.project_map or tool_executor is None:

        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'text': 'Silakan set project terlebih dahulu.'})}\n\n"

        return StreamingResponse(error_gen(), media_type="text/event-stream")

    # >>> FITUR LAMPIRAN ==============================================
    # Gambar TIDAK disimpan ke riwayat. Base64 satu screenshot 1 MB menjadi
    # +/- 1,4 juta karakter; kalau masuk session_history.json maka setiap
    # iterasi agent mengirim ulangnya dan kuota free tier habis cepat.
    # Riwayat hanya menyimpan placeholder teks; gambar asli disuntikkan ke
    # api_messages HANYA pada iterasi pertama (lihat patch B4b).
    # ==================================================================
    from app.services.attachments import build_user_content, describe_attachments

    _lamp_desc = describe_attachments(request.attachments)
    _content_parts, _riwayat_text = build_user_content(request.message, request.attachments)
    # Holder mutable (list 1 elemen), BUKAN variabel biasa. Alasannya: kalau
    # generate_stream() melakukan `_pending = None`, Python menganggapnya
    # variabel LOKAL generator itu (bukan closure), sehingga akses pertama
    # melempar UnboundLocalError. Dengan list, kita memutasi isinya, bukan
    # mengikat ulang namanya -- closure tetap berlaku. (Ditangkap oleh ruff F823.)
    _pending_image_parts = [_content_parts if isinstance(_content_parts, list) else None]
    if _lamp_desc:
        logger.info("[ATTACHMENT] %s", _lamp_desc)

    context_manager.add_message("user", _riwayat_text)

    # >>> OPTIMASI CACHE: Pisahkan Pesan Statis dan Dinamis <<<

    # 1. SYSTEM PROMPT (100% STATIS - Tidak boleh ada f-string dinamis di sini)
    static_system_prompt = SYSTEM_PROMPT

    # >>> PATCH ORION-20d ================================================
    # SUNTIKAN KONTEKS EDITOR. Ini yang membuat Orion "melihat":
    #   - tahu file mana yang sedang Anda buka
    #   - tahu teks mana yang Anda sorot (konteks paling relevan, gratis)
    #   - tahu error yang dilaporkan VS Code di panel Problems
    # Bagian terakhir adalah pencegah halusinasi paling kuat: kalau Orion
    # baru mengedit file X dan Problems menyebut ERROR di file X, ia diberi
    # instruksi eksplisit untuk memperbaikinya sebelum mengaku selesai.
    # ====================================================================
    _editor_ctx = request.editor if isinstance(request.editor, dict) else {}
    _editor_block = ""

    _aktif = str(_editor_ctx.get("activeFile") or "")
    if _aktif and not _aktif.startswith(".."):
        _editor_block += f"\n\nFILE YANG SEDANG DIBUKA USER SAAT INI: {_aktif}"

    _sel = str(_editor_ctx.get("selection") or "")
    if _sel:
        _editor_block += f"\n\nTEKS YANG SEDANG DISOROT USER DI FILE ITU:\n```\n{_sel[:4000]}\n```"

    _diags = _editor_ctx.get("diagnostics") or []
    if isinstance(_diags, list) and _diags:
        _editor_block += (
            "\n\nDIAGNOSTICS TERBARU DARI VS CODE (panel Problems). "
            "Ini FAKTA tentang keadaan kode saat ini, lebih akurat daripada dugaanmu:\n"
            + "\n".join(str(d) for d in _diags[:30])
            + "\n\nATURAN: kalau kamu baru saja mengubah sebuah file dan daftar di atas "
            "menyebut ERROR pada file tersebut, berarti perubahanmu RUSAK atau belum lengkap. "
            "Perbaiki dulu sampai errornya hilang SEBELUM melaporkan tugas selesai. "
            "Kalau errornya ada di file yang tidak kamu sentuh, sebutkan ke user sebagai temuan, "
            "jangan diam-diam abaikan."
        )

    context_message = {
        "role": "user",
        "content": f"Struktur project saat ini:\n{context_manager.get_project_map()}"
        + _editor_block,
    }

    # >>> AKHIR OPTIMASI CACHE <<<

    async def _aliran_inti():
        # >>> AUTO-SUMMARIZE CONTEXT WINDOW (SLIDING WINDOW) <<<
        # Jika history lebih dari 15 pesan, ringkas yang lama
        # >>> AUTO-SUMMARIZE CONTEXT WINDOW (AWAL CHAT) <<<
        history = context_manager.get_history()
        if len(history) > 15:
            yield f"data: {json.dumps({'type': 'status', 'text': '🧠 Memori penuh, sedang mengompresi konteks...' })}\n\n"
            try:
                await execute_summarize(context_manager, history, threshold=15)
                print("[ORION SUMMARIZE] Konteks awal berhasil diringkas.")
            except Exception as e:
                print(
                    f"[ORION SUMMARIZE WARNING] Gagal merangkum awal: {e}. History dipertahankan."
                )
        # >>> AKHIR AUTO-SUMMARIZE <<<

        # >>> PATCH ORION-4: default fallback (500) berbeda dari default config
        # di atas (20). Kalau orion_config.json rusak/hilang, limit melompat dari
        # 20 ke 500 tanpa peringatan. Disamakan + diberi rem pengaman.
        max_iter = orion_config.get("max_iterations", 20)
        if not isinstance(max_iter, int) or max_iter < 1:
            max_iter = 20
        max_iter = min(max_iter, 200)   # rem pengaman: cegah loop tak berujung

        # >>> BARU: threshold token budget dibaca dari config, bukan hardcode.
        # Sesuaikan dengan context window model aktual (sisakan buffer untuk output).
        token_budget_threshold = orion_config.get("token_budget_threshold", 12000)
        if not isinstance(token_budget_threshold, int) or token_budget_threshold < 1000:
            token_budget_threshold = 12000

        print(
            f"[ORION CONFIG] File config yang dibaca: {os.path.join(tool_executor.project_path, '.orion', 'orion_config.json')}"
        )
        print(f"[ORION CONFIG] Nilai max_iterations yang digunakan: {max_iter}")
        print(f"[ORION CONFIG] Token budget threshold: {token_budget_threshold}")

        try:
            analyzed_files = set()
            last_tool_calls = []

            # >>> FIX KRITIS: tracker file yang BENAR-BENAR diubah pada TURN ini saja.
            # Sebelumnya, checkpoint "TASK COMPLETED" hanya mengandalkan keyword
            # generik ("selesai", "berhasil", dll) pada teks model — kata yang SANGAT
            # umum muncul di kalimat audit/laporan biasa sekalipun TIDAK ADA tool
            # edit_file/write_file/create_file yang benar-benar dipanggil. Akibatnya
            # Orion bisa "mengaku selesai" padahal tidak mengerjakan apa pun, atau
            # mengulang audit yang sama karena tidak pernah benar-benar mengeksekusi
            # perubahan. Tracker ini dipakai sebagai syarat WAJIB sebelum checkpoint
            # completion boleh memicu pause (lihat pemakaiannya di bawah).
            files_changed_this_turn = []

            # >>> ANTI-INFINITE-LOOP GUARD <<<
            consecutive_400_errors = 0
            last_400_error_msg = ""

            # >>> PATCH ORION-1b: batas berapa kali loop boleh "menyenggol" model
            # untuk melanjutkan todo yang tersisa. Mencegah loop tak berujung kalau
            # model terus menulis "selesai" tanpa benar-benar mengerjakan apa pun.
            _nudge_count = 0
            MAX_TODO_NUDGE = int(os.getenv("ORION_MAX_TODO_NUDGE", "10"))

            # >>> PATCH ORION-17a: penghitung "verifikasi klaim".
            # Dipakai untuk menantang model yang mengaku selesai tanpa mengubah file.
            _verifikasi_count = 0
            MAX_VERIFIKASI = int(os.getenv("ORION_MAX_VERIFIKASI", "2"))

            # >>> KONVENSI: `_` di bawah adalah PENGHITUNG LOOP agent.
            # Jangan pernah memakai `_` sebagai variabel buang di dalam loop ini
            # (lihat test_loop_counter_guard.py yang menjaganya).
            for _ in range(max_iter):
                # >>> LANGKAH 1: DYNAMIC SLIDING WINDOW SUMMARIZE (DI DALAM LOOP) <<<
                history = context_manager.get_history()
                if len(history) > 20:
                    yield f"data: {json.dumps({'type': 'status', 'text': '🧠 Memori penuh, sedang mengompresi konteks...' })}\n\n"
                    try:
                        await execute_summarize(context_manager, history, threshold=20)
                        print(
                            "[ORION SUMMARIZE] Konteks di dalam loop berhasil diringkas."
                        )
                    except Exception as e:
                        print(
                            f"[ORION SUMMARIZE WARNING] Gagal merangkum di loop: {e}. History dipertahankan."
                        )
                # >>> AKHIR DYNAMIC SLIDING WINDOW SUMMARIZE <<<

                # >>> BARU: SOFT LIMIT CHECKPOINT (Human-in-the-Loop) <<<
                # Jika sudah mencapai 80% dari max_iter, paksa Orion untuk melapor dan meminta izin
                soft_limit = int(max_iter * 0.8)
                if _ == soft_limit:
                    warning_msg = (
                        f"\n\n**[PERINGATAN SISTEM - SOFT LIMIT]**\n"
                        f"Saya telah mencapai {_} iterasi (80% dari batas maksimal {max_iter}).\n"
                        f"Agar tidak membuang token, saya akan berhenti sejenak.\n\n"
                        f"**Status saat ini:** Saya sedang mengerjakan [sebutkan task terakhir].\n"
                        f"**Opsi untuk Anda:**\n"
                        f"1. Ketik **'lanjut'** jika saya berada di jalur yang benar.\n"
                        f"2. Berikan instruksi baru jika saya terlihat berputar-putar atau salah arah.\n"
                    )

                    # Simpan ke history agar Orion ingat dia sudah diperingatkan
                    context_manager.add_message(
                        "user", f"[PERINGATAN SISTEM]: {warning_msg}"
                    )

                    # Kirim ke frontend dan PAUSE
                    yield f"data: {json.dumps({'type': 'token', 'text': warning_msg})}\n\n"
                    yield f"data: {json.dumps({'type': 'ask_user', 'question': 'Soft limit tercapai. Lanjutkan atau ubah instruksi?'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    print(
                        "[ORION SOFT LIMIT] Mencapai 80% iterasi. Menunggu instruksi user."
                    )
                    return  # Hentikan stream, tunggu user mengetik 'lanjut'
                # >>> AKHIR SOFT LIMIT CHECKPOINT <<<

                # >>> LANGKAH 2: REBUILD PAYLOAD DARI SUMBER KEBENARAN <<<
                # JANGAN gunakan variabel 'messages' lokal yang menumpuk. Ambil fresh dari history yang sudah diringkas.
                current_history = context_manager.get_history()
                api_messages = [
                    {"role": "system", "content": static_system_prompt},
                    context_message,
                ] + current_history

                # >>> FITUR LAMPIRAN (B4b): suntik gambar hanya di iterasi pertama.
                # Setelah itu riwayat sudah berisi placeholder teks, jadi iterasi
                # berikutnya tidak mengirim ulang base64 (hemat kuota free tier).
                if _ == 0 and _pending_image_parts[0]:
                    for _i in range(len(api_messages) - 1, -1, -1):
                        if api_messages[_i].get("role") == "user":
                            api_messages[_i] = {**api_messages[_i], "content": _pending_image_parts[0]}
                            break
                    _pending_image_parts[0] = None   # sekali pakai
                    if _lamp_desc:
                        yield f"data: {json.dumps({'type': 'status', 'text': f'📎 {_lamp_desc}'})}\n\n"
                # >>> AKHIR FITUR LAMPIRAN

                # >>> LANGKAH 3: SHORT REPLY ANTI-AMNESIA GUARD <<<
                if _ == 0:
                    last_user_idx = -1
                    for i in range(len(api_messages) - 1, -1, -1):
                        msg_content = api_messages[i].get("content", "")
                        if (
                            api_messages[i].get("role") == "user"
                            and "Struktur project saat ini" not in msg_content
                        ):
                            last_user_idx = i
                            break

                    if last_user_idx >= 0:
                        last_user_msg = api_messages[last_user_idx]
                        user_content = last_user_msg.get("content") or ""

                        last_assistant_msg = None
                        for i in range(last_user_idx - 1, -1, -1):
                            if api_messages[i].get("role") == "assistant":
                                last_assistant_msg = api_messages[i]
                                break

                        # PERBAIKAN KRITIS: Ambil content HANYA JIKA last_assistant_msg tidak None
                        last_assistant_content = (
                            (last_assistant_msg.get("content") or "")
                            if last_assistant_msg
                            else ""
                        )

                        # PERBAIKAN KRITIS: Pastikan last_assistant_msg is not None sebelum mengecek panjangnya
                        # >>> FIX ANTI-AMNESIA #2: threshold 500 char pada pesan assistant
                        # sebelumnya menggagalkan guard justru untuk pertanyaan klarifikasi
                        # yang SINGKAT & FOKUS (kasus paling umum) — diturunkan ke 30 char
                        # dan ditambah deteksi universal "diakhiri tanda tanya".
                        if (
                            last_assistant_msg is not None
                            and len(user_content) < 150
                            and len(user_content) > 0
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
                                    "\n\n[SYSTEM CONTEXT REMINDER - WAJIB DIPATUHI]:\n"
                                    "User sedang MENJAWAB pertanyaan/pilihan yang ANDA sendiri berikan di pesan sebelumnya.\n"
                                    "1. JAWAB LANGSUNG berdasarkan konteks rekomendasi Anda sebelumnya.\n"
                                    "2. JANGAN bertanya balik 'untuk keperluan apa'.\n"
                                    "3. JANGAN memanggil tool read_file untuk membaca .orion/ atau file dokumentasi apapun.\n"
                                    "4. Fokus 100% pada apa yang user pilih dan langsung eksekusi.\n"
                                    "[AKHIR REMINDER]"
                                )
                                api_messages[last_user_idx]["content"] = (
                                    user_content + reminder
                                )
                                print(
                                    "[ORION ANTI-AMNESIA] Short reply detected. Context reminder injected."
                                )
                # >>> AKHIR ANTI-AMNESIA GUARD <<<

                # >>> LANGKAH 4: AGGRESSIVE CONTEXT PRUNING <<<
                compressed_messages = []
                total_msgs = len(api_messages)  # ✅ DIPERBAIKI: Gunakan api_messages

                for i, msg in enumerate(
                    api_messages
                ):  # ✅ DIPERBAIKI: Iterate api_messages
                    content_len = len(str(msg.get("content") or ""))

                    # LAYER 3: Pemotongan History Lama (Aman, tidak membuat bodoh)
                    # Jika pesan tool sangat lama (>15 pesan ke belakang), potong jadi 500 char
                    if (
                        msg.get("role") == "tool"
                        and i < total_msgs - 15
                        and content_len > 500
                    ):
                        compressed_msg = msg.copy()
                        compressed_msg["content"] = (
                            str(compressed_msg["content"])[:500]
                            + "\n\n[...DIPOTONG KARENA SUDAH LAMA, LIHAT KESIMPULAN ASSISTEN...]"
                        )
                        compressed_messages.append(compressed_msg)

                    # LAYER 1: Turunkan threshold dari 5000 ke 3000
                    elif msg.get("role") == "tool" and content_len > 3000:
                        if i < total_msgs - 2:
                            compressed_msg = msg.copy()
                            compressed_msg["content"] = (
                                "[OUTPUT PANJANG SEBELUMNYA TELAH DIPROSES OLEH ASISTEN. Lihat pesan 'assistant' setelah ini untuk kesimpulannya. Jangan baca ulang.]"
                            )
                            compressed_messages.append(compressed_msg)
                        else:
                            compressed_messages.append(msg)
                    else:
                        compressed_messages.append(msg)

                api_messages = compressed_messages
                # >>> AKHIR CONTEXT PRUNING <<<

                # >>> TOKEN BUDGET GUARD (Emergency Check) <<<
                # PERBAIKAN: estimasi sekarang menghitung tool_calls (name + arguments) juga,
                # bukan cuma 'content' — sebelumnya diremehkan untuk pesan dengan diff/edit besar.
                estimated_tokens = sum(estimate_message_tokens(m) for m in api_messages)

                if estimated_tokens > token_budget_threshold:
                    print(
                        f"[TOKEN GUARD] Estimasi token tinggi ({estimated_tokens} > {token_budget_threshold}). Melakukan emergency pruning PERMANEN..."
                    )

                    # PERBAIKAN KRITIS: pruning sekarang dilakukan LANGSUNG ke
                    # context_manager (bukan hanya ke variabel lokal api_messages)
                    # menggunakan safe_split_index, supaya:
                    #   1) Tidak perlu diulang setiap iterasi (persisten).
                    #   2) Tidak pernah memotong pasangan assistant.tool_calls
                    #      dan tool response secara terpisah (yang bisa
                    #      menyebabkan error 400 "tool_call_id not found").
                    try:
                        pruned = prune_history_safely(
                            context_manager,
                            keep_last_n=16,
                            notice=(
                                "[SYSTEM NOTICE]: Sebagian riwayat lama dipangkas otomatis "
                                "karena mendekati batas token. Kesimpulan penting (file yang "
                                "diubah, error yang ditemukan) tetap ada di pesan-pesan terakhir."
                            ),
                        )
                        if pruned:
                            print(
                                "[TOKEN GUARD] History context_manager dipangkas permanen."
                            )
                    except Exception as e:
                        print(
                            f"[TOKEN GUARD WARNING] Gagal memangkas history permanen: {e}"
                        )

                    # Rebuild api_messages dari history yang sudah dipangkas
                    current_history = context_manager.get_history()
                    api_messages = [
                        {"role": "system", "content": static_system_prompt},
                        context_message,
                    ] + current_history

                    new_est = sum(estimate_message_tokens(m) for m in api_messages)
                    print(f"[TOKEN GUARD] Berhasil ditekan menjadi: {new_est} tokens")
                # >>> AKHIR TOKEN BUDGET GUARD <<<

                # >>> LANGKAH 5: PANGGILAN BERSIH KE LLM_ROUTER (RESILIENT) <<<
                yield f"data: {json.dumps({'type': 'status', 'text': '⏳ Sedang berpikir...'})}\n\n"

                response = None  # Reset variabel agar tidak memakai nilai stale dari iterasi sebelumnya
                start_time = asyncio.get_event_loop().time()

                # --- 1. BERSIHKAN & GABUNGKAN PESAN (WAJIB DIBUAT DULU) ---
                merged_messages = []
                for msg in api_messages:
                    clean_msg = {"role": msg.get("role")}

                    if "content" in msg and msg["content"] is not None:
                        clean_msg["content"] = str(msg["content"])
                    elif clean_msg["role"] == "assistant":
                        clean_msg["content"] = ""

                    if "tool_calls" in msg:
                        clean_msg["tool_calls"] = msg["tool_calls"]
                    if "tool_call_id" in msg:
                        clean_msg["tool_call_id"] = msg["tool_call_id"]
                    if "name" in msg:
                        clean_msg["name"] = msg["name"]

                    # >>> PERBAIKAN KRITIS #1: JANGAN PERNAH MERGE PESAN 'tool' <<<
                    # Setiap pesan tool WAJIB punya tool_call_id sendiri yang match
                    # 1-ke-1 dengan tool_calls di pesan assistant sebelumnya. Jika dua
                    # pesan tool berurutan digabung, tool_call_id pesan kedua HILANG
                    # dan request akan ditolak API dengan error 400
                    # ("tool_call_id ... not found" / "bad request").
                    if (
                        merged_messages
                        and merged_messages[-1]["role"] == clean_msg["role"]
                        and clean_msg["role"] != "tool"
                    ):
                        prev_content = merged_messages[-1].get("content", "") or ""
                        curr_content = clean_msg.get("content", "") or ""
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
                # --- AKHIR PEMBUATAN VARIABEL ---

                # --- 2. DEBUG: CEK UKURAN PAYLOAD (Sekarang aman karena variabel sudah ada) ---
                print(
                    f"[DEBUG] Jumlah pesan yang akan dikirim: {len(clean_api_messages)}"
                )
                print(f"[DEBUG] Jumlah tools yang akan dikirim: {len(TOOLS)}")
                # --- AKHIR DEBUG ---

                # --- 3. PANGGIL LLM ---
                try:
                    # >>> PATCH ORION-29: HEARTBEAT SELAMA MENUNGGU LLM ==========
                    # Sebelumnya UI benar-benar senyap 30-60 detik selama await ini,
                    # dan keheningan itu tidak bisa dibedakan dari "mati" -- sisa
                    # terakhir dari keluarga keluhan "berhenti diam-diam".
                    # Sekarang tiap 15 detik dikirim status "masih berpikir... N detik".
                    #
                    # Desain SENGAJA tidak memakai stream=True: merangkai tool_call
                    # dari aliran token jauh lebih rapuh, sedangkan heartbeat memberi
                    # umpan balik tanpa menyentuh logika respons sama sekali.
                    # Jika tugas melempar exception, .result() melemparnya kembali
                    # di dalam try ini, sehingga seluruh jalur recovery lama tetap utuh.
                    # ============================================================
                    _loop_waktu = asyncio.get_running_loop()
                    _tugas_llm = asyncio.create_task(
                        get_ai_response(messages=clean_api_messages, tools=TOOLS)
                    )
                    _mulai = _loop_waktu.time()
                    while not _tugas_llm.done():
                        # CATATAN: JANGAN pakai `_` sebagai variabel buang di fungsi ini.
                        # `_` adalah PENGHITUNG LOOP agent (for _ in range(max_iter));
                        # menimpanya membuat `_ > 0` membandingkan set dengan int.
                        # (Bug nyata ORION-29 v1: asyncio.wait mengembalikan dua set.)
                        _selesai, _tunggu_pending = await asyncio.wait({_tugas_llm}, timeout=15)
                        if not _selesai:
                            _detik = int(_loop_waktu.time() - _mulai)
                            yield f"data: {json.dumps({'type': 'status', 'text': f'🤔 Masih berpikir... {_detik} detik'})}\n\n"
                    response = _tugas_llm.result()

                except Exception as api_err:
                    err_str = str(api_err)
                    print(f"[ORION RECOVERY] Error terdeteksi: {err_str[:150]}")

                    # 1. Cek apakah ini Error 400 (Context Length / Bad Request)
                    is_400_error = (
                        "400" in err_str
                        or "context length" in err_str.lower()
                        or "bad request" in err_str.lower()
                    )

                    if is_400_error:
                        consecutive_400_errors += 1

                        # Cek apakah error yang sama berulang
                        current_error_msg = err_str[:200]
                        if (
                            consecutive_400_errors >= 2
                            and current_error_msg == last_400_error_msg
                        ):
                            # ERROR YANG SAMA BERULANG - BERHENTI SEKARANG
                            yield f"data: {json.dumps({'type': 'error', 'text': f'⚠️ Model saat ini kemungkinan TIDAK KOMPATIBEL dengan format permintaan. Error berulang 2x: {err_str[:250]}. Silakan ganti DEFAULT_MODEL di file .env atau periksa dokumentasi provider.'})}\n\n"
                            print(
                                "[ORION RECOVERY] ❌ Error 400 berulang dengan pesan yang sama. Berhenti untuk mencegah infinite loop."
                            )
                            break

                        last_400_error_msg = current_error_msg

                        print(
                            "[ORION RECOVERY] ⚠️ Terdeteksi Error 400. Memaksa ringkas konteks darurat..."
                        )
                        try:
                            prune_history_safely(context_manager, keep_last_n=8)
                            print("[ORION RECOVERY] ✅ History dipangkas darurat.")
                        except Exception as e:
                            print(f"[ORION RECOVERY] Gagal memangkas history: {e}")
                    else:
                        consecutive_400_errors = 0  # Reset jika sukses
                        last_400_error_msg = ""

                    # 2. Safe Yield (Mencegah crash jika Frontend sudah disconnect)
                    try:
                        status_text = (
                            "⚠️ Model lambat, beralih ke cadangan..."
                            if not is_400_error
                            else "⚠️ Konteks terlalu besar, mengecilkan..."
                        )
                        yield f"data: {json.dumps({'type': 'status', 'text': status_text})}\n\n"
                    except (RuntimeError, asyncio.CancelledError):
                        print(
                            "[ORION RECOVERY] Frontend disconnected. Menghentikan stream dengan aman."
                        )
                        break

                    # 3. Suntikkan pesan sistem (Hanya jika bukan error 400)
                    if not is_400_error:
                        context_manager.add_message(
                            "user",
                            "[SYSTEM RECOVERY]: Koneksi terputus. Lanjutkan dengan model alternatif.",
                        )

                    continue  # Lanjut iterasi berikutnya (Fallback akan dicoba oleh llm_router)
                # >>> AKHIR PANGGILAN BERSIH <<<

                if response is None:
                    continue

                msg = response.choices[0].message

                # >>> PERFORMANCE LOGGING (SUCCESS - REAL TOKENS) <<<
                end_time = asyncio.get_event_loop().time()
                duration_ms = (end_time - start_time) * 1000
                usage = getattr(response, "usage", None)
                input_tokens = usage.prompt_tokens if usage else 0
                output_tokens = usage.completion_tokens if usage else 0
                actual_model_used = getattr(response, "model", "auto_rotated_model")

                proj_path = tool_executor.project_path if tool_executor else "."
                log_performance(
                    proj_path,
                    actual_model_used,
                    input_tokens,
                    output_tokens,
                    duration_ms,
                    "success",
                )
                # >>> AKHIR LOGGING <<<

                if getattr(msg, "tool_calls", None):
                    yield f"data: {json.dumps({'type': 'status', 'text': 'Memproses tool...'})}\n\n"

                    if msg.content:
                        cleaned = fix_over_escaped_text(
                            strip_thinking_blocks(msg.content)
                        )
                        if cleaned:
                            yield f"data: {json.dumps({'type': 'token', 'text': cleaned + chr(10) + chr(10)})}\n\n"

                    reasoning_content = getattr(msg, "reasoning_content", None)

                    assistant_msg = {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [],
                    }
                    if reasoning_content:
                        assistant_msg["reasoning_content"] = reasoning_content
                    tool_results = []
                    # >>> BARU: notifikasi sistem yang HARUS ditunda sampai SETELAH
                    # semua tool_results tersimpan (agar urutan pesan tetap valid:
                    # assistant -> tool -> tool -> ... -> user notice).
                    pending_system_notices = []

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

                        # >>> PERBAIKAN KRITIS #2: ask_user HARUS FLUSH tool_results
                        # yang sudah terkumpul SEBELUM return, kalau tidak, tool_calls
                        # sebelumnya di batch yang sama tidak akan pernah punya
                        # balasan 'tool' di history -> orphaned tool_call -> error 400
                        # di request berikutnya. <<<
                        if func_name == "ask_user":
                            question_text = fix_over_escaped_text(
                                func_args.get("question", "")
                            )
                            yield f"data: {json.dumps({'type': 'ask_user', 'question': question_text})}\n\n"

                            context_manager.add_message(
                                "assistant",
                                msg.content,
                                tool_calls=assistant_msg["tool_calls"],
                                reasoning_content=reasoning_content,
                            )

                            # Flush semua tool_results yang SUDAH diproses di batch ini
                            for tr in tool_results:
                                context_manager.add_message(
                                    "tool",
                                    tr["content"],
                                    tool_call_id=tr["tool_call_id"],
                                )

                            # PENTING: Simpan pesan tool placeholder untuk ask_user itu sendiri
                            context_manager.add_message(
                                "tool",
                                "[MENUNGGU JAWABAN USER]",
                                tool_call_id=tc_id,
                            )

                            # Flush notice anti-loop yang mungkin sudah terkumpul
                            # SEBELUM ask_user dipanggil di batch tool_calls yang sama,
                            # supaya tidak hilang karena 'return' di bawah ini.
                            for notice in pending_system_notices:
                                context_manager.add_message("user", notice)

                            return  # HENTIKAN STREAM SEKARANG JUGA. JANGAN LANJUT LOOP!
                        # >>> AKHIR PERBAIKAN #2 (ask_user) <<<

                        try:
                            # >>> BARU: update_todos tidak butuh tool_executor,
                            # jadi dicek PALING AWAL sebelum guard tool_executor is None.
                            if func_name == "update_todos":
                                if todo_manager is None:
                                    result = "Info: Fitur todo-list tidak tersedia saat ini (modul belum terpasang), lanjutkan tanpa update_todos."
                                else:
                                    rendered = todo_manager.set_todos(
                                        func_args.get("todos", [])
                                    )
                                    yield f"data: {json.dumps({'type': 'todo_update', 'markdown': rendered, 'todos': todo_manager.get_todos()})}\n\n"
                                    result = f"Todo list diperbarui:\n{rendered}"

                            elif tool_executor is None:
                                result = "Error: Tool executor belum diinisialisasi."

                            elif func_name == "list_files":
                                path = func_args.get("path", ".")
                                yield f"data: {json.dumps({'type': 'status', 'text': f'📂 Sedang membaca struktur folder: {path}'})}\n\n"
                                result = tool_executor.list_files(**func_args)
                                yield f"data: {json.dumps({'type': 'status', 'text': '✅ Struktur folder berhasil dibaca'})}\n\n"

                            elif func_name == "read_file":
                                file_path = func_args.get("file_path", "")
                                if file_path in analyzed_files:
                                    result = f"[SYSTEM WARNING] REDUNDANSI TERDETEKSI: Anda sudah membaca '{file_path}' di sesi ini. Informasinya sudah ada di memori Anda. JANGAN baca ulang. Lanjutkan analisis atau eksekusi berdasarkan apa yang sudah Anda ketahui."
                                else:
                                    yield f"data: {json.dumps({'type': 'status', 'text': f' Sedang membaca file: {file_path}'})}\n\n"
                                    result = tool_executor.read_file(**func_args)
                                    analyzed_files.add(file_path)
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'✅ File {file_path} berhasil dibaca'})}\n\n"

                            elif func_name == "read_file_range":
                                file_path = func_args.get("file_path", "")
                                start = func_args.get("start_line", 1)
                                end = func_args.get("end_line", 50)

                                # Buat kunci unik berdasarkan file DAN rentang barisnya
                                range_key = f"{file_path}:{start}-{end}"

                                if range_key in analyzed_files:
                                    result = f"[SYSTEM WARNING] REDUNDANSI TERDETEKSI: Anda sudah membaca rentang baris {start}-{end} dari '{file_path}' di sesi ini. Gunakan informasi yang sudah ada."
                                else:
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'📖 Sedang membaca {file_path} (baris {start}-{end})'})}\n\n"
                                    result = tool_executor.read_file_range(**func_args)

                                    # Simpan kunci unik ini, bukan cuma nama file
                                    analyzed_files.add(range_key)

                                    yield f"data: {json.dumps({'type': 'status', 'text': f'✅ File {file_path} berhasil dibaca'})}\n\n"

                            elif func_name == "write_file":
                                file_path = func_args.get("file_path", "unknown")

                                # >>> BARU: PERMISSION/SANDBOXING GUARD <<<
                                # Cek path traversal SEBELUM menyentuh disk sama sekali.
                                path_ok, path_reason = is_path_safe(
                                    tool_executor.project_path, file_path
                                )
                                if not path_ok:
                                    result = f"🚫 [DITOLAK SISTEM - PATH TIDAK AMAN]: {path_reason}"
                                    yield f"data: {json.dumps({'type': 'status', 'text': '🚫 Operasi file ditolak (di luar folder project)'})}\n\n"
                                elif is_sensitive_file(file_path):
                                    result = (
                                        f"🚫 [DITOLAK SISTEM - FILE SENSITIF]: '{file_path}' terdeteksi "
                                        f"sebagai file sensitif (kredensial/kunci). Tidak ditimpa otomatis. "
                                        f"Jika ini memang perlu, minta user melakukannya secara manual atau "
                                        f"konfirmasi eksplisit lewat ask_user terlebih dahulu."
                                    )
                                    yield f"data: {json.dumps({'type': 'status', 'text': '🚫 Ditolak: file sensitif'})}\n\n"
                                else:
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'📝 Sedang membuat/menimpa file: {file_path}'})}\n\n"
                                    result = tool_executor.write_file(**func_args)
                                    print(
                                        f"[ORION WRITE_FILE] Berhasil menulis: {file_path}"
                                    )
                                    # Hanya catat sebagai bukti kerja nyata jika benar2 sukses
                                    if isinstance(
                                        result, str
                                    ) and result.lower().startswith("sukses"):
                                        files_changed_this_turn.append(
                                            f"- `{file_path}` (write_file)"
                                        )
                                        # >>> BARU: VERIFIKASI OTOMATIS PASCA-WRITE <<<
                                        try:
                                            full_path = os.path.join(
                                                tool_executor.project_path, file_path
                                            )
                                            verification_msg = verify_file_write(
                                                full_path,
                                                expected_content=func_args.get(
                                                    "content"
                                                ),
                                            )
                                            result = f"{result}\n{verification_msg}"
                                        except Exception as verify_err:
                                            # Verifikasi gagal TIDAK BOLEH menggagalkan hasil asli --
                                            # ini lapisan tambahan, bukan pengganti write_file.
                                            print(
                                                f"[POST_EDIT_VERIFIER WARNING] {verify_err}"
                                            )
                                    analyzed_files.discard(
                                        file_path
                                    )  # FIX: Invalidate cache agar bisa dibaca ulang jika perlu
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'✅ File {file_path} berhasil dibuat'})}\n\n"

                            elif func_name == "edit_file":
                                file_path = func_args.get("file_path", "unknown")

                                # >>> BARU: PERMISSION/SANDBOXING GUARD <<<
                                path_ok, path_reason = is_path_safe(
                                    tool_executor.project_path, file_path
                                )
                                if not path_ok:
                                    result = f"🚫 [DITOLAK SISTEM - PATH TIDAK AMAN]: {path_reason}"
                                    yield f"data: {json.dumps({'type': 'status', 'text': '🚫 Operasi file ditolak (di luar folder project)'})}\n\n"
                                elif is_sensitive_file(file_path):
                                    result = (
                                        f"🚫 [DITOLAK SISTEM - FILE SENSITIF]: '{file_path}' terdeteksi "
                                        f"sebagai file sensitif (kredensial/kunci). Tidak diedit otomatis. "
                                        f"Jika ini memang perlu, minta user melakukannya secara manual atau "
                                        f"konfirmasi eksplisit lewat ask_user terlebih dahulu."
                                    )
                                    yield f"data: {json.dumps({'type': 'status', 'text': '🚫 Ditolak: file sensitif'})}\n\n"
                                else:
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'🔧 Sedang mengedit file: {file_path}'})}\n\n"
                                    result = tool_executor.edit_file(**func_args)
                                    print(
                                        f"[ORION EDIT_FILE] Berhasil mengedit: {file_path}"
                                    )
                                    # Hanya catat sebagai bukti kerja nyata jika benar2 sukses
                                    if isinstance(
                                        result, str
                                    ) and result.lower().startswith("sukses"):
                                        files_changed_this_turn.append(
                                            f"- `{file_path}` (edit_file)"
                                        )
                                        # >>> BARU: VERIFIKASI OTOMATIS PASCA-EDIT <<<
                                        try:
                                            full_path = os.path.join(
                                                tool_executor.project_path, file_path
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
                                    analyzed_files.discard(
                                        file_path
                                    )  # FIX: Invalidate cache agar bisa dibaca ulang jika perlu
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'✅ File {file_path} berhasil diedit'})}\n\n"

                            elif func_name == "search_code":
                                query = func_args.get("query", "unknown")
                                yield f"data: {json.dumps({'type': 'status', 'text': f'🔍 Sedang mencari: {query}'})}\n\n"
                                result = tool_executor.search_code(**func_args)
                                yield f"data: {json.dumps({'type': 'status', 'text': '✅ Pencarian selesai'})}\n\n"

                            elif func_name == "read_json_path":
                                file_path = func_args.get("file_path", "unknown")
                                json_path = func_args.get("json_path", "unknown")
                                yield f"data: {json.dumps({'type': 'status', 'text': f'📋 Sedang membaca JSON path: {json_path} dari {file_path}'})}\n\n"
                                result = tool_executor.read_json_path(**func_args)
                                yield f"data: {json.dumps({'type': 'status', 'text': '✅ JSON path berhasil dibaca'})}\n\n"

                            elif func_name == "run_terminal":
                                cmd_to_run = func_args.get("command", "")

                                try:
                                    yield f"data: {json.dumps({'type': 'status', 'text': f'⚙️ Sedang menjalankan: {cmd_to_run}'})}\n\n"

                                    # 1. Cek Perintah Berbahaya
                                    if is_dangerous_command(cmd_to_run):
                                        # >>> PERBAIKAN KRITIS #2 (lanjutan): flush
                                        # tool_results sebelumnya dulu, baru assistant
                                        # message, baru placeholder konfirmasi. <<<
                                        context_manager.add_message(
                                            "assistant",
                                            msg.content,
                                            tool_calls=assistant_msg["tool_calls"],
                                            reasoning_content=reasoning_content,
                                        )
                                        for tr in tool_results:
                                            context_manager.add_message(
                                                "tool",
                                                tr["content"],
                                                tool_call_id=tr["tool_call_id"],
                                            )
                                        context_manager.add_message(
                                            "tool",
                                            f"[MENUNGGU KONFIRMASI]: {cmd_to_run}",
                                            tool_call_id=tc_id,
                                        )
                                        # Flush notice anti-loop yang mungkin sudah
                                        # terkumpul sebelum konfirmasi ini dipicu.
                                        for notice in pending_system_notices:
                                            context_manager.add_message("user", notice)
                                        yield f"data: {json.dumps({'type': 'confirm_terminal', 'command': cmd_to_run})}\n\n"
                                        result = "[MENUNGGU KONFIRMASI USER]"
                                        return  # Keluar dari loop tool saat ini, biarkan frontend menunggu user

                                    # 1b. Cek Perintah Server Long-Running (HARD GUARD, bukan cuma imbauan prompt)
                                    # Sebelumnya fungsi is_long_running_server() sudah ada tapi
                                    # tidak pernah dipanggil — LLM bisa saja mengabaikan larangan
                                    # di system prompt. Sekarang ditolak paksa di level kode,
                                    # SEBELUM subprocess.Popen dipanggil, supaya tidak pernah ada
                                    # proses server yang benar-benar spawn dan membengkak resource.
                                    if is_long_running_server(cmd_to_run):
                                        result = (
                                            f"🚫 [DITOLAK SISTEM]: Perintah '{cmd_to_run}' terdeteksi sebagai "
                                            f"server/proses yang berjalan selamanya (dev server, watch mode, dll). "
                                            f"Perintah ini TIDAK dijalankan untuk mencegah proses menggantung dan "
                                            f"menghabiskan resource sistem.\n\n"
                                            f"[SYSTEM HINT]: Jika perlu verifikasi bahwa server bisa jalan, gunakan "
                                            f"cek cepat non-blocking (misal: cek port kosong, `--version`, atau "
                                            f"compile-check saja seperti `python -m py_compile`/`npm run build`). "
                                            f"Jika user memang minta menjalankan server, beri tahu user untuk "
                                            f"menjalankannya sendiri secara manual di terminal mereka."
                                        )
                                        yield f"data: {json.dumps({'type': 'status', 'text': '🚫 Perintah server ditolak (mencegah proses menggantung)'})}\n\n"

                                    else:
                                        # 2. JALANKAN DENGAN MEKANISME HEARTBEAT
                                        loop = asyncio.get_event_loop()
                                        # >>> PATCH ORION-9 (bug nyata, B023):
                                        # lambda menangkap `cmd_to_run` BY REFERENCE.
                                        # Kalau executor baru menjalankannya setelah loop
                                        # berpindah ke tool_call berikutnya, yang dieksekusi
                                        # adalah perintah yang SALAH. Diikat eksplisit
                                        # sebagai default argument supaya nilainya beku.
                                        future = loop.run_in_executor(
                                            None,
                                            lambda _cmd=cmd_to_run: tool_executor.run_terminal(
                                                command=_cmd, timeout=60
                                            ),
                                        )

                                        # 3. HEARTBEAT LOOP: Kirim sinyal ke frontend setiap 10 detik
                                        while not future.done():
                                            yield f"data: {json.dumps({'type': 'status', 'text': '⏳ Proses masih berjalan, harap tunggu... (Heartbeat)'})}\n\n"
                                            await asyncio.sleep(10)

                                        # 4. Ambil hasil setelah proses benar-benar selesai
                                        result = future.result()
                                        yield f"data: {json.dumps({'type': 'status', 'text': '✅ Perintah terminal selesai'})}\n\n"

                                except (RuntimeError, asyncio.CancelledError):
                                    # Ini tertangkap jika VS Code menutup chat/refresh di tengah proses
                                    print(
                                        "[ORION] Koneksi frontend terputus saat eksekusi tool. Menghentikan proses dengan aman."
                                    )
                                    return  # Hentikan generator sepenuhnya, jangan lanjut ke tool_results

                                except Exception as e:
                                    # Menangkap error lain agar result tetap terisi dan bisa dilaporkan ke LLM
                                    result = f"Error eksekusi terminal: {e!s}"
                                    print(f"[ORION TOOL ERROR] {e}")
                                    import traceback

                                    traceback.print_exc()

                            # >>> PATCH ORION-14 (BUG NYATA) ======================
                            # Tiga tool ini DIDEKLARASIKAN di skema TOOLS dan
                            # method-nya ADA di tool_executor.py, tapi tidak pernah
                            # punya cabang dispatch -- jadi setiap kali model
                            # memanggilnya, yang kembali adalah "Tool tidak dikenal."
                            #
                            # Ironisnya, system prompt baris ~956 MEWAJIBKAN model
                            # memakai web_search untuk error yang tidak dikenal.
                            # Model patuh -> tool mati -> model bingung -> kadang
                            # menyerah tanpa laporan. Ini salah satu penyebab
                            # "berhenti diam-diam" yang belum terdeteksi sebelumnya.
                            # ==========================================================
                            elif func_name == "web_search":
                                query = func_args.get("query", "")
                                yield f"data: {json.dumps({'type': 'status', 'text': f'🌐 Mencari di web: {query[:60]}'})}\n\n"
                                result = tool_executor.web_search(**func_args)
                                yield f"data: {json.dumps({'type': 'status', 'text': '✅ Pencarian web selesai'})}\n\n"

                            elif func_name == "list_backups":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Daftar backup: ' + str(func_args)})}\n\n"
                                result = tool_executor.list_backups(**func_args)

                            elif func_name == "restore_backup":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Memulihkan: ' + str(func_args)})}\n\n"
                                result = tool_executor.restore_backup(**func_args)

                            elif func_name == "delete_file":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Menghapus: ' + str(func_args)})}\n\n"
                                result = tool_executor.delete_file(**func_args)

                            elif func_name == "move_file":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Memindah: ' + str(func_args)})}\n\n"
                                result = tool_executor.move_file(**func_args)

                            elif func_name == "git_commit":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Commit checkpoint: ' + str(func_args)})}\n\n"
                                result = tool_executor.git_commit(**func_args)

                            elif func_name == "run_tests":
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Menjalankan test suite...'})}\n\n"
                                result = tool_executor.run_tests(**func_args)

                            elif func_name == "verify_code":
                                _vc = str(func_args.get("file_path", ""))
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Memeriksa: ' + _vc})}\n\n"
                                result = tool_executor.verify_code(**func_args)

                            elif func_name == "verify_html":
                                _vf = str(func_args.get("file_path", ""))
                                yield f"data: {json.dumps({'type': 'status', 'text': 'Memeriksa: ' + _vf})}\n\n"
                                result = tool_executor.verify_html(**func_args)

                            elif func_name == "web_fetch":
                                url = func_args.get("url", "")
                                yield f"data: {json.dumps({'type': 'status', 'text': f'📄 Membaca halaman: {str(url)[:70]}'})}\n\n"
                                result = tool_executor.web_fetch(**func_args)
                                yield f"data: {json.dumps({'type': 'status', 'text': '✅ Halaman selesai dibaca'})}\n\n"

                            elif func_name == "git_status":
                                yield f"data: {json.dumps({'type': 'status', 'text': '🔀 Memeriksa git status...'})}\n\n"
                                result = tool_executor.git_status()

                            elif func_name == "git_diff":
                                yield f"data: {json.dumps({'type': 'status', 'text': '🔀 Memeriksa git diff...'})}\n\n"
                                result = tool_executor.git_diff(**func_args)

                            else:
                                # >>> PATCH ORION-14b: pesan yang bisa ditindaklanjuti.
                                # "Tool tidak dikenal." saja membuat model menebak-nebak.
                                # Sekarang model diberi daftar tool yang benar-benar jalan.
                                result = (
                                    f"Error: tool '{func_name}' TIDAK TERSEDIA. "
                                    f"Tool yang benar-benar bisa dipakai: {sorted(_NAMA_TOOL_AKTIF)}. "
                                    "Gunakan salah satu dari itu, atau selesaikan dengan cara lain. "
                                    "JANGAN memanggil tool yang sama lagi."
                                )
                                logger.error("[TOOL MATI] Model memanggil %r yang tidak punya dispatch.", func_name)

                        except (RuntimeError, asyncio.CancelledError):
                            # CRITICAL FIX: Mencegah crash/terminate jika VS Code (frontend) memutus koneksi
                            print(
                                "[ORION] Koneksi frontend terputus saat eksekusi tool. Menghentikan proses dengan aman (Graceful Shutdown)."
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
                            res_str = "Berhasil dijalankan, tidak ada output."

                        # >>> SMART TRUNCATION <<<
                        if len(res_str) > 15000:
                            res_str = res_str[:15000] + (
                                "\n\n[⚠️ PERINGATAN SISTEM: Output terlalu panjang dan telah dipotong.]"
                                "\n[JANGAN gunakan 'read_file' lagi untuk file ini.]"
                                "\n[CARA BENAR: Gunakan 'search_code' untuk menemukan kata kunci/baris, "
                                "lalu gunakan 'read_file_range' dengan start_line dan end_line yang spesifik.]"
                            )

                        tool_results.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": func_name,
                                "content": res_str,
                            }
                        )

                        # >>> ANTI-LOOP CHECK (DIPERBAIKI) <<<
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

                        # Batasi hanya menyimpan 3 aksi terakhir
                        if len(last_tool_calls) > 3:
                            last_tool_calls.pop(0)

                        # Jika 3 aksi terakhir adalah run_terminal/search_code dll, dan SEMUANYA GAGAL
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
                                    f"[ORION ANTI-LOOP] Terdeteksi {func_name} gagal 3x berturut-turut. Memaksa berhenti."
                                )
                                # >>> PERBAIKAN KRITIS #3: JANGAN append ke variabel
                                # 'messages' lokal (dead code - tidak pernah dikirim
                                # ke LLM karena api_messages selalu dibangun ulang
                                # dari context_manager tiap iterasi). Tunda notice
                                # ini sampai assistant_msg + tool_results tersimpan,
                                # supaya urutan pesan tetap valid. <<<
                                pending_system_notices.append(
                                    f"[SYSTEM EMERGENCY STOP]: Perintah '{func_name}' telah gagal 3 kali berturut-turut. STOP mencoba variasi yang sama. Baca baik-baik pesan error di output sebelumnya, laporkan inti masalahnya ke user, dan minta instruksi baru. JANGAN mencoba menjalankan perintah ini lagi."
                                )
                                # >>> PATCH ORION-35: RISET OTOMATIS SAAT MENTOK ====
                                # Sebelumnya momen anti-loop ini berarti "menyerah dan
                                # lapor user". Sekarang backend melakukan pencarian web
                                # SENDIRI atas error terakhir dan menyuntikkan temuannya,
                                # sehingga Orion mendapat satu kesempatan pendekatan
                                # berbeda yang berbasis temuan, sebelum benar-benar
                                # menyerah. Ini jawaban kode untuk "kalau pengetahuannya
                                # mentok, dia browsing": tidak mengandalkan model ingat
                                # untuk mencari, karena justru saat mentok ia paling
                                # mungkin lupa mencari.
                                # ==================================================
                                try:
                                    _kueri_riset = research_rules.rancang_kueri_riset(
                                        func_name, res_str)
                                    _temuan_riset = tool_executor.web_search(
                                        _kueri_riset, max_results=3)
                                    logger.info("[RISET OTOMATIS] kueri=%s", _kueri_riset)
                                except Exception as _err_riset:  # noqa: BLE001
                                    _temuan_riset = f"(riset otomatis gagal: {_err_riset})"
                                pending_system_notices.append(
                                    "[SYSTEM - RISET OTOMATIS]: Sebelum menyerah dan lapor "
                                    "user, bacalah temuan web berikut lalu coba SATU pendekatan "
                                    "yang BERBEDA dari yang sudah gagal, berbasis temuan ini:\n"
                                    + str(_temuan_riset)[:2500]
                                    + "\n\nKalau temuan ini tidak memadai, BARU laporkan ke user: "
                                    "apa yang sudah dicoba, apa temuannya, dan apa rekomendasimu."
                                )
                                # >>> AKHIR PATCH ORION-35 =========================
                                last_tool_calls = []  # Reset setelah warning
                        # >>> AKHIR ANTI-LOOP CHECK <<<

                    # SIMPAN KE MEMORI SECARA REAL-TIME
                    context_manager.add_message(
                        "assistant",
                        msg.content,
                        tool_calls=assistant_msg["tool_calls"],
                        reasoning_content=reasoning_content,
                    )
                    for tr in tool_results:
                        context_manager.add_message(
                            "tool", tr["content"], tool_call_id=tr["tool_call_id"]
                        )
                        # Auto-checkpoint system message dihapus untuk menghemat token.
                        # State file sudah tercatat di tool_calls dan tool_results.

                    # >>> Notice darurat (anti-loop) baru ditambahkan SEKARANG,
                    # setelah assistant+tool tersimpan dengan urutan yang benar. <<<
                    for notice in pending_system_notices:
                        context_manager.add_message("user", notice)

                    # Self-Correction Injection (Agresif tapi Terkontrol)
                    has_terminal_error = False
                    error_snippet = ""
                    for tr in tool_results:
                        if tr["name"] == "run_terminal" and is_terminal_failure(
                            tr["content"]
                        ):
                            has_terminal_error = True
                            # 🛡️ PENGAMAN: Ambil hanya 3 baris pertama error untuk menghemat token input
                            error_snippet = "\n".join(
                                tr["content"].split("\n")[:3]
                            ).strip()
                            break

                    if has_terminal_error:
                        context_manager.add_message(
                            "user",
                            f"[SYSTEM AUTO-CORRECTION MODE]: Perintah terminal gagal.\nError Snippet: {error_snippet}\n\nTUGAS: JANGAN bertanya ke user. Analisis error ini. JANGAN gunakan read_file penuh. Gunakan read_file_range jika perlu melihat konteks, lalu gunakan edit_file untuk memperbaikinya SEKARANG JUGA. Lakukan maksimal 2 iterasi perbaikan otomatis.",
                        )
                        print(
                            "[ORION SELF-CORRECT] Error terdeteksi. Memaksa mode perbaikan otomatis (hemat token)."
                        )

                else:
                    # >>> FIX: model fallback (OpenRouter/Cloudflare) kadang mengembalikan
                    # literal '\n' dua-karakter di teks jawaban biasa (bukan cuma di dalam
                    # argumen JSON tool call). fix_over_escaped_text() sudah dipakai untuk
                    # path ask_user, tapi path teks biasa ini terlewat — makanya '\n' literal
                    # masih muncul di layar user untuk balasan normal seperti contoh di atas.
                    content = fix_over_escaped_text(msg.content or "")
                    reasoning_content = getattr(msg, "reasoning_content", None)

                    # Deteksi apakah ini adalah "task completion" (Orion selesai mengerjakan task)
                    # >>> PATCH ORION-34: logika klaim dipindah ke guard_rules supaya bisa di-test.
                    is_task_complete = guard_rules.kata_klaim(content)

                    if len(content.strip()) < 50 and not is_task_complete:
                        print(
                            "[ORION] Orion mengeluarkan teks pendek tanpa tool. Melanjutkan iterasi..."
                        )
                        context_manager.add_message(
                            "assistant", content, reasoning_content=reasoning_content
                        )

                        current_iter = _ + 1
                        if current_iter >= max_iter:
                            pause_msg = f"\n\n[SYSTEM PAUSE]: Saya telah mencapai batas iterasi ({max_iter}) untuk sesi ini demi keamanan token. Namun, saya masih mengingat semua langkah yang telah saya lakukan. Silakan ketik **'lanjutkan'** atau **'continue'** agar saya dapat menyelesaikan tugas ini."
                            yield f"data: {json.dumps({'type': 'token', 'text': pause_msg})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            print(
                                f"[ORION PAUSE] Mencapai batas iterasi {max_iter}. Menunggu instruksi user untuk lanjut."
                            )
                            break

                        continue
                    else:
                        # >>> TASK COMPLETION DETECTED <<<
                        # Orion selesai mengerjakan task, berikan checkpoint report dan pause
                        # >>> FIX KRITIS: sebelumnya syaratnya cuma `is_task_complete and _ > 0`.
                        # Kata kunci seperti "berhasil"/"selesai" SANGAT umum muncul di teks
                        # audit/laporan biasa meski TIDAK ADA tool edit_file/write_file yang
                        # benar-benar jalan — akibatnya Orion bisa "mengaku selesai" padahal
                        # tidak mengerjakan apa pun (dan user melihat siklus audit berulang
                        # tanpa progres nyata). Sekarang checkpoint HANYA boleh menutup task
                        # kalau ada bukti nyata: minimal 1 file benar-benar berhasil ditulis/
                        # diedit pada TURN ini (files_changed_this_turn).
                        if is_task_complete and _ > 0 and files_changed_this_turn:
                            # >>> PATCH ORION-1 (KRITIS) ============================
                            # SEBELUMNYA: begitu model menulis kata "selesai"/"berhasil"
                            # DAN ada file yang berubah, loop langsung `break`. Kalau user
                            # meminta 3 hal, Orion menyelesaikan yang pertama lalu berhenti
                            # -- persis keluhan "hanya mengerjakan tugas pertama".
                            # Kode lama sudah MENDETEKSI todo yang tersisa (pending_warning)
                            # tapi hanya menampilkannya sebagai peringatan, lalu tetap stop.
                            #
                            # SEKARANG: kalau masih ada todo pending/in_progress, loop TIDAK
                            # berhenti. Disuntikkan pesan sistem yang menunjuk item
                            # berikutnya, lalu `continue`. Berhenti hanya terjadi kalau
                            # semua todo selesai ATAU batas nudge tercapai (degradasi anggun
                            # ke perilaku lama: checkpoint + peringatan).
                            # ==========================================================
                            _pending_items = []
                            if todo_manager and todo_manager.has_pending_or_in_progress():
                                _pending_items = [
                                    t for t in todo_manager.get_todos()
                                    if t.get("status") in ("pending", "in_progress")
                                ]

                            if _pending_items and _nudge_count < MAX_TODO_NUDGE:
                                _next_todo = _pending_items[0]
                                _nudge_count += 1
                                _next_id = str(_next_todo.get("id", "?"))
                                _next_text = str(_next_todo.get("text", ""))[:300]
                                _sisa = len(_pending_items)

                                # simpan kemajuan turn ini supaya tidak hilang dari konteks
                                context_manager.add_message(
                                    "assistant", content,
                                    reasoning_content=reasoning_content,
                                )
                                context_manager.add_message(
                                    "user",
                                    "[SYSTEM - LANJUTKAN]: Bagian pertama sudah dikerjakan, tapi "
                                    f"task user BELUM selesai. Masih ada {_sisa} item di todo list.\n"
                                    f"Item berikutnya: `[{_next_id}]` {_next_text}\n\n"
                                    "INSTRUKSI: JANGAN menyimpulkan task selesai dan JANGAN bertanya "
                                    f"ke user. Kerjakan item `[{_next_id}]` SEKARANG. Setelah benar-benar "
                                    "selesai, panggil tool `update_todos` untuk menandainya 'completed', "
                                    "lalu lanjut ke item berikutnya.",
                                )
                                yield f"data: {json.dumps({'type': 'status', 'text': f'Melanjutkan ke todo [{_next_id}]...'})}\n\n"
                                yield f"data: {json.dumps({'type': 'todo_update', 'markdown': todo_manager.render_markdown(), 'todos': todo_manager.get_todos()})}\n\n"
                                logger.info("[ORION NUDGE %d/%d] Masih ada %d todo. Lanjut ke [%s].",
                                            _nudge_count, MAX_TODO_NUDGE, _sisa, _next_id)
                                continue
                            # >>> AKHIR PATCH ORION-1 ================================

                            checkpoint_report = (
                                "\n\n---\n\n"
                                "## ✅ TASK COMPLETED - CHECKPOINT REPORT\n\n"
                                "**Status:** Task selesai dikerjakan\n\n"
                                "**File yang diubah:**\n"
                            )

                            # Pakai daftar file yang benar2 diubah PADA TURN INI SAJA,
                            # bukan scan seluruh histori sesi (yang bisa menampilkan
                            # perubahan lama dari task sebelumnya seolah baru terjadi).
                            if files_changed_this_turn:
                                checkpoint_report += "\n".join(
                                    files_changed_this_turn[-10:]
                                )  # Max 10 file terakhir
                            else:
                                checkpoint_report += "- Tidak ada file yang diubah"

                            # >>> FIX KRITIS: cegah item todo yang diam-diam "hilang".
                            # Sebelumnya, kalau user minta 3 fitur tapi Orion cuma
                            # mengerjakan 2, checkpoint tetap bilang "task selesai"
                            # tanpa pernah menyebut item ke-3 yang terlewat. Sekarang,
                            # kalau ada todo yang masih pending/in_progress, checkpoint
                            # WAJIB menampilkannya secara eksplisit -- supaya user
                            # (bukan cuma Orion) yang menentukan apakah itu memang
                            # sudah cukup atau harus dilanjutkan.
                            pending_warning = ""
                            if (
                                todo_manager
                                and todo_manager.has_pending_or_in_progress()
                            ):
                                pending_items = [
                                    t
                                    for t in todo_manager.get_todos()
                                    if t["status"] in ("pending", "in_progress")
                                ]
                                pending_list = "\n".join(
                                    f"- ⚠️ `[{t['id']}]` {t['text']} (status: {t['status']})"
                                    for t in pending_items
                                )
                                pending_warning = (
                                    f"\n\n**⚠️ PERHATIAN: Masih ada item todo yang BELUM selesai:**\n"
                                    f"{pending_list}\n\n"
                                    f"Task ini TIDAK 100% tuntas -- item di atas belum dikerjakan."
                                )

                            checkpoint_report += pending_warning

                            checkpoint_report += (
                                "\n\n**Rencana selanjutnya:**\n"
                                "Silakan review perubahan di atas. Apakah ada yang perlu disesuaikan, atau saya lanjutkan ke task berikutnya?\n\n"
                                "---\n"
                            )

                            # Tampilkan checkpoint report di chat
                            yield f"data: {json.dumps({'type': 'token', 'text': strip_thinking_blocks(content) + checkpoint_report})}\n\n"

                            # Simpan ke context
                            context_manager.add_message(
                                "assistant",
                                content + checkpoint_report,
                                reasoning_content=reasoning_content,
                            )

                            # PAUSE: Tunggu respons user
                            yield f"data: {json.dumps({'type': 'ask_user', 'question': 'Task selesai. Apakah ada yang perlu disesuaikan, atau saya lanjutkan ke task berikutnya?'})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            print(
                                "[ORION TASK COMPLETE] Task selesai. Menunggu respons user untuk lanjut."
                            )
                            break
                        # >>> PATCH ORION-17b (GERBANG VERIFIKASI KLAIM) ==========
                        # GEJALA YANG DIPERBAIKI: Orion melaporkan "fitur X telah
                        # berhasil diintegrasikan" padahal TIDAK ADA satu pun file
                        # yang diubah pada giliran ini. User memeriksa file dan
                        # menemukan tidak ada perubahan; Orion lalu meng-audit ulang
                        # dan mengulang klaim yang sama.
                        #
                        # PENYEBAB: `is_task_complete` dideteksi dari pencocokan kata
                        # ("selesai", "berhasil", "sudah saya"). Model bisa memakai
                        # kata-kata itu saat MENGANALISIS tanpa mengerjakan apa pun,
                        # dan klaim itu sering berasal dari .orion/master_context.md
                        # yang ditulis sesi sebelumnya (lihat PATCH ORION-16).
                        #
                        # PERBAIKAN: kalau model mengklaim selesai TAPI nol file
                        # berubah pada giliran ini, klaimnya DITOLAK. Kita suntik
                        # pesan yang memaksa verifikasi lewat search_code/read_file
                        # sebelum boleh menyimpulkan apa pun. Dibatasi
                        # MAX_VERIFIKASI kali supaya tidak jadi loop baru.
                        # ==========================================================
                        elif (
                            guard_rules.klaim_perlu_ditantang(
                                is_task_complete,
                                bool(files_changed_this_turn),
                                _verifikasi_count,
                                MAX_VERIFIKASI,
                            )
                        ):
                            _verifikasi_count += 1
                            logger.warning(
                                "[KLAIM TANPA BUKTI] Orion mengaku selesai tapi 0 file berubah "
                                "(verifikasi %d/%d). Menantang klaimnya.",
                                _verifikasi_count, MAX_VERIFIKASI,
                            )
                            context_manager.add_message(
                                "assistant", content, reasoning_content=reasoning_content
                            )
                            context_manager.add_message(
                                "user",
                                "[SYSTEM - KLAIM ANDA DITOLAK]: Anda melaporkan pekerjaan selesai, "
                                "tapi pada giliran ini TIDAK ADA satu pun file yang berhasil Anda "
                                "ubah atau buat. Laporan Anda tidak bisa diterima sebagai kebenaran.\n\n"
                                "WAJIB lakukan sekarang, berurutan:\n"
                                "0. Kalau klaim Anda menyangkut file .html/.js, JALANKAN tool `verify_html` "
                                "pada file itu SEKARANG dan kutip hasilnya apa adanya, termasuk kalau "
                                "hasilnya berisi error. Jangan menyebut 'tervalidasi' tanpa output tool ini.\n"
                                "1. Sebutkan nama file + nomor baris yang Anda klaim sudah berisi fitur itu.\n"
                                "2. Buktikan dengan `search_code` atau `read_file_range` pada file tersebut.\n"
                                "3. Kalau buktinya ADA: kutip barisnya, lalu laporkan bahwa pekerjaan "
                                "ini ternyata sudah dikerjakan sebelumnya (bukan oleh Anda sekarang).\n"
                                "4. Kalau buktinya TIDAK ADA: katakan terus terang 'fitur ini belum ada', "
                                "lalu BUAT sekarang dengan `edit_file`/`write_file`.\n\n"
                                "JANGAN mengulang klaim tanpa bukti. JANGAN meng-audit ulang untuk "
                                "membenarkan diri. Jangan bertanya ke user sebelum langkah 1-2 selesai.",
                            )
                            yield f"data: {json.dumps({'type': 'status', 'text': '🔍 Memverifikasi klaim selesai (0 file berubah)...'})}\n\n"
                            continue
                        # >>> AKHIR PATCH ORION-17b

                        else:
                            # Bukan task completion, hanya respons normal
                            yield f"data: {json.dumps({'type': 'token', 'text': strip_thinking_blocks(content)})}\n\n"
                            context_manager.add_message(
                                "assistant",
                                content,
                                reasoning_content=reasoning_content,
                            )
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            break
                        # >>> AKHIR TASK COMPLETION DETECTED <<<

        except Exception as e:
            error_msg = str(e)
            # >>> PATCH ORION-2: pakai logger, bukan print (stdout = pipe ke extension)
            logger.exception("[ORION EXCEPTION] agent loop gagal")
            yield f"data: {json.dumps({'type': 'error', 'text': error_msg})}\n\n"
            # >>> PATCH ORION-2 (KRITIS): WAJIB kirim 'done' setelah 'error'.
            # Sebelumnya hanya 'error' yang dikirim, sehingga webview tidak pernah
            # mendapat sinyal selesai -> UI menggantung dan user melihat assistant
            # "berhenti diam-diam".
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # >>> PATCH ORION-28 ================================================
    # Audit menemukan SEPULUH jalur keluar di aliran inti yang mengakhiri
    # stream TANPA mengirim event 'done' (ask_user, menunggu konfirmasi
    # terminal, emergency stop, batas iterasi, dll). Akibatnya extension
    # membaca penutupan yang SAH itu sebagai crash dan menampilkan
    # "Koneksi ke backend terputus" -- alarm palsu.
    #
    # Alih-alih menambal sepuluh tempat (dan tempat baru di masa depan),
    # satu wrapper menjamin: berapa pun jalurnya, kalau aliran berakhir
    # tanpa 'done', wrapper yang mengirimkannya. Crash sungguhan (proses
    # mati) tetap tidak menghasilkan done, sehingga ORION-12 di extension
    # tetap bisa membedakan putus beneran dari berakhir normal.
    # ==================================================================
    async def generate_stream():
        # >>> PATCH ORION-36: logika "jamin ada done" dipindah ke stream_utils
        # supaya bisa di-unit-test tanpa menjalankan FastAPI (test_stream_utils).
        async for potongan in stream_utils.jamin_done(_aliran_inti(), logger=logger):
            yield potongan

    return StreamingResponse(generate_stream(), media_type="text/event-stream")


@app.post("/clear-chat")
async def clear_chat():
    context_manager.clear_history(persist=True)
    return {"success": True, "message": "Riwayat chat dihapus."}


class TerminalRequest(BaseModel):
    command: str


@app.post("/execute-terminal")
async def execute_approved_terminal(request: TerminalRequest):
    """Endpoint khusus untuk mengeksekusi perintah yang sudah disetujui user."""
    if tool_executor is None:
        return {"success": False, "output": "Tool executor not initialized."}

    output = tool_executor.run_terminal(command=request.command)

    # >>> PERBAIKAN KRITIS: Update placeholder dengan hasil asli <<<
    history = context_manager.get_history()
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "tool" and "[MENUNGGU KONFIRMASI]" in msg.get(
            "content", ""
        ):
            history[i]["content"] = output
            print(
                f"[ORION] Perintah berbahaya dieksekusi. Tool call ID {msg.get('tool_call_id')} di-update."
            )
            if hasattr(context_manager, "save_history"):
                context_manager.save_history()
            break

    return {"success": True, "output": output}


@app.get("/performance")
async def get_performance():
    """Membaca dan menghitung statistik dari performance_log.jsonl"""
    if tool_executor is None:
        return {"error": "Project belum di-set."}

    log_file = os.path.join(
        tool_executor.project_path, ".orion", "performance_log.jsonl"
    )

    if not os.path.exists(log_file):
        return {
            "summary": {
                "total_requests": 0,
                "total_cost_usd": 0,
                "avg_response_time_ms": 0,
            },
            "recent_logs": [],
        }

    logs = []
    total_cost = 0.0
    total_duration = 0.0
    total_input_tokens = 0  # <<< TAMBAHKAN INI
    total_output_tokens = 0
    success_count = 0
    error_count = 0

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        logs.append(entry)
                        total_cost += entry.get("estimated_cost_usd", 0)
                        total_duration += entry.get("duration_ms", 0)
                        total_input_tokens += entry.get("input_tokens", 0)
                        total_output_tokens += entry.get("output_tokens", 0)
                        if entry.get("status") == "success":
                            success_count += 1
                        else:
                            error_count += 1
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        # >>> PATCH ORION-3: `except: pass` menelan SEMUA error termasuk
                        # KeyboardInterrupt/SystemExit, dan menyembunyikan baris log rusak.
                        continue
    except OSError as e:
        return {"error": f"Gagal membaca log: {e!s}"}

    total_requests = len(logs)
    avg_duration = total_duration / total_requests if total_requests > 0 else 0

    return {
        "summary": {
            "total_requests": total_requests,
            "total_input_tokens": sum(
                l.get("input_tokens", 0) for l in logs
            ),  # <<< TAMBAHKAN
            "total_output_tokens": sum(
                l.get("output_tokens", 0) for l in logs
            ),  # <<< TAMBAHKAN
            "total_cost_usd": round(total_cost, 6),
            "avg_response_time_ms": round(avg_duration, 2),
            "success_count": success_count,
            "error_count": error_count,
        },
        "recent_logs": logs[-10:],
    }
