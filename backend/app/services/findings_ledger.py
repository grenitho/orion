"""
findings_ledger.py — conversation memory that survives pruning (UPDATE-024 Commit 6)
======================================================================================
Owner field evidence (2026-10-27, InvoiceFlow): an explorer report with
three concrete bug findings vanished from the model's context on the NEXT
turn — long tool results are placeholder-pruned (agent_loop LAYER 1) and
old turns are summarize-compressed — so Orion denied having any record of
findings it had produced one message earlier, then searched the BLUEPRINT
on disk for them ("tidak ada catatan di blueprint baris 369").

The ledger fixes the class, not the instance: durable, per-project
findings stored OUTSIDE the prunable history and re-injected into every
turn's context message (the same mechanism the inbox block uses since
UPDATE-018 — present every turn, never summarized away).

Design:
  * entries: source (explorer/verifier/worker), timestamp, condensed text;
    capped (MAX_ENTRIES, oldest dropped) and per-entry capped (MAX_CHARS)
    so the ledger can never become the token hog it exists to defeat;
  * the context block shows the last MAX_BLOCK entries CONDENSED and tells
    the model where the full texts live (.orion/findings.json, readable
    with read_json_path) — retrieval on demand instead of flooding;
  * empty ledger -> empty block -> streams stay byte-identical (harness).
"""

from __future__ import annotations

import json
import os
import time

MAX_ENTRIES = 12       # stored generations
MAX_CHARS = 1500       # per entry
MAX_BLOCK = 8          # entries shown per turn
MAX_BLOCK_CHARS = 300  # per entry in the block

REL_PATH = os.path.join(".orion", "findings.json")


def _path(project_path: str) -> str:
    return os.path.join(project_path, REL_PATH)


def _load(project_path: str) -> list:
    try:
        with open(_path(project_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def simpan_temuan(project_path: str, sumber: str, isi: str) -> None:
    """Append one finding; cap entries and per-entry length; never raises
    (a broken ledger must degrade to absence, not crash a delegation)."""
    try:
        teks = str(isi or "").strip()
        if not teks:
            return
        entri = {
            "id": int(time.time() * 1000),
            "sumber": str(sumber or "unknown"),
            "waktu": time.strftime("%Y-%m-%d %H:%M"),
            "isi": teks[:MAX_CHARS],
        }
        daftar = _load(project_path)
        daftar.append(entri)
        os.makedirs(os.path.dirname(_path(project_path)), exist_ok=True)
        tmp = _path(project_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daftar[-MAX_ENTRIES:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(project_path))
    except OSError:
        pass


def blok_konteks(project_path: str) -> str:
    """The per-turn context block; '' when the ledger is empty so streams
    of projects without findings stay byte-identical to pre-024 shape."""
    daftar = _load(project_path)
    if not daftar:
        return ""
    baris = []
    for entri in daftar[-MAX_BLOCK:]:
        potongan = str(entri.get("isi", ""))[:MAX_BLOCK_CHARS].replace("\n", " ")
        if len(str(entri.get("isi", ""))) > MAX_BLOCK_CHARS:
            potongan += " …"
        baris.append(
            f"- [{entri.get('sumber', '?')} • {entri.get('waktu', '?')}] {potongan}"
        )
    return (
        "\n\n## FINDINGS LEDGER — conversation memory that survives pruning:\n"
        + "\n".join(baris)
        + f"\nFull texts live in {REL_PATH} — read_json_path it when you need "
        "the complete wording of an entry. Findings listed here were produced "
        "IN THIS PROJECT'S CONVERSATION; they are as real as anything on disk."
    )
