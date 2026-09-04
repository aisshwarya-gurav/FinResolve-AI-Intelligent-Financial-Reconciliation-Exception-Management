# FinRCA-Bench Pipeline

A third validation layer alongside the synthetic demo and the BenchRec
real-data pipeline — this one uses
[FinRCA-Bench](https://github.com/PratikGhawate/FinRCA-AI-Bench), a
purpose-built academic benchmark for financial reconciliation and
root-cause analysis, generated at `--scale small` with a fixed seed
(reproducible, deterministic).

## Why this one is different

BenchRec proved the matching approach on independently-published
real-world-style data. FinRCA-Bench adds something neither the synthetic
nor BenchRec pipeline has: **labeled ground truth for exactly which cases
are real failures vs. legitimate lookalikes designed to be confusing.**
That lets us report two honest numbers a panel can't dismiss as
cherry-picked:

- **Recall on real injected failures** — did we catch the actual problems?
- **False positive rate on hard negatives** — did we avoid crying wolf on
  legitimate edge cases (weekend clearing, pending settlement, FX
  conversion, fee deductions) that are deliberately built to *resemble*
  failures?

## Scope

Out of FinRCA-Bench's 15 failure categories and 9-table schema, we use
only what maps onto this project's actual job — payment-to-bank matching:

- `payments.csv` (ERP/AP side) and `bank_transactions.csv` (bank side)
- Ground truth filtered to **F12** (ERP payment missing from bank),
  **F13** (bank transaction missing from ERP), **F14** (bank/ERP amount
  mismatch), **F15** (incorrect payment-to-bank match), plus the
  `NO_FAILURE` cases whose primary entity is a payment (the relevant
  hard negatives)

The other 11 failure categories (duplicate invoices, PO mismatches,
approval workflow failures, GL posting issues, etc.) are out of scope —
they're AP/GL problems, not bank-matching problems, and pulling them in
would be a different project.

## Architecture

Same four-agent pattern as the rest of this repo:

1. **Matcher** (`matcher.py`) — deterministic, joins `payments.reference_number`
   to `bank_transactions.payment_reference`, checks amount + currency
   agreement. No LLM.
2. **Explainer** (`explainer.py`) — same grounding + verification
   discipline as the synthetic pipeline, adapted field names.
3. **Auditor** — reused from `agents/auditor.py`, logs every decision.
4. **Evaluator** (`evaluator.py`) — the new piece. Cross-references the
   Matcher's output against FinRCA-Bench's real injected-failure labels.

## Honest results (50-case generated dataset, seed 42)

**Matching:**
- 184 payments, 184 bank transactions
- 173 matched cleanly
- 8 amount/currency mismatches, 3 payments with no bank record, 2 bank
  transactions with no payment record

**Against real ground truth:**
- **8/8 real injected bank-matching failures caught (100% recall)** —
  every genuine F12-F15 failure in the dataset was flagged as an
  exception, none silently passed through as a clean match
- **6/9 hard negatives correctly left alone, 3/9 wrongly flagged
  (33% false positive rate)** — the matcher over-flags legitimate pending
  settlements, net-fee accounting, and FX conversion differences,
  because a pure amount/reference join can't yet distinguish "explainable
  variance" from "real problem"

## What this honestly tells you

The deterministic Matcher is tuned toward caution — it never misses a
real failure, but it isn't yet smart enough to wave through legitimate
variance on its own. That's *exactly* what the Explainer agent exists to
fix: each of the 3 false positives is a case where an LLM given the full
context (fee schedules, settlement timing norms, FX rate context) should
recognize the legitimate pattern and downgrade it, rather than escalate
it as a hard exception. Improving that disambiguation is the natural next
step, and it's now measurable — you have a labeled test set to check
against, not just a vibe.

## Run it

```bash
python finrca_data/main_finrca.py
```

Requires `ANTHROPIC_API_KEY` set for the Explainer step; the Matcher and
ground-truth evaluation run without it.

## Regenerating the source data

```bash
git clone https://github.com/PratikGhawate/FinRCA-AI-Bench.git
cd FinRCA-AI-Bench
pip install -r requirements.txt
python generate_finrca_bench.py --config config.yaml --seed 42 --scale small
```

Then copy `data/benchmark/full/payments.csv`,
`data/benchmark/full/bank_transactions.csv`, and
`data/benchmark/rca_ground_truth.jsonl` into `finrca_data/`.
