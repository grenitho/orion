"""
todo_manager.py

Modul independen untuk melacak daftar tugas (todo list) eksplisit per project,
supaya progres task besar (multi-langkah) terlihat jelas oleh user -- mirip
pendekatan "todo tracking" pada agentic coding assistant modern.

Didesain 100% ADDITIVE: tidak menyentuh context_manager, tool_executor, atau
alur loop utama di main.py. Model BOLEH memanggil tool `update_todos` kapan
saja untuk task multi-langkah, atau TIDAK memanggilnya sama sekali untuk task
sederhana -- keduanya valid, tidak ada perilaku lama yang berubah.
"""

import json
import os

VALID_STATUSES = {"pending", "in_progress", "completed"}


class TodoManager:
    def __init__(self, project_path: str = None):
        self.project_path = None
        self.todo_file = None
        self.todos = []  # list of dict: {"id": str, "text": str, "status": str}
        if project_path:
            self.set_project_path(project_path)

    def set_project_path(self, project_path: str):
        """Dipanggil dari titik yang sama dengan context_manager.set_project_path
        (lihat integrasi di main.py: /set-project dan auto_restore_project)."""
        self.project_path = project_path
        orion_dir = os.path.join(project_path, ".orion")
        os.makedirs(orion_dir, exist_ok=True)
        self.todo_file = os.path.join(orion_dir, "todo_list.json")
        self.load()

    def load(self):
        if not self.todo_file or not os.path.exists(self.todo_file):
            self.todos = []
            return
        try:
            with open(self.todo_file, "r", encoding="utf-8") as f:
                self.todos = json.load(f)
        except Exception as e:
            print(f"[TODO_MANAGER WARNING] Gagal load todo list: {e}")
            self.todos = []

    def save(self):
        if not self.todo_file:
            return
        try:
            os.makedirs(os.path.dirname(self.todo_file), exist_ok=True)
            tmp = self.todo_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.todos, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.todo_file)
        except Exception as e:
            print(f"[TODO_MANAGER WARNING] Gagal simpan todo list: {e}")

    def set_todos(self, todos: list) -> str:
        """
        Timpa seluruh todo list dengan list baru dari model.
        Format tiap item yang diharapkan dari argumen tool call:
            {"id": "1", "text": "Tambahkan slider brightness", "status": "pending"}
        Field "id" opsional (auto-generate dari index kalau kosong).
        Field "status" opsional, default "pending".
        Item yang tidak valid (bukan dict / text kosong) dilewati diam-diam,
        supaya satu item rusak tidak menggagalkan seluruh list.
        """
        cleaned = []
        for i, item in enumerate(todos or []):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            status = item.get("status", "pending")
            if status not in VALID_STATUSES:
                status = "pending"
            cleaned.append(
                {
                    "id": str(item.get("id", i + 1)),
                    "text": text,
                    "status": status,
                }
            )
        self.todos = cleaned
        self.save()
        return self.render_markdown()

    def update_status(self, todo_id: str, status: str) -> str:
        """Update status 1 item tanpa perlu kirim ulang seluruh list."""
        if status not in VALID_STATUSES:
            return (
                f"[TODO_MANAGER ERROR] Status '{status}' tidak valid. "
                f"Gunakan salah satu: {', '.join(sorted(VALID_STATUSES))}"
            )
        found = False
        for item in self.todos:
            if item["id"] == str(todo_id):
                item["status"] = status
                found = True
                break
        if not found:
            return f"[TODO_MANAGER ERROR] Todo dengan id '{todo_id}' tidak ditemukan."
        self.save()
        return self.render_markdown()

    def get_todos(self) -> list:
        return self.todos

    def render_markdown(self) -> str:
        """Representasi ringkas untuk ditampilkan ke user di chat."""
        if not self.todos:
            return "_(Todo list kosong)_"
        icon_map = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}
        lines = []
        for item in self.todos:
            icon = icon_map.get(item["status"], "⬜")
            lines.append(f"{icon} `[{item['id']}]` {item['text']}")
        return "\n".join(lines)

    def has_pending_or_in_progress(self) -> bool:
        return any(t["status"] in ("pending", "in_progress") for t in self.todos)

    def clear(self):
        self.todos = []
        self.save()