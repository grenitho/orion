"""
auth.py — shared-secret token guard (the last SECURITY.md promise)
===================================================================
The backend binds to loopback only and CORS is closed, but ANY local
process can still connect to 127.0.0.1:8000 and call the endpoints. This
module narrows "who may talk to Orion" down to "who can read the user's
home directory": a random 256-bit secret, minted once, that the extension
attaches to every request as the X-Orion-Token header.

Token resolution order (first hit wins):
  1. the ORION_SHARED_TOKEN environment variable (explicit override, honoured
     by BOTH the backend and the extension);
  2. the token file: path from ORION_TOKEN_FILE when set (tests use this),
     otherwise ~/.orion_shared_secret — created by the backend on first
     start with restrictive permissions.

The extension reads the same file (orion/src/services/authToken.ts), so a
normal install needs zero configuration. Deleting the file makes the next
backend start mint a fresh secret; the extension picks it up on its next
request because it re-reads the file every time.

Comparison is constant-time (hmac.compare_digest). The guard itself is an
HTTP middleware in main.py: everything except the bare health probe GET /
and CORS preflight requires the header. The secret is never logged.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

TOKEN_ENV = "ORION_SHARED_TOKEN"
TOKEN_FILE_ENV = "ORION_TOKEN_FILE"
DEFAULT_TOKEN_FILE = Path.home() / ".orion_shared_secret"
HEADER = "x-orion-token"


def token_file_path() -> Path:
    """Where the shared secret lives on this machine (overridable for tests)."""
    override = os.getenv(TOKEN_FILE_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_TOKEN_FILE


def get_shared_token() -> str:
    """Return the shared secret, minting and persisting one if needed."""
    env = os.getenv(TOKEN_ENV, "").strip()
    if env:
        return env

    path = token_file_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    fresh = secrets.token_hex(32)
    try:
        path.write_text(fresh + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:  # Windows honours only part of the mode bits
            pass
    except OSError:
        print(
            f"[ORION AUTH WARNING] cannot persist the shared token to {path}; "
            "the extension will not be able to authenticate until this is fixed."
        )
    return fresh


def request_is_authorized(headers) -> bool:
    """Constant-time check of the X-Orion-Token header against the secret."""
    provided = headers.get(HEADER) or ""
    if not provided:
        return False
    return hmac.compare_digest(provided, get_shared_token())
