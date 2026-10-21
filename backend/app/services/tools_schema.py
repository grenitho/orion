"""
tools_schema.py - the schema of Orion's 22 tools (Phase A of the main.py split)
Pure data: no logic, no heavy imports.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Read the folder and file structure at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative folder path (default: '.').",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Folder depth (default: 2).",
                    },
                },
                # There is NO "required": [] HERE
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path relative to the project root.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit specific parts of an existing file using the SEARCH/REPLACE format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path.",
                    },
                    "diff": {
                        "type": "string",
                        "description": "The search and replace block. Format: <<<<<<< SEARCH\\n[old code]\\n=======\\n[new code]\\n>>>>>>> REPLACE",
                    },
                },
                "required": ["file_path", "diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a string or regex across the whole project. Returns AT MOST 50 matches; if the output ends with a [LIMITED ...] note, there are more matches than shown - narrow the query or set file_pattern (e.g. '*.py') instead of re-running the same search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The keyword or regex to search for.",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "File extension filter (e.g. '*.py'). Leave empty for all files.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restore_backup",
            "description": "Restore a file from its latest backup in .orion/backups. Reversible: the current version is saved first as .pre-restore.bak. Use this when edit_file/write_file turned out to break something.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Relative path of the file to restore."}}, "required": ["file_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_backups",
            "description": "List the backups available for restoration. Use before restore_backup when unsure whether a backup exists.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Optional: filter by path."}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file SAFELY: its last copy is backed up to .orion/backups first so it can be restored. Do NOT delete files through run_terminal.",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string", "description": "Relative path of the file to delete."}}, "required": ["file_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file inside the project, backing up the source first. Refuses to overwrite an existing file at the destination.",
            "parameters": {"type": "object", "properties": {"from_path": {"type": "string"}, "to_path": {"type": "string"}}, "required": ["from_path", "to_path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Create a safe point (checkpoint) with git add -A + git commit. Use AFTER a batch of changes passes verify_code, so there is a clean point to return to. Rejects with an explanation when the folder is not a git repo. Do NOT commit through run_terminal.",
            "parameters": {"type": "object", "properties": {"message": {"type": "string", "description": "Commit message describing the changes."}}, "required": ["message"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run the project's test suite (pytest / npm test / go test, detected "
                "automatically) and return a DETAILED report: pass/fail counts plus the list "
                "of failed tests and one cause line each. MUST be called after changing code "
                "when the project has tests, and its results MUST be quoted when reporting "
                "completion. While tests are still red, a completion claim is a lie."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Optional: manual test command. Leave empty for automatic detection."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 180, max 600).", "default": 180},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_code",
            "description": (
                "Check ANY file with real parsers/compilers: .py (syntax + "
                "uninstalled imports + ruff), .js/.mjs/.cjs/.jsx (node --check), "
                ".ts/.tsx (tsc --noEmit), .html/.htm (inline script syntax, duplicate ids, "
                "DOM references, tag balance), .json/.xml/.svg/.yaml/.css. Also accepts "
                "a FOLDER to check many files at once. MUST be called "
                "BEFORE declaring code working, passing, or finished. The model cannot "
                "see a missing bracket; this checker can."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path of the file or folder to check.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_html",
            "description": (
                "Check HTML/JS files with a REAL checker: node --check for JavaScript "
                "syntax, duplicate id detection, and getElementById/querySelector references "
                "whose elements do not exist. MUST be called BEFORE declaring a page "
                "working, connected, or error-free. A single missing bracket kills the entire "
                "script block, and that kind of thing is NOT visible by reading the code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path of the .html/.htm/.js file to check.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Read the CONTENTS of a URL in full and return them as text. "
                "Use AFTER web_search found a relevant URL, or to read official "
                "documentation / issue pages / changelogs. web_search only gives "
                "snippets; this tool gives the full content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to read (http/https).",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Cap on the number of content characters returned (default 20000, maximum 100000).",
                        "default": 20000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for real-time information. MUST be used when hitting an unknown error, needing a current URL, or needing API documentation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific search keywords (e.g. 'piper tts indonesian male voice huggingface url').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": "Read a file by line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path.",
                    },
                    "start_line": {"type": "integer", "description": "Start line."},
                    "end_line": {"type": "integer", "description": "End line."},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_json_path",
            "description": "Read a specific value from a JSON file using dot notation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path of the JSON file.",
                    },
                    "json_path": {
                        "type": "string",
                        "description": "Key path with dot separators.",
                    },
                },
                "required": ["file_path", "json_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Run a terminal/shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The terminal command."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "STOP the process to discuss with the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question or set of options presented to the user.",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todos",
            "description": (
                "Create or update the todo (checklist) list for the multi-step task "
                "being worked on, so progress is clearly visible to the user. "
                "MUST be called at the START when receiving a multi-step/itemized "
                "instruction (e.g. 'add features A, B, C'), then update each item's status "
                "as progress is made (pending -> in_progress -> completed). "
                "For a simple single-step task, this tool MAY be skipped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The complete todo list (replaces the entire previous list).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Short unique id, e.g. '1', '2'.",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Short description of the sub-task.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "The current status of this sub-task.",
                                },
                            },
                            "required": ["text"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "View a short list of files that were modified, added, or deleted.",
            "parameters": {
                "type": "object"
                # ONLY "type": "object". NO properties or required.
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "View code changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path of a specific file (optional). Leave empty for a summary.",
                    }
                },
                # There is NO "required": [] HERE
            },
        },
    },
]
