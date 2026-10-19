"""
sse.py — SSE event builders (Phase C step 1)
=============================================
Before this module existed, 47 sites inside the /chat endpoint assembled SSE
frames inline: f"data: {json.dumps({'type': ...})}\\n\\n". The frame format and
the seven event names inside it are a FROZEN CONTRACT with the extension
(FASE_C_PLAN.md section 1) — ChatPanel._processStreamData handles exactly
these seven names, and the "exactly one done per run" guarantee is held by
stream_utils.jamin_done.

ev() produces frames that are BYTE-IDENTICAL to the old inline shape ('type'
always first, same json.dumps defaults), so moving them is a pure call-shape
change with no behaviour change. The tests live in test_sse.py; the frozen
names are also guarded there by an AST check (manual frames must never
reappear).
"""

from __future__ import annotations

import json

# ============================================================
# FROZEN CONTRACT — the seven event names (FASE_C_PLAN.md section 1).
# Do NOT rename/add/remove without a deliberate, separate matching
# change in orion/src/panels/ChatPanel.ts (_processStreamData).
# ============================================================
EVENT_STATUS = "status"                      # activity log line / progress
EVENT_TOKEN = "token"                        # answer text for the assistant bubble
EVENT_ASK_USER = "ask_user"                  # stop and ask the user
EVENT_CONFIRM_TERMINAL = "confirm_terminal"  # command approval modal
EVENT_TODO_UPDATE = "todo_update"            # todo checklist
EVENT_ERROR = "error"                        # error bubble
EVENT_DONE = "done"                          # run finished normally

FROZEN_EVENTS = (
    EVENT_STATUS,
    EVENT_TOKEN,
    EVENT_ASK_USER,
    EVENT_CONFIRM_TERMINAL,
    EVENT_TODO_UPDATE,
    EVENT_ERROR,
    EVENT_DONE,
)


def ev(event_type: str, **payload) -> str:
    """Assemble one SSE frame: 'data: {"type": <event_type>, ...payload}\\n\\n'.

    'type' is always the FIRST key — same as the old inline shape, so
    json.dumps (which preserves dict insertion order) yields identical bytes.
    The caller's kwarg order determines the order of the remaining payload
    keys, exactly like the key order in the dict literal it replaced.
    """
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"
