# Development Roadmap

Canonical, English, up-to-date. Supersedes the numbered Indonesian build logs in
this folder (see `docs/README.md` for their status) and the removed
`ROADMAP_ORION.md` / `PANDUAN_FITUR_ATTACHMENT.md`.

For the agent-facing context document (the one Orion itself reads from
`.orion/`), see `STATUS_ORION.md`; for the refactor plan, see `FASE_C_PLAN.md`.

---

## Shipped and guarded by tests

| Area | What exists today |
|---|---|
| Honesty | claim-verification gate (a "done" claim with zero changed files is rejected and challenged); anti-hallucination rules in the system prompt; `.orion/*.md` treated as claims, never as facts |
| Eyes | VS Code diagnostics + active file + selection sent with every request; `verify_code` (16 file kinds, real parsers/compilers); `run_tests` with parsed results |
| Multi-task | explicit todo list owned by the backend; loop continues while items remain pending |
| Resilience | 3-tier LLM router with key rotation, backoff + jitter, `reasoning_effort` mapped to Gemini thinking levels; heartbeat status while the model thinks |
| Safety | path-traversal guard, confirmation modals, destructive/remote git blocked at code level, automatic `.bak` before overwrite, reversible restores, SSRF-guarded `web_fetch`, closed CORS, loopback bind |
| Reliability | every run ends with exactly one terminal event; a wrapper guarantees `done`; 180 automated tests, several born from real production incidents |
| Extensibility | MCP tool-server foundation (backend 0.6.0, UPDATE-014): local MCP servers via `<project>/.orion/mcp.json`, namespaced `mcp__<server>__<tool>` tools, bounded calls with one transparent reconnect, every failure degraded to an informative tool-result string — see `docs/MCP_FOUNDATION.md`. Configuration UI shipped with UPDATE-015 (backend 0.7.0 / extension 0.0.11): MCP Servers panel + optional wizard card + token-guarded `/mcp/status` & `/mcp/config` |
| Delegation | internal sub-agents (backend 0.8.0, UPDATE-016): Explorer (read-only survey), Verifier (real-tool critic), parallel Workers with disjoint write scopes checked before spawn and per write; depth 1, in-memory histories, per-turn and per-run budgets in `.orion/orion_config.json`, failures as `[SUBAGENT ...]` strings — see `docs/SUBAGENTS.md` |
| Inbox & profiles | the ambient inbox drop-zone + per-role sub-agent model profiles (backend 0.9.0 / extension 0.2.0, UPDATE-018): `.orion/inbox/` ingested per chat turn (text/code, images, pdf/docx/xlsx via soft-imported parsers) under announced hard caps, archive-never-delete with a name+size+mtime manifest, `📥` status frame, warnings injected so the model knows what it cannot see; `subagent_models` pins one existing-credential model per role with router-level fallback, managed by the token-guarded `/models/*` routes and the wizard's "Sub-agent models" card — see `docs/INBOX_AND_PROFILES.md` |
| Structure | Phase C complete: `main.py` reduced 2,769 → 569 lines (HTTP routing + composition only); the agent loop (`agent_loop.py`), per-turn state (`session_state.py`), end-of-iteration gates (`loop_guards.py`), SSE builders (`sse.py`), schema, prompts and pure-tool dispatch each live in their own tested module |
| Auth | shared-secret token (`X-Orion-Token`) between extension and backend; every route except the health probe requires it |
| Language | entire code surface in English — UI/status strings, tool results, logs, comments, system prompt (Phase D); chat replies still follow the user's language by design, and claim-detection keywords stay bilingual on purpose |

## Next, in order

> Renumbered by the owner's approved UPDATE-018 scope (2026-10-23). v0.1.0
> (UPDATE-016) and v0.1.1 (UPDATE-017) are released; the Marketplace lists
> 0.1.0; UPDATE-018 ships as backend 0.9.0 / extension 0.2.0 (tag v0.2.0).

1. **UPDATE-019 — defects & forensics**: `validate_project_structure` must
   ignore `node_modules`/`__pycache__` (the duplicate-assets warning fired
   again on the owner's set-project output in 017), plus the "twin process"
   forensics from incident 013. Owner principle: product defects outrank
   new features.
2. **UPDATE-020 — UX**: a webview **attach button** in the chat panel that
   forwards into the same single-turn pipeline the inbox already uses (the
   forwarding code has been dormant since the attachment feature), plus the
   onboarding backlog (installer skip-Gemini path, empty-ENTER handling,
   v0.0.9 translation leftovers, master-switch button in the MCP panel,
   version-mismatch toast).
3. **UPDATE-021 — borrowed-stack trial** (optionally parallel): SQLite +
   a copy of the client's PocketBase ERP database; then Supabase via
   postgres; n8n-mcp; playwright for the content-creator analytics loop.
4. **UPDATE-022 — standalone client (vision, post-1.0):** the backend is
   the brain (HTTP + the 7-event SSE contract); the VS Code extension is
   only one client. A TUI/WebUI client in the style of claudecode/opencode
   is therefore a new FRONTEND over the same API, not a rewrite — what it
   must replace is the editor's senses (active file, selection,
   diagnostics) and the approval modals.
5. Backlog (unscheduled): **semantic search** (embeddings) for large
   codebases — grep stops being enough past a certain size; a community MCP
   search server through the UPDATE-014 foundation is the cheaper first
   evaluation.

**Phase C — extract the agent loop: shipped** (four commits, `d8af462` →
`4ee5f46`, one per step per `docs/FASE_C_PLAN.md`; SSE baselines byte-identical
across every step; behaviour deliberately unchanged, two temptations recorded
in the plan's Backlog instead of fixed).

**Phase D — internationalization: shipped** (executed right after Phase C step 1).
User-facing strings, tool results, logs, comments and the system prompt are now
English, in six reviewable commits ending with the prompt commit `0da6394`
(before/after comparison inside the commit message; SSE baseline byte-identical;
prompt shrank 7,003 → 5,275 cl100k tokens). Details and the deliberate
exceptions (bilingual claim keywords, Indonesian identifiers, untranslated
historical docs) are recorded at the bottom of `docs/FASE_C_PLAN.md`.

## Deliberately not planned

- Process sandboxing (container/microVM): Orion is a personal, local tool; the
  approval modal + backups are the chosen trade-off. Documented in `SECURITY.md`.
- Multi-user / auth / database: out of scope for a single-machine assistant.
- Token streaming from the LLM: rejected on purpose — assembling tool calls from
  token streams is far more fragile than the current heartbeat approach.

## Housekeeping (open, cosmetic)

- Add GitHub **topics** to the repository (About → gear icon).
- Translate or retire the remaining Indonesian historical logs if the project
  ever gains non-Indonesian contributors; until then `docs/README.md` labels them.
