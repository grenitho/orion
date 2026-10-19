"""
post_edit_verifier.py

Automatic post-edit verification: after write_file/edit_file executes,
RE-READ the file from disk to prove the change really was saved as expected
-- instead of merely trusting the tool's return-value string without hard
evidence.

Designed as an EXTRA LAYER appended to the existing tool result (see the
integration in main.py). It does not change the old format and does not
change write_file/edit_file behaviour itself -- if this module fails to
import or errors out, the original tools keep working as usual (see the
try/except on the caller side in main.py).
"""

import os
import re

_REPLACE_BLOCK_RE = re.compile(r"=======\s*\n(.*?)\n>>>>>>> REPLACE", re.DOTALL)


def extract_replace_snippet(diff_text: str, max_len: int = 200) -> str:
    """
    Take the first non-empty line of the REPLACE block in the
    SEARCH/REPLACE format (<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE),
    used as lightweight verification evidence. Deliberately does not match
    the whole block, to avoid false negatives from insignificant minor
    whitespace/indentation differences.
    """
    if not diff_text:
        return ""
    match = _REPLACE_BLOCK_RE.search(diff_text)
    if not match:
        return ""
    replace_content = match.group(1).strip()
    lines = [ln for ln in replace_content.split("\n") if ln.strip()]
    if not lines:
        return ""
    return lines[0].strip()[:max_len]


def verify_file_write(full_path: str, expected_content: str = None) -> str:
    """
    Verification after write_file: make sure the file really exists on disk,
    and (when expected_content is given) its contents match exactly.
    """
    if not os.path.exists(full_path):
        return (
            "⚠️ [VERIFICATION FAILED] File not found on disk after "
            "write_file was called. The write likely failed silently "
            "(permissions, full disk, or wrong path)."
        )

    try:
        actual_size = os.path.getsize(full_path)
    except Exception as e:
        return f"⚠️ [VERIFICATION FAILED] File exists but its size could not be read: {e}"

    if expected_content is not None:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                actual_content = f.read()
        except Exception as e:
            return f"⚠️ [VERIFICATION FAILED] File exists but could not be re-read: {e}"

        if actual_content != expected_content:
            return (
                "⚠️ [VERIFICATION MISMATCH] File saved, but its contents differ "
                "from the content that was sent (possibly line-ending normalization, or "
                "another process overwrote this file afterwards). You MUST re-read this "
                "file before continuing."
            )

    return f"✅ [VERIFICATION OK] File confirmed saved on disk ({actual_size} bytes)."


def verify_file_edit(full_path: str, expected_snippet: str) -> str:
    """
    Verification after edit_file: make sure the snippet from the REPLACE
    block really appears in the final file.
    """
    if not os.path.exists(full_path):
        return "⚠️ [VERIFICATION FAILED] File not found on disk after edit_file was called."

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            actual_content = f.read()
    except Exception as e:
        return f"⚠️ [VERIFICATION FAILED] File exists but could not be re-read: {e}"

    snippet = (expected_snippet or "").strip()
    if not snippet:
        # No specific snippet to match (non-standard diff format) --
        # still report that the file exists, but do not claim its contents are correct.
        return "ℹ️ [VERIFICATION PARTIAL] File exists on disk, but there is no specific snippet to match automatically. A manual re-read is recommended if in doubt."

    if snippet in actual_content:
        return "✅ [VERIFICATION OK] REPLACE snippet confirmed present in the final file."

    return (
        "⚠️ [VERIFICATION MISMATCH] REPLACE snippet NOT found in the "
        "final file. The edit may have failed silently, or the SEARCH/REPLACE format "
        "did not match the file contents exactly. You MUST re-read this file "
        "(read_file) before continuing or claiming the task is complete."
    )