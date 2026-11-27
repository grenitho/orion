import json
import os
import shutil
import uuid

from app.services.session_state import jalur_file_sesi, normalisasi_session_id


class ContextManager:
    def __init__(self, project_path: str = None, session_id: str = "default"):
        self.project_path = project_path
        self.session_id = normalisasi_session_id(session_id)
        self.history = []
        self.project_map = ""
        self.history_file = None

        if project_path:
            self.set_project_path(project_path, session_id=self.session_id)

    def set_project_path(self, project_path: str, session_id: str | None = None):
        self.project_path = project_path
        if session_id is not None:
            self.session_id = normalisasi_session_id(session_id)
        self.history_file = jalur_file_sesi(
            project_path, "session_history.json", self.session_id
        )
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        self.load_history()

    def add_message(
        self,
        role: str,
        content: str,
        tool_calls=None,
        tool_call_id=None,
        reasoning_content=None,
    ):
        msg = {"role": role, "content": content}

        if tool_calls is not None:
            msg["tool_calls"] = tool_calls
        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content

        self.history.append(msg)
        self.save_history()

    def get_history(self) -> list:
        return self.history

    def clear_history(self, persist: bool = False):
        """Clear the history in RAM.
        If persist=True, save the change to disk (for /clear-chat).
        If persist=False, leave the file on disk intact (for safety during summarize)."""
        self.history = []
        if persist:
            self.save_history()

    def save_history(self):
        """Atomic write with unique temp suffix to prevent file corruption or Windows lock collisions."""
        if not self.history_file:
            return

        tmp_path = f"{self.history_file}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)

            os.replace(tmp_path, self.history_file)
        except Exception as e:
            print(f"[CONTEXT_MANAGER WARNING] Failed to save history: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def load_history(self):
        """Load history with orphaned tool_calls validation (both directions)."""
        if not self.history_file or not os.path.exists(self.history_file):
            self.history = []
            return

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                loaded_history = json.load(f)

            clean_history = []
            i = 0
            while i < len(loaded_history):
                msg = loaded_history[i]

                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    expected_ids = {tc["id"] for tc in msg["tool_calls"]}
                    found_ids = set()
                    j = i + 1

                    while (
                        j < len(loaded_history)
                        and loaded_history[j].get("role") == "tool"
                    ):
                        found_ids.add(loaded_history[j].get("tool_call_id"))
                        j += 1

                    if expected_ids - found_ids:
                        print(
                            f"[CONTEXT_MANAGER] ⚠️ Discarding assistant message with {len(expected_ids - found_ids)} orphaned tool_calls."
                        )
                        i = j
                        continue

                if msg.get("role") == "tool" and not msg.get("tool_call_id"):
                    i += 1
                    continue

                clean_history.append(msg)
                i += 1

            all_valid_call_ids = set()
            for m in clean_history:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    all_valid_call_ids.update(tc["id"] for tc in m["tool_calls"])

            final_history = []
            dropped_orphan_tools = 0
            for m in clean_history:
                if m.get("role") == "tool":
                    if m.get("tool_call_id") not in all_valid_call_ids:
                        dropped_orphan_tools += 1
                        continue
                final_history.append(m)

            if dropped_orphan_tools:
                print(
                    f"[CONTEXT_MANAGER] ⚠️ Discarding {dropped_orphan_tools} orphaned tool responses (no matching assistant tool_calls)."
                )

            self.history = final_history
            print(f"[CONTEXT_MANAGER] Loaded {len(self.history)} messages from disk.")

        except json.JSONDecodeError as e:
            print(f"[CONTEXT_MANAGER ERROR] History file corrupted: {e}")
            print("[CONTEXT_MANAGER] Trying backup...")
            self._try_load_backup()
        except Exception as e:
            print(f"[CONTEXT_MANAGER ERROR] Failed to load history: {e}")
            self.history = []

    def _try_load_backup(self):
        """Try loading from backup when the main file is corrupted."""
        backup_file = self.history_file + ".backup"
        if os.path.exists(backup_file):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
                print(
                    f"[CONTEXT_MANAGER] ✅ Restored from backup: {len(self.history)} messages."
                )
            except Exception:
                print("[CONTEXT_MANAGER] ❌ Backup also corrupted. Starting fresh.")
                self.history = []
        else:
            print("[CONTEXT_MANAGER] ❌ No backup available. Starting fresh.")
            self.history = []

    def create_backup(self):
        """Create a backup before risky operations (such as summarize)."""
        if not self.history_file or not os.path.exists(self.history_file):
            return

        try:
            backup_file = self.history_file + ".backup"
            shutil.copy2(self.history_file, backup_file)
            print(f"[CONTEXT_MANAGER] Backup created at {backup_file}")
        except Exception as e:
            print(f"[CONTEXT_MANAGER WARNING] Failed to create backup: {e}")

    @staticmethod
    def safe_split_index(history: list, target_index: int) -> int:
        """Find a safe cut point near target_index so assistant(tool_calls) + tool(response) is never split."""
        if target_index <= 0 or target_index >= len(history):
            return target_index

        idx = target_index
        while idx > 0 and history[idx].get("role") == "tool":
            idx -= 1
            if history[idx].get("role") == "assistant" and history[idx].get(
                "tool_calls"
            ):
                break
        return idx

    def get_project_map(self) -> str:
        return self.project_map

    def set_project_map(self, project_map: str):
        self.project_map = project_map
