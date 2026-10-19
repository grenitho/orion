"""
todo_manager.py

An independent module tracking an explicit per-project todo list, so the
progress of large (multi-step) tasks is clearly visible to the user --
similar to the "todo tracking" approach of modern agentic coding assistants.

Designed to be 100% ADDITIVE: it does not touch context_manager,
tool_executor, or the main loop flow in main.py. The model MAY call the
`update_todos` tool at any time for multi-step tasks, or NOT call it at all
for simple tasks -- both are valid; no old behaviour changed.
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
        """Called from the same places as context_manager.set_project_path
        (see the integration in main.py: /set-project and auto_restore_project)."""
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
            print(f"[TODO_MANAGER WARNING] Failed to load todo list: {e}")
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
            print(f"[TODO_MANAGER WARNING] Failed to save todo list: {e}")

    def set_todos(self, todos: list) -> str:
        """
        Overwrite the entire todo list with a new list from the model.
        Expected format of each item in the tool-call arguments:
            {"id": "1", "text": "Add a brightness slider", "status": "pending"}
        The "id" field is optional (auto-generated from the index when empty).
        The "status" field is optional, defaulting to "pending".
        Invalid items (not a dict / empty text) are skipped silently, so one
        broken item cannot fail the whole list.
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