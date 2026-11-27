"""
todo_manager.py

An independent module tracking an explicit per-project / per-session todo list,
so the progress of large (multi-step) tasks is clearly visible to the user.
"""

import json
import os
import uuid

from app.services.session_state import jalur_file_sesi, normalisasi_session_id

VALID_STATUSES = {"pending", "in_progress", "completed"}


class TodoManager:
    def __init__(self, project_path: str = None, session_id: str = "default"):
        self.project_path = None
        self.session_id = normalisasi_session_id(session_id)
        self.todo_file = None
        self.todos = []  # list of dict: {"id": str, "text": str, "status": str}
        if project_path:
            self.set_project_path(project_path, session_id=self.session_id)

    def set_project_path(self, project_path: str, session_id: str | None = None):
        """Bind TodoManager to a project and session (defaults to 'default')."""
        self.project_path = project_path
        if session_id is not None:
            self.session_id = normalisasi_session_id(session_id)
        self.todo_file = jalur_file_sesi(
            project_path, "todo_list.json", self.session_id
        )
        os.makedirs(os.path.dirname(self.todo_file), exist_ok=True)
        self.load()

    def load(self):
        if not self.todo_file or not os.path.exists(self.todo_file):
            self.todos = []
            return
        try:
            with open(self.todo_file, "r", encoding="utf-8") as f:
                self.todos = json.load(f)
        except Exception as e:
            print(f"[TODO_MANAGER WARNING] Failed to load todo list: {e}")
            self.todos = []

    def save(self):
        if not self.todo_file:
            return
        tmp = f"{self.todo_file}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            os.makedirs(os.path.dirname(self.todo_file), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.todos, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.todo_file)
        except Exception as e:
            print(f"[TODO_MANAGER WARNING] Failed to save todo list: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def set_todos(self, todos: list) -> str:
        """Overwrite the entire todo list with a new list from the model."""
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
        """Update the status of 1 item without resending the whole list."""
        if status not in VALID_STATUSES:
            return (
                f"[TODO_MANAGER ERROR] Invalid status '{status}'. "
                f"Use one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        found = False
        for item in self.todos:
            if item["id"] == str(todo_id):
                item["status"] = status
                found = True
                break
        if not found:
            return f"[TODO_MANAGER ERROR] Todo with id '{todo_id}' not found."
        self.save()
        return self.render_markdown()

    def get_todos(self) -> list:
        return self.todos

    def render_markdown(self) -> str:
        """Compact representation shown to the user in chat."""
        if not self.todos:
            return "_(Todo list is empty)_"
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
