# backend/app/services/tool_executor.py
import json
import os
import re
import shutil
import signal
import subprocess
from datetime import datetime
import sys


class ToolExecutor:
    # >>> PATCH ORION-27b: list of git operations forbidden through run_terminal.
    # >>> UPDATE-008 hardening: the four network/hang patterns below are
    # deliberately UNANCHORED. The old "^git ..." anchor only caught a bare
    # command, so wrapped or chained forms slipped past this list:
    #   cmd /c git push ..., powershell -Command "git push ...",
    #   cd repo && git push, sh -c 'git clone ...'
    # (those forms still hit the extension's DANGEROUS_PATTERNS approval
    # modal, but the backend rejection is the layer that must stay
    # authoritative — a modal can be clicked away, this cannot.)
    # Accepted trade-off: a harmless command that merely CONTAINS the literal
    # text e.g. "git push" (echoing it into a notes file) is rejected too;
    # the rejection message then tells the agent to leave the action to the
    # user, which is always the safe direction.
    GIT_DIBLOKIR = [
        (r"git\s+init\b", "restructures folders and is proven to hang",
         "No git repo is needed to work. Safe points are available via .orion/backups "
         "and the git_commit tool (when a repo already exists)."),
        (r"git\s+push\b", "sends code to an external server without review first",
         "Ask the user to run push themselves after reviewing the changes."),
        (r"git\s+(pull|fetch)\b", "pulls remote changes that may auto-merge",
         "Ask the user to run pull/fetch themselves."),
        (r"git\s+clone\b", "downloads large repos and can hang past the timeout",
         "Ask the user to clone it themselves, then point the project at the resulting folder."),
        (r"git\s+reset\s+--hard|git\s+clean\s+-f|git\s+checkout\s+\.\s*$|"
         r"git\s+restore\s+\.\s*$|git\s+branch\s+-[dD]\b",
         "DISCARDS work permanently and cannot be undone",
         "To revert a single file use restore_backup; to inspect differences "
         "use git_diff. Never discard the user's work."),
        (r"git\s+(rebase|merge)\b", "rewrites commit history with a risk of conflicts",
         "Leave rebase/merge to the user."),
    ]

    def __init__(self, project_path: str = "."):
        self.project_path = os.path.abspath(project_path)

    def _resolve_safe_path(self, relative_path: str):
        """
        >>> FIX (path-traversal guard, defense-in-depth): main.py already has
        permission_manager.is_path_safe() for write_file/edit_file, BUT that
        only applies when the caller goes through main.py. ToolExecutor itself
        (read_file, read_file_range, read_json_path, write_file, edit_file,
        list_files) had no defense at all when called directly — a file_path
        containing '../../' could leak/overwrite files outside the project.
        Resolve to an absolute path (handling '..' and symlinks), then make
        sure the result is still inside self.project_path before touching
        anything at all.

        Return: (abs_path, None) when safe, or (None, error_message) when not.
        """
        candidate = os.path.realpath(os.path.join(self.project_path, relative_path))
        project_root = os.path.realpath(self.project_path)

        if candidate != project_root and not candidate.startswith(
            project_root + os.sep
        ):
            return None, (
                f"Error: Path '{relative_path}' resolves to a location outside the "
                f"project folder ({project_root}). Operation denied for security."
            )
        return candidate, None

    def list_files(self, path: str = ".", depth: int = 2) -> str:
        """Read the folder and file structure at a given path, dynamically."""
        target_path, err = self._resolve_safe_path(path)
        if err:
            return err
        if not os.path.exists(target_path):
            return f"Error: Path '{path}' not found."

        tree = []
        ignored_dirs = {
            ".git",
            "node_modules",
            "venv",
            ".venv",
            "__pycache__",
            ".idea",
            ".vscode",
        }

        for root, dirs, files in os.walk(target_path):
            # Filter out ignored folders
            dirs[:] = [d for d in dirs if d not in ignored_dirs]

            # Limit the depth
            # >>> FIX: root.replace(target_path, "") is fragile when target_path
            # happens to appear again as a substring elsewhere in the path
            # (e.g. a project named "app" that also has another subfolder named
            # "app"). os.path.relpath computes from the actual path structure,
            # not raw string matching.
            rel = os.path.relpath(root, target_path)
            level = 0 if rel == "." else rel.count(os.sep) + 1
            if level >= depth:
                dirs[:] = []  # Stop descending deeper
                continue

            indent = " " * 4 * level
            tree.append(f"{indent}{os.path.basename(root)}/")

            sub_indent = " " * 4 * (level + 1)
            for file in files:
                if not file.startswith("."):  # Ignore hidden files
                    tree.append(f"{sub_indent}{file}")

        return "\n".join(tree) if tree else "Empty folder."

    def read_file(self, file_path: str) -> str:
        """Read the full contents of a file."""
        full_path, err = self._resolve_safe_path(file_path)
        if err:
            return err
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error: Cannot read file. {e!s}"

    def read_file_range(
        self, file_path: str, start_line: int = 1, end_line: int = 50
    ) -> str:
        """Read a file by line range. Very useful for large files."""
        full_path, err = self._resolve_safe_path(file_path)
        if err:
            return err
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error: Cannot read file. {e!s}"

        total_lines = len(lines)
        # Convert to 0-based indices and clamp to avoid errors
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, end_line)

        if start_idx >= total_lines:
            return f"Error: start_line ({start_line}) exceeds the file's total lines ({total_lines})."

        result_lines = []
        for i in range(start_idx, end_idx):
            # Format: "LineNumber: LineContent"
            result_lines.append(f"{i + 1}: {lines[i].rstrip()}")

        return "\n".join(result_lines)

    def read_json_path(self, file_path: str, json_path: str) -> str:
        """Read a specific value from a JSON file using dot notation."""
        full_path, err = self._resolve_safe_path(file_path)
        if err:
            return err
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return f"Error: Cannot read JSON file. {e!s}"

        keys = json_path.split(".")
        current = data
        try:
            for key in keys:
                if isinstance(current, dict):
                    current = current[key]
                elif isinstance(current, list):
                    current = current[int(key)]
                else:
                    return f"Error: Path '{json_path}' is invalid (it hits a non-dict/list data type)."
        except (KeyError, IndexError, ValueError):
            return f"Error: Path '{json_path}' not found inside the JSON."

        # Format the output so it is easy for the LLM to read
        if isinstance(current, (dict, list)):
            return json.dumps(current, indent=2)
        return str(current)

    def _backup_before_overwrite(self, full_path: str, file_path: str) -> str:
        """
        >>> FIX: write_file/edit_file used to overwrite files without any
        trace — if the LLM wrote something wrong, the old version was gone
        with no way back. Save a copy of the old file (if any) to
        .orion/backups/<path>.bak before overwriting. The backup is
        overwritten again on the next overwrite (only 1 generation back, not
        a full history) so it cannot bloat. A backup failure MUST NOT fail
        the main operation — this is an extra safety layer, not a
        precondition.
        """
        if not os.path.exists(full_path):
            return ""  # New file, nothing to back up
        try:
            backup_root = os.path.join(self.project_path, ".orion", "backups")
            backup_path = os.path.join(backup_root, file_path + ".bak")
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            shutil.copy2(full_path, backup_path)
            return ""
        except Exception as e:
            return f" [WARNING: backup could not be created: {e}]"

    def write_file(self, file_path: str, content: str) -> str:
        """Create a new file or overwrite an existing one."""
        full_path, err = self._resolve_safe_path(file_path)
        if err:
            return err
        try:
            backup_warning = self._backup_before_overwrite(full_path, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Success: File '{file_path}' written/updated.{backup_warning}"
        except Exception as e:
            return f"Error: Cannot write file. {e!s}"

    def edit_file(self, file_path: str, diff: str):
        """
        Edit a file using the SEARCH/REPLACE format.
        """
        abs_path, err = self._resolve_safe_path(file_path)
        if err:
            return err

        if not os.path.exists(abs_path):
            return f"Error: File '{file_path}' not found. Make sure the path is correct."

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        except Exception as e:
            return f"Error reading file: {e!s}"

        # Regex to extract the SEARCH and REPLACE blocks
        # Expected format:
        # <<<<<<< SEARCH
        # [old code]
        # =======
        # [new code]
        # >>>>>>> REPLACE
        pattern = re.compile(
            r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", re.DOTALL
        )
        matches = pattern.findall(diff)

        if not matches:
            return "Error: Invalid diff format. Make sure you use the <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE format exactly."

        new_content = original_content
        success_count = 0

        for search_block, replace_block in matches:
            # Strip trailing/leading newlines the LLM may have added unintentionally
            search_block = search_block.strip("\n")
            replace_block = replace_block.strip("\n")

            if search_block not in new_content:
                return f"Error: SEARCH block not found in the original file. There may be differing whitespace/newlines.\n\nYour SEARCH block:\n---\n{search_block}\n---"

            # Replace only the first occurrence to avoid duplicates when blocks are identical
            new_content = new_content.replace(search_block, replace_block, 1)
            success_count += 1

        backup_warning = self._backup_before_overwrite(abs_path, file_path)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            return f"Error writing file: {e!s}"

        # The success prefix is a CONTRACT: the agent loop records evidence of
        # real work (files_changed_this_turn) and runs the post-edit
        # verification ONLY for results starting with it. Before UPDATE-004
        # this message started with "Edited file", so pure-edit turns never
        # produced evidence and the post-edit verifier never ran at all.
        return f"Success: edited file '{file_path}'. {success_count} block(s) replaced.{backup_warning}"

    def search_code(self, query: str, file_pattern: str = "*") -> str:
        import fnmatch
        import os  # Make sure os is imported

        results = []
        max_results = 50

        # >>> ADDED THIS LINE <<<
        BINARY_EXTENSIONS = {
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".rlib",
            ".o",
            ".pdb",
            ".pyc",
            ".onnx",
            ".lib",
            ".obj",
            ".wav",
            ".mp3",
        }

        try:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return "Error: Invalid regex format."

        for root, dirs, files in os.walk(self.project_path):
            # Ignore folders that are not needed
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {
                    ".git",
                    "node_modules",
                    "venv",
                    ".venv",
                    "__pycache__",
                    ".idea",
                    ".vscode",
                    ".orion",
                    "target",
                    "dist",
                    "build",
                }
            ]

            for file in files:
                if file_pattern != "*" and not fnmatch.fnmatch(file, file_pattern):
                    continue

                # >>> ADDED THIS LOGIC <<<
                ext = os.path.splitext(file)[1].lower()
                if ext in BINARY_EXTENSIONS:
                    continue  # Skip binary files, do not read them!

                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.project_path)

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if pattern.search(line):
                                results.append(f"{rel_path}:{line_num}: {line.strip()}")
                                if len(results) >= max_results:
                                    return (
                                        "\n".join(results)
                                        + f"\n\n... [LIMITED: More than {max_results} results found. Sharpen your query.]"
                                    )
                except Exception:
                    continue

        if not results:
            return f"No results found for '{query}' with pattern '{file_pattern}'."
        return "\n".join(results)

    def web_search(self, query: str, max_results: int = 5) -> str:
        """Search the internet for real-time information to get unstuck."""
        try:
            from ddgs import DDGS

            with DDGS() as ddgs:
                results = [r for r in ddgs.text(query, max_results=max_results)]

            if not results:
                return "No results found for that query."

            # Format results so they are easy for the LLM to read
            formatted_results = []
            for r in results:
                formatted_results.append(
                    f"Title: {r.get('title', 'No Title')}\n"
                    f"URL: {r.get('href', 'No URL')}\n"
                    f"Snippet: {r.get('body', 'No Snippet')}"
                )
            return "\n\n---\n\n".join(formatted_results)

        except ImportError:
            return "Error: Library 'duckduckgo-search' is not installed. Run: pip install duckduckgo-search"
        except Exception as e:
            return f"Error during web search: {e!s}"

    def web_fetch(self, url: str, max_chars: int = 20000) -> str:
        """Fetch the contents of a URL and return it as clean text.

        Complements web_search: searching only yields SNIPPETS, while this
        tool lets Orion read documentation/error pages in full.

        Uses only the standard library (urllib) -- no added dependencies.
        Has SSRF protection: URLs pointing at localhost/private networks are
        REJECTED, because the destination address is chosen by the model.
        """
        import ipaddress
        import socket
        import urllib.error
        import urllib.request
        from urllib.parse import urlparse

        if not url or not isinstance(url, str):
            return "Error: Empty URL."
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url

        try:
            parsed = urlparse(url)
        except ValueError as e:
            return f"Error: Invalid URL: {e}"

        if parsed.hostname is None:
            return "Error: URL has no hostname."

        # ---- SSRF protection ----
        try:
            host = parsed.hostname.lower()
            if host in ("localhost", "metadata.google.internal"):
                return "Error: local/metadata URL rejected (SSRF protection)."
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return f"Error: {host} resolves to a private/loopback address ({ip}). Rejected (SSRF protection)."
        except (socket.gaierror, ValueError, OSError) as e:
            return f"Error: DNS resolution failed for {parsed.hostname}: {e}"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; OrionBot/1.0; code-assistant)",
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:  # nosec - URL already validated
                status = resp.status
                ctype = resp.headers.get("Content-Type", "")
                mentah = resp.read(3_000_000)   # 3 MB cap
        except urllib.error.HTTPError as e:
            return f"HTTP error {e.code} while fetching {url}: {e.reason}"
        except urllib.error.URLError as e:
            return f"Connection error while fetching {url}: {e.reason}"
        except (OSError, ValueError) as e:
            return f"Error while fetching {url}: {type(e).__name__}: {e}"

        try:
            teks = mentah.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return f"Error: failed to decode response from {url} (Content-Type: {ctype})."

        # ---- clean HTML into text ----
        if "html" in ctype.lower() or teks.lstrip()[:1].startswith("<"):
            import re as _re
            # >>> PATCH ORION-35c: modern documentation pages put the navigation
            # MENU at the start of the document, so a naive extraction returns the
            # menu list instead of the content. Strip boilerplate first, then
            # prioritize <main>/<article>.
            teks = _re.sub(r"(?is)<(nav|header|footer|aside|menu)[^>]*>.*?</\1>", " ", teks)
            _inti = _re.search(r"(?is)<(main|article)[^>]*>(.*?)</\1>", teks)
            if _inti and len(_inti.group(2)) > 800:
                teks = _inti.group(2)
            teks = _re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", teks)
            teks = _re.sub(r"(?s)<!--.*?-->", " ", teks)
            teks = _re.sub(r"(?s)<[^>]+>", " ", teks)
            for ent, gnt in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                             ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
                teks = teks.replace(ent, gnt)
            teks = _re.sub(r"[ \t]+", " ", teks)
            teks = _re.sub(r"\n\s*\n+", "\n\n", teks)

        teks = teks.strip()
        if not teks:
            return f"URL {url} was fetched (HTTP {status}) but its content is empty after cleaning."

        try:
            batas = max(1000, min(int(max_chars), 100_000))
        except (TypeError, ValueError):
            batas = 20000
        terpotong = len(teks) > batas
        if terpotong:
            teks = teks[:batas]

        return (
            f"=== CONTENT OF {url} (HTTP {status}, {ctype.split(';')[0]}) ===\n"
            f"{teks}\n"
            + (f"\n=== [TRUNCATED at {batas} characters; the original page is longer] ==="
               if terpotong else "\n=== [END] ===")
        )

    # >>> PATCH ORION-23a ================================================
    def _cek_sintaks_js(self, kode: str):
        """Return (line_number, message) when there is a syntax error, otherwise None.

        Uses `node --check` -- the REAL JavaScript syntax checker. Reading code
        with the model's eyes CANNOT detect a missing bracket; a parser can.
        """
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            return (0, "node is not installed; JS syntax check skipped. "
                       "Install Node.js so verify_html works fully.")
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                              encoding="utf-8") as f:
                f.write(kode)
                tmp = f.name
            r = subprocess.run([node, "--check", tmp], capture_output=True,
                               text=True, timeout=30)
            if r.returncode == 0:
                return None
            m = re.search(r"\.js:(\d+)", r.stderr)
            baris = int(m.group(1)) if m else 0
            pesan = next((x.strip() for x in r.stderr.splitlines()
                          if "Error" in x), r.stderr.strip()[:200])
            return (baris, pesan)
        except (OSError, subprocess.SubprocessError) as e:
            return (0, f"syntax check failed to run: {e}")
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def verify_html(self, file_path: str) -> str:
        """HTML checker. Since ORION-25 this is merely an alias of verify_code;
        the checking logic lives in app/services/code_verifier.py + html_checker.py
        so there is one source of truth for all languages."""
        return self.verify_code(file_path)

    def verify_code(self, file_path: str) -> str:
        """Check ANY supported file with real parsers/compilers.

        Supports: .py .js .mjs .cjs .jsx .ts .tsx .html .htm .json .xml .svg
        .yaml .yml .css .scss -- and also FOLDERS (checks up to 40 files).

        For .py: syntax (compile), uninstalled imports (which would become
        ModuleNotFoundError at runtime), plus ruff/pyflakes when available.
        For .js: node --check. For .ts: tsc --noEmit when available.
        For .html: inline <script> syntax + duplicate ids + DOM references +
        structural tag balance.

        MUST be used before declaring code "working" or "finished".
        """
        from app.services import code_verifier

        full, err = self._resolve_safe_path(file_path)
        if err:
            return err
        return code_verifier.verify_path(full, self._pilih_interpreter_python())

    # >>> END OF PATCH ORION-23a =======================================

    # >>> PATCH ORION-26a ================================================
    def _path_backup(self, file_path: str) -> str:
        """Backup location for a file, following the pattern already used by
        _backup_before_overwrite: .orion/backups/<path>.bak"""
        return os.path.join(self.project_path, ".orion", "backups", file_path + ".bak")

    def list_backups(self, file_path: str = "") -> str:
        """List the backups available for restoration."""
        root = os.path.join(self.project_path, ".orion", "backups")
        if not os.path.isdir(root):
            return "No backups exist yet in .orion/backups/."
        entri = []
        for akar, _dirs, files in os.walk(root):
            for nama in sorted(files):
                if not nama.endswith(".bak"):
                    continue
                full = os.path.join(akar, nama)
                rel = os.path.relpath(full, root)[:-4]   # strip .bak
                if file_path and not rel.replace("\\", "/").endswith(file_path.replace("\\", "/")):
                    continue
                try:
                    uk = os.path.getsize(full)
                    wt = datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
                except OSError:
                    uk, wt = 0, "?"
                entri.append(f"  {rel}  ({uk} bytes, {wt})")
        if not entri:
            return ("No backups" + (f" for '{file_path}'" if file_path else "")
                    + " in .orion/backups/.")
        return f"Available backups ({len(entri)}):\n" + "\n".join(entri[:40])

    def restore_backup(self, file_path: str) -> str:
        """Restore a file from its latest backup.

        SAFE and REVERSIBLE: the current version (possibly broken) is backed
        up first to .orion/backups/<path>.pre-restore.bak before being
        overwritten, so restoring never loses anything permanently.
        """
        full, err = self._resolve_safe_path(file_path)
        if err:
            return err
        bak = self._path_backup(file_path)
        if not os.path.isfile(bak):
            return (f"Error: no backup for '{file_path}'. "
                    f"Run list_backups to see what is available.")
        try:
            if os.path.isfile(full):
                shutil.copy2(full, bak + ".pre-restore.bak")
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copy2(bak, full)
            return (f"Success: '{file_path}' restored from backup. "
                    f"The pre-restore version is kept in .orion/backups/"
                    f"{file_path}.pre-restore.bak in case you need to roll back again. "
                    f"Now run verify_code on that file to make sure its contents are healthy.")
        except OSError as e:
            return f"Error: failed to restore '{file_path}': {e}"

    def delete_file(self, file_path: str) -> str:
        """Delete a file, BUT back it up first so it can be restored.

        Orion previously could only delete via run_terminal (triggering the
        confirmation modal and leaving no way back). This is the safe path.
        """
        full, err = self._resolve_safe_path(file_path)
        if err:
            return err
        if not os.path.isfile(full):
            return f"Error: file not found: {file_path}"
        try:
            peringatan = self._backup_before_overwrite(full, file_path)
            os.remove(full)
            return (f"Success: '{file_path}' deleted. Its last copy is in "
                    f".orion/backups/{file_path}.bak — restore it with restore_backup "
                    f"if it turns out to be needed.{peringatan}")
        except OSError as e:
            return f"Error: failed to delete '{file_path}': {e}"

    def move_file(self, from_path: str, to_path: str) -> str:
        """Move/rename a file inside the project, backing up the source first."""
        src, err = self._resolve_safe_path(from_path)
        if err:
            return err
        dst, err2 = self._resolve_safe_path(to_path)
        if err2:
            return err2
        if not os.path.isfile(src):
            return f"Error: source not found: {from_path}"
        if os.path.exists(dst):
            return (f"Error: destination already exists: {to_path}. Delete it first with delete_file "
                    f"if you really intend to overwrite, so the intent is explicit.")
        try:
            self._backup_before_overwrite(src, from_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            return (f"Success: '{from_path}' moved to '{to_path}'. "
                    f"The source backup remains in .orion/backups/{from_path}.bak. "
                    f"Remember: update imports/references that point to the old path.")
        except OSError as e:
            return f"Error: failed to move: {e}"

    def git_commit(self, message: str) -> str:
        """Create a safe point (checkpoint) with git commit.

        Orion used to attempt `git commit` through run_terminal -- scary for
        the user because it triggered the shell-command confirmation modal.
        This is a controlled dedicated path: only `git add -A` + `git commit`,
        no shell, and it rejects with a clear message when the folder is not
        a git repo.
        """
        pesan = (message or "").strip() or "Orion checkpoint"
        if not os.path.isdir(os.path.join(self.project_path, ".git")):
            return ("Error: this project folder is NOT a git repo, so there is nothing to "
                    "commit. If you want a safe point without git, use the backups: "
                    "every write_file/edit_file already saves .orion/backups automatically.")
        try:
            r = subprocess.run(["git", "add", "-A"], cwd=self.project_path,
                               capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                return f"Error git add: {r.stderr.strip()[:300]}"
            r2 = subprocess.run(["git", "commit", "-m", pesan], cwd=self.project_path,
                                capture_output=True, text=True, timeout=120,
                                encoding="utf-8", errors="replace")
            keluar = (r2.stdout or r2.stderr).strip()
            if r2.returncode != 0:
                if "nothing to commit" in keluar.lower():
                    return "Nothing to commit (working tree clean)."
                return f"Error git commit: {keluar[:300]}"
            return f"Success: git checkpoint created. {keluar.splitlines()[0] if keluar else ''}"
        except (OSError, subprocess.SubprocessError) as e:
            return f"Error running git: {e}"
    # >>> END OF PATCH ORION-26a ========================================

    # >>> PATCH ORION-30a ================================================
    def _pilih_interpreter_python(self) -> str:
        """Use the CHECKED PROJECT'S OWN venv, not the Orion backend's venv.

        Without this, `run_tests` on another project ran with the backend's
        interpreter: that project's dependencies were invisible, and the
        report could claim "ModuleNotFoundError" that does not actually exist
        in the project environment -- or conversely, pass only because the
        backend happened to have the package.
        """
        for kandidat in (
            os.path.join(self.project_path, ".venv", "Scripts", "python.exe"),
            os.path.join(self.project_path, "venv", "Scripts", "python.exe"),
            os.path.join(self.project_path, ".venv", "bin", "python"),
            os.path.join(self.project_path, "venv", "bin", "python"),
        ):
            if os.path.isfile(kandidat):
                return kandidat
        return sys.executable

    def _pytest_terpasang(self, interpreter: str) -> bool:
        try:
            r = subprocess.run([interpreter, "-c", "import pytest"],
                               capture_output=True, text=True, timeout=30,
                               cwd=self.project_path, stdin=subprocess.DEVNULL)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _deteksi_runner_test(self):
        """Decide how to run tests based on the project contents. Return (name, argv) or None."""
        root = self.project_path

        # Python: pytest
        punya_pytest_cfg = any(
            os.path.exists(os.path.join(root, n))
            for n in ("pytest.ini", "tox.ini", "setup.cfg", "conftest.py")
        )
        if punya_pytest_cfg or self._ada_file_test_python(root):
            interp = self._pilih_interpreter_python()
            if not self._pytest_terpasang(interp):
                return ("pytest-hilang", [interp])   # flagged; handled by run_tests
            return ("pytest", [interp, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider"])

        # Node: package.json with a test script that is not a placeholder
        pkg = os.path.join(root, "package.json")
        if os.path.isfile(pkg):
            try:
                with open(pkg, encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                skrip = (data.get("scripts") or {}).get("test", "")
                if skrip and "no test specified" not in skrip:
                    return ("npm", ["npm", "test", "--silent"])
            except (OSError, ValueError):
                pass

        # Go
        if os.path.isfile(os.path.join(root, "go.mod")):
            return ("go", ["go", "test", "./..."])

        return None

    @staticmethod
    def _ada_file_test_python(root: str) -> bool:
        for _akar, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (
                ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".orion")]
            for nama in files:
                if nama.startswith("test_") or nama.endswith("_test.py"):
                    return True
        return False

    def run_tests(self, command: str = "", timeout: int = 180) -> str:
        """Run the project's test suite and REPORT THE RESULTS IN DETAIL.

        Why this tool exists: without it, the only way for Orion to learn the
        code's behaviour was running it via run_terminal and reading hundreds
        of lines of raw output -- token-expensive and easy to misread.
        Here the output is condensed to: pass/fail counts + the list of
        failed tests + one cause line per failure.

        The runner is detected automatically (pytest / npm test / go test)
        unless `command` is filled in manually. Running tests means running
        project code -- the same trust level as running the application.
        """
        if command and command.strip():
            nama, argv = "manual", command.strip().split()
        else:
            deteksi = self._deteksi_runner_test()
            if not deteksi:
                return (
                    "No test framework found in this project (no pytest.ini/"
                    "conftest.py/test_*.py, no test script in package.json, and no go.mod). "
                    "A project without tests cannot verify its own behavior -- "
                    "consider adding at least one test, or run "
                    "verify_code for syntax and structure checks."
                )
            nama, argv = deteksi

        if nama == "pytest-hilang":
            interp = argv[0]
            return (
                "This project HAS test files, but pytest is not installed in the project "
                f"interpreter ({interp}). This is not a code failure. Run it yourself: "
                f'"{interp}" -m pip install pytest   then call run_tests again, '
                "or ask the user for permission to install it."
            )

        try:
            batas = max(10, min(int(timeout), 600))
        except (TypeError, ValueError):
            batas = 180

        try:
            r = subprocess.run(argv, cwd=self.project_path, capture_output=True,
                               text=True, timeout=batas, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return (f"Tests ({nama}) DID NOT FINISH within {batas} seconds. "
                    "A test is likely hanging (waiting for input/network). "
                    "Run it manually with a longer timeout to see which one.")
        except (OSError, subprocess.SubprocessError) as e:
            return f"Failed to run tests ({nama}): {e}"

        keluar = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        return self._urai_hasil_test(nama, r.returncode, keluar)

    def _urai_hasil_test(self, nama: str, kode_keluar: int, keluar: str) -> str:
        """Condense test output into a report that is token-cheap and easy to act on."""
        baris = keluar.splitlines()

        if nama == "pytest":
            gagal = [b.strip() for b in baris if b.strip().startswith("FAILED ")]
            error = [b.strip() for b in baris if b.strip().startswith("ERROR ")]
            ringkasan = next((b.strip() for b in reversed(baris)
                              if re.search(r"\d+ (passed|failed|error)", b)), "")
            if kode_keluar == 0 and not gagal and not error:
                return (f"ALL TESTS PASSED ({nama}). {ringkasan or 'see output'}. "
                        "The code changes are safe with respect to the existing tests.")
            bagian = [f"TESTS FAILED ({nama}). {ringkasan}"]
            bagian.extend(f"  {g[:160]}" for g in gagal[:15])
            bagian.extend(f"  {e[:160]}" for e in error[:10])
            sebab = [b.strip() for b in baris if re.match(r"^.{0,120}Error|assert", b.strip())]
            if sebab:
                bagian.append("  causes (related lines):")
                bagian.extend(f"    {x[:150]}" for x in sebab[:8])
            bagian.append("Fix the failures above first; do NOT report the task as complete "
                          "while tests are still red.")
            return "\n".join(bagian)

        # other runners (npm/go/manual): condense generically
        pola_gagal = re.search(r"(\d+)\s+(?:failed|failing)", keluar, re.I)
        pola_lulus = re.search(r"(\d+)\s+(?:passed|ok\b)", keluar, re.I)
        if kode_keluar == 0:
            return (f"TESTS PASSED ({nama}). "
                    f"passed={pola_lulus.group(1) if pola_lulus else '?'} "
                    f"failed={pola_gagal.group(1) if pola_gagal else '0'}.")
        penting = [b.strip() for b in baris if b.strip()][-25:]
        return (f"TESTS FAILED ({nama}, exit {kode_keluar}). "
                f"failed={pola_gagal.group(1) if pola_gagal else '?'}.\n"
                "  last 25 lines of output:\n" + "\n".join(f"    {b[:160]}" for b in penting)
                + "\nFix these before claiming completion.")
    # >>> END OF PATCH ORION-30a ========================================

    def git_status(self) -> str:
        """View the list of changed files (very token-efficient)."""
        try:
            # Only take the names of modified, added, or deleted files
            cmd = ["git", "status", "--porcelain"]
            result = subprocess.run(
                cmd, cwd=self.project_path, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                if not result.stdout.strip():
                    return "Working tree clean. No files changed."

                # Format the output to be readable and very terse
                lines = result.stdout.strip().split("\n")
                return f"Changed files ({len(lines)} files):\n" + "\n".join(
                    [f"- {line[3:]}" for line in lines if line]
                )
            return f"Error git status: {result.stderr}"
        except Exception as e:
            return f"Failed to run git status: {e!s}"

    def git_diff(self, file_path: str = "") -> str:
        """View code changes. SAFE: when file_path is empty it only summarizes the changed files instead of showing the whole diff."""
        try:
            if file_path:
                # If a specific file is requested, show its diff (BUT CAPPED)
                cmd = ["git", "diff", file_path]
                result = subprocess.run(
                    cmd,
                    cwd=self.project_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    diff_text = result.stdout
                    if not diff_text.strip():
                        return f"No changes in {file_path}."
                    # 🛡️ SAFEGUARD: cap diff output at 1500 characters (~300 tokens)
                    if len(diff_text) > 1500:
                        return (
                            diff_text[:1500]
                            + "\n\n[... DIFF TRUNCATED. Use read_file_range on this file if you need the details ...]"
                        )
                    return diff_text
                return f"Error git diff: {result.stderr}"
            else:
                # 🛡️ MAIN SAFEGUARD: when there is no file_path, do NOT show the whole project diff!
                # Just call git_status so the model knows which files changed.
                return (
                    self.git_status()
                    + "\n\n[INFO: To see the actual changes, call git_diff with a specific file_path argument, or use read_file_range.]"
                )
        except Exception as e:
            return f"Failed to run git diff: {e!s}"

    def run_terminal(self, command: str, timeout: int = 120) -> str:
        """
        Run a terminal command with a timeout and aggressive process-tree kill.
        Prevents zombie processes (like rustc.exe) from blocking the system.
        """
        # >>> PATCH ORION-27 ==============================================
        # Block destructive / networked / hang-prone git operations through
        # run_terminal. Real case: Orion ran `git init` through here and hung,
        # then the backend died and the user saw "connection lost". The safe
        # git operations already have their own tools (git_status, git_diff,
        # git_commit); the rest is the user's right to run consciously, not
        # the agent's.
        # ==================================================================
        for pola, alasan, saran in self.GIT_DIBLOKIR:
            if re.search(pola, command.strip().lower()):
                return (
                    f"[REJECTED BY SYSTEM] Command '{command.strip()[:70]}' may not "
                    f"be run through run_terminal because it {alasan}. {saran} "
                    "Do NOT try to work around this rejection with other command variants; "
                    "report to the user that the operation requires manual action."
                )
        # >>> FIX (fatal crash on non-Windows): subprocess.CREATE_NEW_PROCESS_GROUP
        # only EXISTS as a module attribute on Windows. On Linux/Mac,
        # `subprocess.CREATE_NEW_PROCESS_GROUP` raised AttributeError before any
        # command could run — the entire run_terminal feature was dead.
        # The solution differs per platform: Windows uses that creationflag (then
        # taskkill /T for the kill tree), POSIX uses preexec_fn=os.setsid to
        # create a new process group (then os.killpg for the kill tree).
        is_windows = sys.platform == "win32"
        popen_kwargs = {}
        if is_windows:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        process = None
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=self.project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout to prevent deadlock
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                **popen_kwargs,
            )

            # Use communicate with a timeout to handle blocking safely
            output = ""
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # AGGRESSIVELY KILL THE PROCESS TREE (cross-platform)
                if process and process.pid:
                    if is_windows:
                        subprocess.run(
                            f"taskkill /F /T /PID {process.pid}",
                            shell=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                return (
                    f"{output if output else ''}\n\n"
                    f"⏰ [SYSTEM TIMEOUT]: Command cancelled after {timeout} seconds. "
                    f"The process and all child processes (e.g. rustc.exe) were force-stopped. "
                    f"If this is a heavy build (such as a Tauri Release), run it manually in your own terminal."
                )

            # >>> CRITICAL FIX: provide a clear error message <<<
            if process.returncode != 0:
                error_msg = (
                    output.strip()
                    if output
                    else f"Process exited with code {process.returncode}"
                )
                return (
                    f"❌ [ERROR EXECUTION]: Command failed with exit code {process.returncode}\n\n"
                    f"--- Output/Error Log ---\n{error_msg}\n"
                    f"--- End of Log ---\n\n"
                    f"[SYSTEM HINT]: Analyze the error above. If this is a dependency issue, "
                    f"try installing the required packages. If it is a syntax error, fix the code."
                )

            return output.strip() if output.strip() else "Success, no output."

        except Exception as e:
            # Fallback kill for any other error (cross-platform)
            if process and process.pid:
                try:
                    if is_windows:
                        subprocess.run(
                            f"taskkill /F /T /PID {process.pid}",
                            shell=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass
            return f"❌ [SYSTEM ERROR]: Failed to run the terminal command. {e!s}"
