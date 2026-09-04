# Reconciliation Agent

A multi-agent system that reconciles a merchant's payment ledger against a
bank settlement statement — built for the Razorpay AI Buildathon, AI Finance
Controller track.

## Problem

Merchants reconcile payment ledgers against bank statements by hand.
Amounts drift (partial refunds), dates drift (settlement delay), and some
payments never settle at all. This agent automates matching, explains every
exception in plain English, and reports honest accuracy — including what it
could *not* resolve.

## Architecture

Four agents, each with one job:

1. **Matcher** — pure rule-based logic (pandas). Exact match on reference
   ID + amount + date; anything else becomes an exception. No LLM involved —
   deterministic and auditable.
2. **Explainer** — the only agent that calls an LLM. Only runs on
   exceptions the Matcher flagged, never on clean matches. Output is forced
   into a strict JSON schema and passed through a rule-based **verifier**
   that rejects any explanation referencing numbers not present in the
   source record — this is the hallucination guard.
3. **Auditor** — logs every decision from every agent with a timestamp to
   `data/audit_log.jsonl`. This is the audit trail.
4. **Reporter** — computes the final match rate and lists every unresolved
   exception. Nothing is hidden or cherry-picked.

## Why this design avoids hallucination

- The LLM never sees the full ledger or bank tables — only one
  already-computed exception record at a time.
- Every LLM response is schema-validated and grounding-checked before being
  accepted; unverifiable explanations are auto-escalated to human review
  instead of shown as resolved.
- Untrusted record data is passed to the model as clearly delimited data,
  never blended into the system instructions.

## Security notes

- All data in `data/*.csv` is synthetically generated (see
  `data/generate_data.py`) — no real payment data or PII.
- API key is read from the `ANTHROPIC_API_KEY` environment variable, never
  hardcoded. Copy `.env.example` to `.env` and fill in your key.
- Every agent action is bounded: the Explainer only processes flagged
  exceptions, never modifies source data, and has no ability to take
  irreversible action.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # then add your ANTHROPIC_API_KEY
python data/generate_data.py   # generates synthetic ledger + bank CSVs
python main.py                  # runs the full pipeline
```

## Output

- `data/latest_report.json` — final report with match rate and exception list
- `data/audit_log.jsonl` — full audit trail, one JSON event per line

## Honest metrics (fill in after your first real run)

- Total records processed:
- Clean match rate:
- Auto-resolved with verified explanation:
- Escalated to human review:
- False positives observed (if any):

## What's next

- Streamlit UI for live demo
- Expand verifier beyond number-grounding to date/entity grounding
- Batch-level anomaly circuit breaker (pause on mismatch spikes)
