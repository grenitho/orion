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
| Reliability | every run ends with exactly one terminal event; a wrapper guarantees `done`; 77 automated tests, several born from real production incidents |
| Structure | `main.py` reduced 2,769 → 2,115 lines; schema, prompts and pure-tool dispatch extracted to their own modules |

## Next, in order

1. **Phase C — extract the agent loop** (`docs/FASE_C_PLAN.md`). Four separately
   committed steps: `sse.py`, `session_state.py`, `loop_guards.py`, `agent_loop.py`.
   Pure maintainability work; no behaviour change intended.
2. **Shared-secret token** between extension and backend (last item promised in
   `SECURITY.md`).
3. **Semantic search** (embeddings) for large codebases — grep stops being enough
   past a certain size.
4. **MCP tool servers**, then **parallel sub-agents** (the pattern Google recommends
   for Flash-Lite class models).

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
