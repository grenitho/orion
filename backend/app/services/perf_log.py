"""
perf_log.py — the performance_log.jsonl writer (UPDATE-024 Commit 1)
=====================================================================
Moved VERBATIM out of agent_loop.py (where it lived since Phase C) so the
sub-agent manager can record its own LLM calls without a circular import
(agent_loop imports subagent_manager; the reverse must never happen).

UPDATE-024 Commit 1 adds ONE additive field: ``role``. Main-loop calls keep
writing exactly the old entry shape (role absent); sub-agent calls tag
themselves ("explorer" / "verifier" / "workers"). Owner directive
2026-10-27: sub-run token usage is billing/trial data the user needs —
a silent sub-agent that burns tokens without appearing in the log is the
same defect class as a silent profile fallback (UPDATE-023 backlog).

The /performance route and the extension dashboard read only the known
keys, so the extra field is invisible to them and harmless to old readers.
KNOWN LIMITATION (pre-existing, unchanged): estimated_cost_usd uses one
hardcoded price pair (the DeepSeek-era rates); treat it as a rough
relative estimate across models, not an invoice. Per-model pricing is a
separate candidate, not silently changed here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime


def log_performance(
    project_path: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: float,
    status: str,
    role: str | None = None,
):
    """Record API usage statistics to the .orion/performance_log.jsonl file.

    ``role`` None = the main agent loop (entry shape unchanged since Phase
    C); a sub-agent role name tags the entry so per-role token accounting
    is possible (UPDATE-024 Commit 1).
    """
    try:
        orion_dir = os.path.join(project_path, ".orion")
        os.makedirs(orion_dir, exist_ok=True)
        log_file = os.path.join(orion_dir, "performance_log.jsonl")

        # >>> REALISTIC PRICING (DeepSeek V3/R1 via API) <<<
        price_input_per_million = 0.14
        price_output_per_million = 0.28

        estimated_cost_usd = (input_tokens / 1_000_000) * price_input_per_million + (
            output_tokens / 1_000_000
        ) * price_output_per_million

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(estimated_cost_usd, 6),
            "duration_ms": round(duration_ms, 2),
            "status": status,
        }
        if role:
            log_entry["role"] = role

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    except Exception as e:
        print(f"[ORION PERF LOG WARNING] Failed to record log: {e}")
