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
| Structure | Phase C complete: `main.py` reduced 2,769 → 569 lines (HTTP routing + composition only); the agent loop (`agent_loop.py`), per-turn state (`session_state.py`), end-of-iteration gates (`loop_guards.py`), SSE builders (`sse.py`), schema, prompts and pure-tool dispatch each live in their own tested module |
| Auth | shared-secret token (`X-Orion-Token`) between extension and backend; every route except the health probe requires it |
| Language | entire code surface in English — UI/status strings, tool results, logs, comments, system prompt (Phase D); chat replies still follow the user's language by design, and claim-detection keywords stay bilingual on purpose |

## Next, in order

1. **Semantic search** (embeddings) for large codebases — grep stops being enough
   past a certain size. (Cheaper alternative to evaluate first: a community
   MCP search server through the UPDATE-014 foundation.)
2. **Parallel sub-agents: shipped** (UPDATE-016, backend 0.8.0 — Explorer,
   Verifier, disjoint-scope Workers; see `docs/SUBAGENTS.md`). With this the
   MCP phase (014 foundation → 015 config UI → 016 delegation) is COMPLETE;
   the remaining step is the **v0.1.0 release** (tag + GitHub Release +
   Marketplace publish of the extension), held until the owner's E2E on
   UPDATE-016 passes.
3. **Standalone client (vision, post-v0.10-stable):** the backend is the
   brain (HTTP + the 7-event SSE contract); the VS Code extension is only
   one client. A TUI/WebUI client in the style of claudecode/opencode is
   therefore a new FRONTEND over the same API, not a rewrite — what it must
   replace is the editor's senses (active file, selection, diagnostics) and
   the approval modals. Deliberately scheduled AFTER v0.1.0 plus a stability
   window; not scope for 015/016.
4. Manual VS Code pass of FASE_C_PLAN section 6 scenarios c/d (broken file →
   `verify_code` cited; Cancel mid-generation) before the next release.
5. Marketplace submission prep: PNG icon (≥128px), publisher account, first
   GitHub Release with the VSIX attached.

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
