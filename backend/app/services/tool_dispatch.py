"""
tool_dispatch.py — tabel dispatch untuk tool "sederhana" (Fase B pemecahan main.py)
====================================================================================
Sebelum modul ini ada, 22 tool ditangani oleh rantai `elif func_name == ...`
sepanjang ratusan baris di dalam generator stream. Sebagian besar cabang hanya
berbentuk: kirim status -> panggil method executor -> kirim status selesai.
Modul ini memindahkan 14 cabang murni itu ke sebuah TABEL, sehingga:

  * main.py tinggal memuat cabang yang benar-benar rumit dan interaktif
    (ask_user, update_todos, write_file, edit_file, run_terminal,
    read_file/read_file_range dengan guard redundansinya, git_diff);
  * menambah tool sederhana kelak berarti menambah satu baris tabel,
    bukan menyentuh generator stream;
  * label status UI hidup di satu tempat yang bisa dibaca dan diuji.

Tool yang TIDAK boleh masuk tabel ini: yang punya guard, konfirmasi, heartbeat,
atau efek alur kontrol. Penjaga itu ada di test_tool_dispatch.py.
"""

from __future__ import annotations

from typing import Callable, Optional

Label = Callable[[dict], str]

# nama tool -> (label_mulai(args), label_selesai(args) | None)
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

# nama tool == nama method di ToolExecutor untuk ke-14 entri ini.
# Kalau suatu hari berbeda, cukup ubah kamus ini.
METHOD: dict[str, str] = {nama: nama for nama in TABLE}

# Tool yang HARUS tetap ditangani main.py karena punya guard/interaksi.
# Dicantumkan eksplisit supaya keputusan ini terbaca, bukan tersirat.
WAJIB_DI_MAIN = {
    "ask_user",        # menghentikan stream, menunggu jawaban user
    "update_todos",    # menyentuh todo_manager + event todo_update
    "read_file",       # guard redundansi analyzed_files
    "read_file_range", # guard redundansi analyzed_files
    "write_file",      # permission_manager + post_edit_verifier
    "edit_file",       # permission_manager + post_edit_verifier
    "run_terminal",    # konfirmasi modal + heartbeat + kill tree
    "git_diff",        # perlakuan khusus output besar
}


def tool_sederhana(nama: str) -> bool:
    return nama in TABLE


def label(nama: str, args: dict) -> tuple[str, Optional[str]]:
    mulai, selesai = TABLE[nama]
    return mulai(args or {}), (selesai(args or {}) if selesai else None)


def jalankan(nama: str, executor, args: dict):
    method = getattr(executor, METHOD[nama])
    return method(**(args or {}))
