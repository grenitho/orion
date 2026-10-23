"""
inbox_manager.py — the folder drop-zone for user files (UPDATE-018)
====================================================================
The VS Code chat panel has no attachment UI, and walking a beginner through
base64 pasting would be cruel. So Orion grows an ambient inbox: drop files
into ``<project>/.orion/inbox/`` and the NEXT chat turn picks them up:

  * text family (txt/md/csv/code/json/…)  -> capped plain text, injected as
    a labelled context block for that turn only;
  * images (png/jpg/webp)                 -> base64 vision parts, same
    single-turn injection rule as the old attachment feature;
  * pdf / docx / xlsx                     -> extracted to text via pypdf /
    python-docx / openpyxl (soft imports: a missing parser degrades to a
    per-file warning, never to a crash);
  * everything processed or broken is MOVED to ``.orion/inbox/archive/``
    (never deleted - user data is sacred); unsupported/oversized files stay
    in the inbox and are warned about exactly once per file version
    (name+size+mtime manifest in ``.orion/inbox_state.json``).

Budgets (clamped, configurable in .orion/orion_config.json):
  inbox_enabled (default true), inbox_max_files (default 5 per turn),
  plus hard caps: 20 MB per file (images: 7 MB — the vision API's inline
  limit, same cap as attachments.py), 60k chars per extracted text,
  30 PDF pages, 10 DOCX tables, first 200 rows of the first spreadsheet
  sheet. Every cap that cuts content away ANNOUNCES itself inside the
  extracted text, so the model is never silently blind.

Failure contract matches MCP/sub-agents: nothing here raises into the
request path; problems become warning strings the route surfaces as a
frozen 'status' frame (📥 Inbox: …) and the model is told what it cannot see.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from pathlib import Path

INBOX_REL = Path(".orion") / "inbox"
ARCHIVE_REL = INBOX_REL / "archive"
STATE_REL = Path(".orion") / "inbox_state.json"

MAX_BYTES = 20 * 1024 * 1024
# >>> REVIEW FIX (UPDATE-018, fix b): images travel as base64 inline vision
# parts, and Gemini's documented inline limit is 7 MB per image — the same
# cap attachments.py already enforces (MAX_BYTE_GAMBAR). Without this, a
# 15 MB screenshot dropped in the inbox would poison the whole LLM call
# with a provider-side 400 instead of degrading to a per-file warning.
MAX_BYTES_GAMBAR = 7 * 1024 * 1024
MAX_CHARS = 60_000
PDF_PAGES = 30
XLSX_ROWS = 200
DOCX_TABLES = 10
DEFAULT_MAX_FILES = 5

MIME_GAMBAR = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
EKSTENSI_TEKS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".scss", ".sql",
    ".sh", ".bat", ".ps1", ".xml", ".csv", ".tsv", ".log",
}

# Soft imports: a backend without the document parsers must still boot and
# still run the inbox for text/images (same policy as todo_manager/mcp).
try:
    from pypdf import PdfReader as _PdfReader
except Exception:  # pragma: no cover - depends on env
    _PdfReader = None
try:
    from docx import Document as _DocxDocument
except Exception:  # pragma: no cover
    _DocxDocument = None
try:
    from openpyxl import load_workbook as _load_workbook
except Exception:  # pragma: no cover
    _load_workbook = None


def read_inbox_config(config: dict | None) -> tuple[bool, int]:
    """(enabled, max_files_per_turn) with clamping; missing keys = defaults."""
    config = config or {}
    enabled = bool(config.get("inbox_enabled", True))
    raw = config.get("inbox_max_files", DEFAULT_MAX_FILES)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raw = DEFAULT_MAX_FILES
    return enabled, max(1, min(20, raw))


def _cap_text(text: str, name: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return (
        text[:MAX_CHARS]
        + f"\n[⚠️ SYSTEM: content of '{name}' was truncated at {MAX_CHARS} "
        "characters; read the rest via read_file_range on the archived copy "
        "if you truly need more.]"
    )


def _extract_pdf(path: Path) -> str:
    reader = _PdfReader(str(path))
    total = len(reader.pages)
    pages = reader.pages[:PDF_PAGES]
    text = "\n".join((p.extract_text() or "") for p in pages)
    text = text.strip() or "(no extractable text found in this PDF)"
    # >>> REVIEW FIX (UPDATE-018, fix d): a silent page cut would let the
    # model believe it saw the whole document. Say what was not extracted.
    if total > PDF_PAGES:
        text += (
            f"\n[… truncated: showing the first {PDF_PAGES} of {total} pages; "
            "the remaining pages were NOT extracted]"
        )
    return text


def _extract_docx(path: Path) -> str:
    doc = _DocxDocument(str(path))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables[:DOCX_TABLES]:
        for row in table.rows[:XLSX_ROWS]:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    # >>> REVIEW FIX (UPDATE-018, fix d): same honesty rule as the PDF path —
    # capped tables/rows must be announced, never silently dropped.
    if len(doc.tables) > DOCX_TABLES:
        parts.append(
            f"[… truncated: showing the first {DOCX_TABLES} of "
            f"{len(doc.tables)} tables]"
        )
    return "\n".join(parts) or "(the document has no extractable text)"


def _extract_xlsx(path: Path) -> str:
    wb = _load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= XLSX_ROWS:
                rows.append(f"[… truncated at {XLSX_ROWS} rows of sheet '{ws.title}']")
                break
            rows.append(" | ".join("" if c is None else str(c) for c in row))
        return "\n".join(rows) or "(the first sheet is empty)"
    finally:
        wb.close()


def extract_file(path: Path) -> tuple[str, object]:
    """Classify + extract one inbox file.

    Returns ("text", str) | ("image", (mime, b64)) | ("skip", reason) |
    ("error", reason). Never raises.
    """
    suffix = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "error", f"cannot stat file: {exc}"
    if size > MAX_BYTES:
        return "skip", f"larger than {MAX_BYTES // (1024 * 1024)} MB; split it or reference it by path instead"
    if size == 0:
        return "skip", "the file is empty"
    if suffix in MIME_GAMBAR:
        # >>> REVIEW FIX (UPDATE-018, fix b): enforce the same 7 MB inline
        # cap attachments.py uses for vision parts (Gemini's documented
        # limit). The file stays in the inbox and is warned about once per
        # version, exactly like any other 'skip'.
        if size > MAX_BYTES_GAMBAR:
            return (
                "skip",
                f"image is {size / (1024 * 1024):.1f} MB, above the 7 MB "
                "inline limit of the vision API; resize/compress it first",
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return "error", f"cannot read file: {exc}"
        return "image", (MIME_GAMBAR[suffix], base64.b64encode(raw).decode())
    if suffix in EKSTENSI_TEKS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return "error", f"cannot read file: {exc}"
        return "text", _cap_text(text, path.name)
    if suffix == ".pdf":
        if _PdfReader is None:
            return "skip", "PDF parser not installed (pip install pypdf)"
        try:
            return "text", _cap_text(_extract_pdf(path), path.name)
        except Exception as exc:
            return "error", f"PDF parse failed: {type(exc).__name__}: {str(exc)[:120]}"
    if suffix == ".docx":
        if _DocxDocument is None:
            return "skip", "Word parser not installed (pip install python-docx)"
        try:
            return "text", _cap_text(_extract_docx(path), path.name)
        except Exception as exc:
            return "error", f"DOCX parse failed: {type(exc).__name__}: {str(exc)[:120]}"
    if suffix == ".xlsx":
        if _load_workbook is None:
            return "skip", "Excel parser not installed (pip install openpyxl)"
        try:
            return "text", _cap_text(_extract_xlsx(path), path.name)
        except Exception as exc:
            return "error", f"XLSX parse failed: {type(exc).__name__}: {str(exc)[:120]}"
    return "skip", f"unsupported type '{suffix or '(none)'}'; supported: text/code, png/jpg/webp, pdf, docx, xlsx"


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a lost manifest only means a file may be re-read once


def _unique_dest(archive: Path, name: str) -> Path:
    dest = archive / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return archive / f"{stem}-{stamp}{suffix}"


def ingest(project_path: str | None, config: dict | None = None) -> dict:
    """Scan the inbox once and consume what is new. Never raises."""
    out: dict = {"texts": [], "images": [], "warnings": [], "names": []}
    enabled, max_files = read_inbox_config(config)
    if not enabled or not project_path:
        return out
    inbox = Path(project_path) / INBOX_REL
    if not inbox.is_dir():
        return out
    state_path = Path(project_path) / STATE_REL
    state = _load_state(state_path)

    try:
        candidates = sorted(
            (p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
    except OSError as exc:
        out["warnings"].append(f"cannot read the inbox folder: {exc}")
        return out

    fresh: list = []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        rec = state.get(path.name)
        if isinstance(rec, dict) and rec.get("size") == stat.st_size:
            # >>> REVIEW FIX (UPDATE-018, fix c): a corrupt-but-valid-JSON
            # manifest (e.g. {"mtime": "abc"}) used to raise inside float()
            # and break the never-raise contract. An unparsable mtime now
            # simply counts as "a new version" — the file is processed once
            # more, which is the safe direction.
            try:
                rec_mtime = float(rec.get("mtime", -1))
            except (TypeError, ValueError):
                rec_mtime = -1.0
            if abs(rec_mtime - stat.st_mtime) < 1e-6:
                continue  # already processed in this exact version
        fresh.append((path, stat))
    if not fresh:
        return out

    taken, rest = fresh[:max_files], fresh[max_files:]
    if rest:
        out["warnings"].append(
            f"{len(rest)} inbox file(s) kept for later turns "
            f"(inbox_max_files={max_files} per turn)"
        )

    archive = Path(project_path) / ARCHIVE_REL
    for path, stat in taken:
        kind, payload = extract_file(path)
        out["names"].append(path.name)
        if kind == "text":
            out["texts"].append((path.name, payload))
        elif kind == "image":
            mime, b64 = payload
            out["images"].append((path.name, mime, b64))
        elif kind == "error":
            out["warnings"].append(f"inbox file '{path.name}' failed to parse: {payload}")
        else:
            out["warnings"].append(f"inbox file '{path.name}' skipped: {payload}")
        if kind in ("text", "image", "error"):
            try:
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(_unique_dest(archive, path.name)))
            except OSError as exc:
                out["warnings"].append(f"could not archive '{path.name}': {exc}")
        state[path.name] = {"size": stat.st_size, "mtime": stat.st_mtime}
    _save_state(state_path, state)
    return out


def describe(result: dict) -> str:
    """One line for the frozen 'status' frame; empty when nothing arrived."""
    names = result.get("names") or []
    if not names:
        return ""
    shown = ", ".join(names[:6])
    lebih = f" +{len(names) - 6}" if len(names) > 6 else ""
    return f"Inbox: {len(names)} new file(s) this turn: {shown}{lebih}"


def context_block(result: dict) -> str:
    """The labelled text block appended to this turn's context message."""
    texts = result.get("texts") or []
    images = result.get("images") or []
    warns = result.get("warnings") or []
    # >>> REVIEW FIX (UPDATE-018, fix a): warnings must reach the model even
    # when NO text could be extracted (e.g. every dropped file failed to
    # parse). The spec promises "the model is told what it cannot see"; the
    # previous early-return on empty texts silently dropped exactly that.
    if not texts and not images and not warns:
        return ""
    sections = []
    for name, text in texts:
        sections.append(f"### INBOX FILE: {name}\n{text}")
    for name, mime, _b64 in images:
        sections.append(
            f"### INBOX IMAGE: {name} ({mime}) — attached to this turn's "
            "request as a vision part; visible THIS TURN ONLY"
        )
    if warns:
        sections.append(
            "[INBOX WARNINGS — things you CANNOT see; tell the user about "
            "them instead of guessing] " + " | ".join(warns)
        )
    return (
        "\n\n## NEW INBOX FILES (the user dropped these into .orion/inbox; "
        "they are visible to you THIS TURN ONLY; archived copies remain on "
        "disk under .orion/inbox/archive/):\n" + "\n\n".join(sections)
    )
