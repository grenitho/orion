"""
permission_manager.py

An extra protection layer (defense-in-depth) ensuring file operations
(write_file/edit_file) never leave the project_path boundary -- preventing
path traversal (e.g. file_path="../../../etc/passwd", or absolute paths to
other locations) whether caused by a model mistake or by possible prompt
injection from the contents of a previously read file.

This module is purely ADDITIVE: it only adds a NEW check before the tool
executes. For valid paths (the normal case, 99.9% of daily use) this
function always returns (True, "OK") and does not change the already
stable behaviour at all.
"""

import os

# Sensitive file/folder name patterns that the model should not overwrite
# automatically without explicit user confirmation, even when the path is
# technically valid (still inside the project). This is a SOFT heuristic --
# its goal is preventing accidental incidents (e.g. the model trying to
# "clean up" .env), not absolute security protection.
SENSITIVE_PATTERNS = (
    ".env",
    ".git/",
    "id_rsa",
    ".pem",
    ".key",
    ".pfx",
)


def is_path_safe(project_path: str, file_path: str):
    """
    Return (is_safe: bool, reason: str).

    is_safe is True ONLY if file_path, after being resolved to an absolute
    path, stays inside project_path. False when path traversal (../..) is
    detected, an absolute path outside the project, or a different drive
    (Windows only).
    """
    if not project_path:
        return False, "project_path has not been set in the system."
    if not file_path or not str(file_path).strip():
        return False, "file_path is empty."

    try:
        project_root = os.path.realpath(project_path)
        target_path = os.path.realpath(os.path.join(project_path, file_path))
    except Exception as e:
        return False, f"Failed to resolve path: {e}"

    try:
        common = os.path.commonpath([project_root, target_path])
    except ValueError:
        # Happens when project & target are on different drives (Windows
        # only, e.g. the project is on one drive but the target resolves to
        # another)
        return False, (
            f"Path '{file_path}' points to a different drive/location than the "
            f"project root ('{project_root}'). Operation denied for security."
        )

    if common != project_root:
        return False, (
            f"Path '{file_path}' resolves OUTSIDE the project folder "
            f"('{project_root}'). Operation denied to prevent path traversal."
        )

    return True, "OK"


def is_sensitive_file(file_path: str) -> bool:
    """Simple heuristic: is this file likely to contain secrets/credentials
    that should not be edited automatically without the user's explicit
    confirmation?"""
    if not file_path:
        return False
    normalized = str(file_path).replace("\\", "/").lower()
    return any(pattern in normalized for pattern in SENSITIVE_PATTERNS)