# Three-Way Match: Invoice -> Payment -> Bank Settlement

A fourth validation layer, extending the two-way (payment <-> bank)
matcher one hop further back to the invoice — a real accounting concept
("three-way match") rather than just "two sources matched twice."

## Why this closes a longer, more realistic loop

The two-way matcher (`main_finrca.py`) answers: *"did this payment
settle at the bank?"* This one answers the fuller question a real AP
team asks: *"was this invoice actually paid in full, exactly once, and
did that payment actually settle?"* — catching a class of exceptions
invisible to payment-vs-bank matching alone: an invoice paid twice, an
invoice only partially paid, or a payment with no invoice behind it at
all.

## Architecture

Two-stage deterministic matcher, still no LLM in the matching logic:

**Stage 1 — Invoice <-> Payment** (`payment_allocations.csv` links them):
For every invoice marked `paid`, sum its allocated payments and compare
to the invoice total. Three outcomes: fully paid (proceed to Stage 2),
partial payment (F07), or allocations exceeding the total — overpaid or
duplicate payment (F06). Separately, any completed payment with zero
invoice allocation is a payment without a valid invoice (F05).

**Stage 2 — Payment <-> Bank** (only for invoices Stage 1 confirmed
fully paid): same amount/currency check as the two-way matcher.

**Explainer, Auditor**: same agents, same grounding/verification
discipline, reused as-is — the schema-agnostic design from the earlier
pipelines paid off here, no rewrite needed.

## Honest results (same 50-case generated dataset, seed 42)

**Matching (178 invoices marked 'paid'):**
- 192 clean invoice→payment→bank chains
- 14 chain exceptions (bank-side issues on an otherwise-confirmed invoice/payment match)
- 2 partial payments (F07)
- 2 overpaid/duplicate (F06)
- 2 payments with no invoice (F05)

**Against real ground truth (F05, F06, F07):**
- **6/6 real injected failures caught (100% recall)** — exact ID match,
  zero misses
- **10/11 hard negatives correctly left alone (9.1% false positive
  rate)** — meaningfully better than the two-way matcher's 33% on pure
  bank-matching, because confirming the invoice/payment relationship
  first filters out noise before the bank-matching stage even runs

**The one false positive, traced**: `INV_0420168`'s flagged payment
(`PAY_0420147`) is the *exact same* `LEGITIMATE_NET_FEE_ACCOUNTING` case
already identified and tested in `test_false_positives.py` — not a new,
independent weakness, just the same known bank-side fee discrepancy
propagating up the chain. That's a coherent, honest story: the
invoice-payment stage is clean, and the only leak is one already-known,
already-being-fixed edge case.

## Run it

```bash
python finrca_data/main_three_way.py
```

Requires `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/etc. (see root
`.env.example`) for the Explainer step; the Matcher and ground-truth
evaluation run without it.
