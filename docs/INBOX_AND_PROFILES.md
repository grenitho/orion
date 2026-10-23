# Inbox & Model Profiles (UPDATE-018)

Backend **0.9.0** · extension **0.2.0**. Two independent features that share
one philosophy: *ambient, bounded, and honest* — the model always knows what
it can and cannot see, every failure degrades to a warning string, and the
frozen 7-event SSE contract is untouched.

---

## Part A — the inbox drop-zone

The VS Code chat panel has no attachment UI, and walking a beginner through
base64 pasting is cruel. So Orion grows an **ambient inbox**: drop files into

```
<project>/.orion/inbox/
```

and the **next chat turn** picks them up — no button, no drag-and-drop, no
terminal. Works with any client that can POST `/chat`.

### What happens on each `/chat` turn

1. `inbox_manager.ingest()` scans the folder (alphabetical order, hidden
   files ignored) and compares each file against the manifest
   `.orion/inbox_state.json` (**name + size + mtime**). Only *new versions*
   are processed, so an unsupported file is warned about **exactly once**,
   not on every turn.
2. Up to `inbox_max_files` files (default **5**, clamped 1..20) are consumed
   per turn; the rest are announced as *kept for later turns*.
3. Each consumed file is classified and extracted:

   | Family | Treatment |
   |---|---|
   | text/code (`.txt .md .csv .json .py .ts .js .html .css .sql .sh .ps1 .yaml .toml .ini .log …`) | read as UTF-8 (errors replaced), capped at **60 000 chars** |
   | images (`.png .jpg .jpeg .webp`) | base64 **vision parts** in the exact format `attachments.py` uses (`image_url` + data URI), injected on the first iteration only — never persisted |
   | `.pdf` | text via **pypdf**, first **30 pages** |
   | `.docx` | paragraphs + first **10 tables** × 200 rows via **python-docx** |
   | `.xlsx` | first sheet, first **200 rows** via **openpyxl** |

   Hard caps: **20 MB per file**, **7 MB per image** (Gemini's inline vision
   limit — the same cap the attachment pipeline enforces). The parsers are
   **soft imports**: a backend without pypdf/python-docx/openpyxl still
   boots and still runs the inbox for text/images; the missing parser only
   downgrades that one file to a warning (`requirements.txt` declares all
   three, so a normal install has them).
4. **Archive, never delete**: every processed *or broken* file is MOVED to
   `.orion/inbox/archive/` (name collisions get a timestamp suffix).
   Unsupported/oversized files STAY in the inbox. User data is sacred.
5. Extracted texts ride this turn's **context message** as labelled blocks
   (`### INBOX FILE: name`) under a header that tells the model the files
   are visible **THIS TURN ONLY** and where the archived copies live. They
   are present on every iteration of the turn but never enter
   `session_history.json` — the history only stores one
   `[INBOX FILE: name — processed this turn; archived copy under …]`
   placeholder per file, so later turns know the files arrived.
6. Visibility: one frozen `status` frame per turn —
   `📥 Inbox: N new file(s) this turn: names…` — and nothing else. Texts,
   warnings and images never add new SSE event types.

### Honesty contract

Nothing in the inbox path raises into the request (same contract as MCP and
sub-agents): unreadable folders, corrupt manifests, failing parsers, failing
archive moves — all become **warning strings**. Warnings are injected into
the context block under `[INBOX WARNINGS — things you CANNOT see; tell the
user about them instead of guessing]`, **even when no text could be
extracted at all**. Every cap that cuts content away announces itself
inside the extracted text (truncation notes for chars/pages/tables/rows),
so the model is never silently blind.

### Configuration (`.orion/orion_config.json`)

| Knob | Default | Meaning |
|---|---|---|
| `inbox_enabled` | `true` | master switch; `false` = the folder is never scanned |
| `inbox_max_files` | `5` | files consumed per turn, clamped 1..20 |

The hard caps (20 MB / 7 MB / 60k chars / 30 pages / 10 tables / 200 rows)
live in `inbox_manager.py` on purpose: they are safety rails, not
preferences.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No `📥` frame after dropping a file | The file was already processed in this exact version (see `.orion/inbox_state.json`) — modify it or drop a new name; or `inbox_enabled` is `false`; or the drop happened *after* the turn started (ingest runs once per turn) |
| `skipped: unsupported type` | Only the families above are supported; the file stays in the inbox and the warning repeats only when the file changes |
| `PDF parser not installed` | `pip install -r backend/requirements.txt` inside the backend venv, then restart the backend |
| Same file re-read every turn | Archive move failed (warning says `could not archive …`); the manifest still records the version, so at most the warning repeats when the file changes — check permissions on `.orion/inbox/archive/` |
| Image rejected with a 7 MB message | Gemini's inline limit; resize/compress, or reference the file by path and let Orion `read_file` it |

---

## Part B — per-role sub-agent model profiles

By default every sub-agent (Explorer / Verifier / Workers) uses the **main
router** — the full 4-tier queue. UPDATE-018 makes that pin-able per role,
for the two natural money-savers: a cheap model for exploration, a strong
model for verification.

### Configuration

`.orion/orion_config.json` (written by the wizard, or by hand):

```json
{
  "subagent_models": {
    "explorer": { "group": "provider_2", "model": "deepseek/deepseek-chat" },
    "verifier": { "group": "gemini",     "model": "gemini/gemini-3.5-flash" },
    "workers":  null
  }
}
```

* roles are exactly `explorer`, `verifier`, `workers` (all parallel workers
  of one delegation share the `workers` profile);
* `null` / absent / `{}` = **main router** — the empty state changes nothing
  (owner requirement);
* `group` is one of `gemini`, `provider_<N>` (the `PROVIDER_<N>_*` env
  groups), `fallback` (the legacy `FALLBACK_*` endpoint);
* `model` follows the router's prefix rules (`/` verbatim, otherwise the
  group's default prefix — `gemini/` for gemini, `openai/` for
  provider/fallback).

**No new secrets, ever.** A profile is a *single-target queue* assembled
from credentials that already exist in the backend environment: gemini
profiles rotate the group's existing N keys; provider/fallback profiles
reuse that group's key, base URL and temperature. A broken profile
(malformed shape, unknown group, group without credentials) **falls back to
the main router with a logged warning** — it can never fail a sub-run.

### The `/models/*` endpoints (token-guarded like every route)

| Route | Purpose |
|---|---|
| `GET /models/options` | credential groups + model names derived from the environment (**never key values**), plus the profiles currently saved on disk |
| `POST /models/test` | a real one-shot probe (`"Reply with the single word: pong"`) through the profile path; returns `{success, resolved_model, reply_preview}` or an honest, credential-scrubbed, truncated error |
| `POST /models/config` | validate → **atomic write** of `subagent_models` into `orion_config.json` (other keys preserved) → live sync into the running config; invalid payloads are rejected 400 *before* the file is touched |

### The wizard card (extension 0.2.0)

Setup Wizard → optional card **“Sub-agent models”** (below the MCP card):

1. **Load model options** — fills one dropdown per role from
   `/models/options`; the first choice is always *Main router (default)*.
   A saved profile whose group vanished from the environment stays visible,
   marked *(saved; not in current env)*, so Save cannot silently drop it.
2. **Test** (per role) — runs the `/models/test` probe and shows the
   resolved model + reply preview (or the honest error).
3. **Save profiles** — one `/models/config` transaction; applies to the
   **next chat turn**, no backend restart needed.

As everywhere in the wizard, the webview is a thin UI: every HTTP call
(with the shared token) runs in the extension host.

### Seeing which model answered

* backend log: `[LLM_ROUTER] Sub-agent profile active: group=… model=…`
  (and the `[WARNING] … falling back to the main router` line when a
  profile is broken);
* `[SUBAGENT <role>] model profile pinned: …` per delegation;
* the sub-agent report header (`[SUBAGENT REPORT • role • N iterations …]`)
  is unchanged — profiles are a routing concern, not a protocol change.

---

## Gates that shipped with UPDATE-018

* pytest **292 passed, 0 warnings** (211 + 81 new: inbox manager, router
  profiles, sub-agent plumbing, `/models/*` endpoints);
* ruff (F, E9, B) clean · `compileall` · `tsc --noEmit` · `eslint` ·
  `vsce package` clean;
* SSE harness: `u018base` recorded at `ee33ff0`; after all commits the
  **15 pre-existing scenarios are byte-identical**, the new deterministic
  scenario `inbox_txt` (one txt file in a temp project's inbox →
  `📥` status frame + normal answer) is byte-stable across runs;
* `git am --3way` of the five update patches onto a clean `ee33ff0` clone
  reproduces the sandbox tree exactly.
