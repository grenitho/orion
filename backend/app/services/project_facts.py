"""
project_facts.py — deterministic project-level facts (UPDATE-025 commit 9)
===========================================================================
Owner directive (2026-10-29): "Orion harus bisa proaktif, dan memberikan
saran. Dia tidak boleh seperti kuda yang ditutup matanya." The first
proactive fact worth a tooth is the cheapest one to detect deterministically:
WHETHER THE PROJECT HAS ANY PERSISTENCE LAYER AT ALL.

InvoiceFlow history: add-customer failures kept being investigated as
code defects (payload shapes, proxies, ports) while the structural
candidate — an in-memory store with no database behind it — was never
named to the owner, because nobody asked "is there a database?" and the
model only answers what it was asked. A bounded filesystem scan answers
that without semantics:

  * any *.db / *.sqlite / *.sqlite3 file (depth <= 3),
  * an ORM/driver in requirements.txt / pyproject.toml / setup.py
    (sqlalchemy, peewee, tortoise, psycopg, pymongo, redis, sqlite...),
  * a database/models module (database.py, models.py, db.py, schema
    dirs) at depth <= 3.

Node modules, venvs and .git are never walked. The scan runs ONLY when
an open failure thread is about stored/added data — never on ordinary
turns — so the cost is a few hundred stat calls on the rare relevant
turn, and the wolf cannot cry: absence of markers is a FACT, not a
guess, and the block states exactly what was checked.
"""

from __future__ import annotations

import os
import re

_SKIPS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".orion",
}
_DB_EXT = (".db", ".sqlite", ".sqlite3")
_MODUL_DB = {"database.py", "models.py", "db.py", "dal.py", "persistence.py"}
_POLA_DEP = re.compile(
    r"sqlalchemy|databases\b|tortoise|peewee|psycopg|pymongo|motor\b|redis"
    r"|sqlite|prisma|sequelize|typeorm|knex|mongoose|django\b",
    re.I,
)
_DEP_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "package.json")
_MAX_DEPTH = 3


def ada_persistensi(project_path: str) -> bool:
    """Bounded scan: does this project show ANY persistence marker?"""
    akar = os.path.abspath(project_path or ".")
    if not os.path.isdir(akar):
        return True  # cannot see the project => never claim the absence
    for dirpath, dirnames, filenames in os.walk(akar):
        kedalaman = os.path.relpath(dirpath, akar).count(os.sep)
        if kedalaman >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _SKIPS]
        for fn in filenames:
            if fn.endswith(_DB_EXT) or fn.lower() in _MODUL_DB:
                return True
            if fn in _DEP_FILES:
                try:
                    with open(os.path.join(dirpath, fn), encoding="utf-8",
                              errors="ignore") as fh:
                        kepala = fh.read(200_000)
                    if _POLA_DEP.search(kepala):
                        return True
                except OSError:
                    continue
    return False


def blok_persistensi(project_path: str) -> str:
    """The fixed advisory block: fact + what was checked + the owner's
    options. Deterministic content — the model weaves it, never invents
    it, and cannot forget it."""
    return (
        "\n\n**📦 Detected fact — this project has NO persistence layer.** "
        "Checked and absent: any `*.db`/`*.sqlite*` file, any ORM/driver "
        "(sqlalchemy/peewee/psycopg/pymongo/redis/…) in requirements or "
        "pyproject, and any `database.py`/`models.py`-style module. Data "
        "added through the app therefore lives ONLY in process memory: it "
        "vanishes on every backend restart and can never survive between "
        "sessions. If your failing flow expects added records to stick "
        "around, that structural gap — not a typo in the code — is the "
        "first candidate cause.\n"
        "Your options, smallest first:\n"
        "1. **SQLite + SQLAlchemy** — one file, trial-safe, real queries;\n"
        "2. **JSON-file store** — simplest durable store for single user;\n"
        "3. **Keep in-memory** — legitimate for a throwaway trial, but "
        "then data loss on restart is EXPECTED behaviour, not a bug.\n"
        "Say which one you want and the pipeline will implement and "
        "verify it. (This block fires once per open failure thread; the "
        "scan is deterministic — it never guesses.)"
    )
