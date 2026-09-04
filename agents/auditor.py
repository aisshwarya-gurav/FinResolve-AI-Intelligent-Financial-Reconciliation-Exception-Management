"""
Auditor Agent — not really "AI", just disciplined logging.

Every decision made by the Matcher and Explainer agents gets recorded
here with a timestamp and a reasoning trace. This is what satisfies the
"explainable, bounded, gated" + "audit trail" requirement in the brief.

The audit log is what you should screenshot/quote in your pitch and
architecture walkthrough.
"""

import json
from datetime import datetime, timezone

AUDIT_LOG_PATH = "data/audit_log.jsonl"


def log_event(agent: str, reference_id: str, decision: str, detail: dict):
    """Appends one structured audit event. jsonl format = one JSON object
    per line, easy to grep, easy to load into pandas for the report."""

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "reference_id": reference_id,
        "decision": decision,
        "detail": detail,
    }
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    return event


def clear_log():
    """Call at the start of each run so logs don't accumulate across runs."""
    open(AUDIT_LOG_PATH, "w").close()


def load_log():
    events = []
    try:
        with open(AUDIT_LOG_PATH, "r") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    return events
