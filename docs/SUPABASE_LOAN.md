# SUPABASE LOAN — per-project, least-privilege, revocable (UPDATE-024)

Orion does not *integrate* with Supabase; it **borrows** it, per project,
for as long as the project needs it. This document is the contract.

## The loan model

| Property | Rule |
|---|---|
| Scope | ONE user project at a time, via that project's `.orion/mcp.json`. No entry = no loan. |
| Lifecycle | The MCP manager starts the server on `/set-project` and stops it on project switch / backend shutdown — moving to another project revokes the loan automatically. |
| Manual kill-switch | master switch (`mcp_enabled`), per-server Disable in the MCP panel, or delete the entry. Re-enable = flip back. No code changes, ever. |
| Secrets | ONLY in the `env` section of the project's `.orion/mcp.json` (or the OS user's files). NEVER in chat, repo, commits, or logs. `backend/.env` stays reserved for global LLM provider keys. |
| Failure contract | Dead server / timeout / bad config arrive as `[MCP ...]` tool-result strings; Orion treats them as facts and continues with built-in tools. A loan being off is never an error state. |

## The two flavours

**Flavour A — observe (default for trials and audits).**
Official Supabase MCP server started with `--read-only` and
`--project-ref <trial-project>`. Orion can inspect schema and data, run
read queries, and feed explorer/verifier sub-agents — and cannot change
anything, no matter which tool it calls or which prompt injection it
swallows.

**Flavour B — guarded build.**
Same server, same `--project-ref`, WITHOUT `--read-only`, used only while
Orion builds/migrates the trial app. Guards that make this acceptable:
1. the referenced Supabase project is DISPOSABLE (trial data only; never
   a production project);
2. Row-Level Security enabled on every table from the first migration;
3. the Supabase `service_role` key NEVER enters `mcp.json` — the manager
   warns at startup when any server env value is shaped like a
   service-role JWT (UPDATE-024 Commit 5) and the loan should be rebuilt
   with a personal access token instead;
4. every write Orion makes still passes the ordinary honesty stack:
   edit ledger, per-file disk evidence, verifier delegation before
   completion claims.

Promoting a trial to a real product is a HUMAN decision with a human
credential rotation: new project, new keys, RLS policies reviewed, loan
re-established deliberately. Orion never self-promotes a loan.

## Trial runbook (owner)

1. Create a disposable Supabase project (free tier is fine; remember its
   7-day inactivity pause — acceptable for trials, not for products).
2. Dashboard → Access Tokens → create a personal access token.
3. Copy `backend/examples/mcp.supabase.template.json` to
   `<project>/.orion/mcp.json`; replace the three placeholders; keep
   `--read-only` for flavour A, remove it for flavour B.
4. In VS Code set that folder as Orion's project; MCP panel must show
   `supabase` status ok with its tool list.
5. First loan test (flavour A): ask Orion "list the tables and their row
   counts" — expect real schema answers; then ask it to delete a table —
   expect refusal-by-server (read-only) and an honest report.
6. When done: Disable in the MCP panel or delete `mcp.json`. The loan is
   off; Orion keeps working with built-in tools.

## Who guarantees what

| Guarantee | Held by |
|---|---|
| read-only enforcement, project scoping | Supabase server flags (config) |
| key shape warning (service-role detection) | `mcp_manager` startup warning (code) |
| keys never logged / never in VSIX / never in chat | auth & env-privacy posture + `.vscodeignore` (code, pre-existing) |
| loan revocable in one click / one file edit | MCP master switch & per-project config (code, pre-existing) |
| Orion honesty while using the loan | ledger / evidence / verifier gates (code, 022–023) |
| choosing flavour, project, and promotion | the owner (process) |
