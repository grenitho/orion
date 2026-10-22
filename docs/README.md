# About this folder

`docs/` is **project memory, not user documentation**.

## Canonical, user-facing documentation (English)

Lives at the repository root and in this folder:

| File | Purpose |
|---|---|
| `/README.md` | what Orion is, quick start, configuration, limitations |
| `/SECURITY.md` | threat model, what Orion protects against and what it deliberately does not |
| `MCP_FOUNDATION.md` | MCP tool-server foundation (backend 0.6.0+): configuration reference, failure contract, troubleshooting |
| `PANDUAN_SETUP.md` | **end-user setup guide, Indonesian on purpose** (primary audience: first-time non-developer users): the one-line Windows installer, the manual route, the upcoming in-extension wizard, and multi-provider LLM configuration |
| `DEVELOPMENT_ROADMAP.md` | current state, what is next, what is deliberately not planned |
| `FASE_C_PLAN.md` | execution plan for extracting the agent loop out of `main.py` |

## Internal / agent-context documents (Indonesian)

| File | Purpose |
|---|---|
| `STATUS_ORION.md` | the document **Orion itself reads** from `.orion/` at the start of work. Written in Indonesian on purpose: it is machine context for the maintainer's language, not user docs. Kept in sync with every release |

## Historical build logs (Indonesian, internal)

Files numbered `01_` … `23_` are the original session-by-session build journal.
They are kept as history — how Orion was actually constructed, including the
dead ends. They are **not** maintained, **not** translated, and frequently
**out of date**. Never treat them as documentation of current behaviour;
for that, use the canonical files above.

Two of them (`21_Orion_Pending_Roadmap.md`, `22_Orion_Current_Checkpoint.md`)
were **deleted** on purpose: they listed finished work as "pending", and Orion
was observed re-reading them and acting on the stale claims. If you ever see a
numbered log contradicting `STATUS_ORION.md`, the log is wrong.

## Removed documents

- `ROADMAP_ORION.md` — superseded by `DEVELOPMENT_ROADMAP.md`
- `PANDUAN_FITUR_ATTACHMENT.md` — described an attachment UI that was removed
  (ORION-22); the dormant backend support is noted in `STATUS_ORION.md`
