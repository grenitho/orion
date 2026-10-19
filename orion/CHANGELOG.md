# Change Log

All notable changes to the Orion extension are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.0.5] - 2026-10-19

### Added
- Full installation & first-run guide in this readme, plus a troubleshooting
  table.
- Marketplace metadata: publisher, repository, license, keywords, categories
  and gallery banner; `LICENSE` now ships inside the VSIX.

### Changed
- The entire user interface is English: chat webview, activity view,
  performance dashboard, notifications, backend status stream and tool
  result messages. **Chat replies still follow the language you write in** —
  that is deliberate and now guaranteed by an explicit rule in the system
  prompt.
- System prompt and all 22 tool descriptions translated to English; claim
  detection keywords intentionally stay bilingual (Indonesian + English) so
  honesty guards keep working for Indonesian conversations.

### Internal (no behaviour change)
- Phase C refactor completed: SSE frame builders (`sse.py`), per-turn state
  (`session_state.py`), end-of-iteration decision (`loop_guards.py`) and the
  agent loop itself (`agent_loop.py`) extracted from `main.py`
  (2,769 → 569 lines). Verified by byte-identical SSE baseline recordings
  and 97 automated tests.

## [0.0.1 – 0.0.4]

- Agentic loop with todo tracking, terminal approval modal, post-edit
  verification, 3-tier LLM router with heartbeat, project intelligence
  (`.orion/` context + dependency map), performance dashboard, cancel
  button, image & text attachments. Build history lives in `docs/`.
