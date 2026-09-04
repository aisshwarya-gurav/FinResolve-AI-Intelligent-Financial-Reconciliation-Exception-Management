# Real-Data Pipeline (BenchRec)

In addition to the synthetic demo (`main.py`), this project includes a
matcher tested on **real, independently-published benchmark data**: the
BenchRec cash reconciliation dataset from ICAIF 2023 (ACM International
Conference on AI in Finance).

## Why this matters

The synthetic pipeline proves the agent architecture works cleanly. This
one proves the matching approach holds up on messy real-world data where:
- There's no shared ID between the ledger and bank statement systems
- Reference numbers are formatted differently on each side
- The task is genuinely hard: 80,879 possible internal ledger candidates
  to match each bank statement line against

## How matching works here (different from the synthetic Matcher)

The synthetic project's Matcher does an exact `reference_id` join — not
possible here, since A-side (ledger) and B-side (statement) rows use
completely separate ID systems. Instead:

1. **Blocking** — narrow candidates by currency, account, amount
   (±0.1%), and date (±5 days). Cuts 80k candidates down to a small,
   relevant set per transaction.
2. **TF-IDF text similarity** — character n-grams over the reference/
   attribute text fields, cosine similarity against blocked candidates.
3. **Superstring rescue** — the key discovery from testing on this data:
   raw text similarity alone missed true matches, because the two
   systems reformat the same underlying reference number differently.
   Extracting long digit runs (6+ digits) from both sides and checking
   for substring overlap catches these — a shared long digit run is very
   strong evidence of a real match. This alone took match rate from
   3.5% to 73.5% in testing.
4. **Threshold decision** — anything below the confidence threshold is
   left unmatched for human review, rather than guessed — same
   "leave unmatched rather than mismatch" philosophy the ICAIF benchmark
   itself uses to define "good."

## Honest results (2,000-row evaluation sample)

- Match rate: 72.7%
- Precision on matched: 87.8%
- False matches: 177 (12.2%) — reported, not hidden
- Unmatched (no candidates in block): 47
- Unmatched (below confidence threshold): 499

**Context**: ICAIF 2023's official benchmark bar is 99.8-99.9% precision
for a production-grade matcher. This baseline is well below that, and
that gap is reported honestly here rather than glossed over — closing it
is the next iteration (see below), not a claim already made.

## What's next for this pipeline

- Add the Explainer agent to the low-confidence/unmatched cases —
  same anti-hallucination grounding pattern as the synthetic pipeline,
  giving a human reviewer a plain-English reason for each miss
- Tune the confidence threshold with a precision/recall sweep to trade
  off match rate vs. false-match rate deliberately, not by accident
- Run against the full 68,975-row B-side (currently sampling 2,000 for
  runtime) — the matcher scales linearly, just takes longer

## Run it

```bash
python real_data/main_real.py
```

Data files (`BenchRec_cash_v1.0_*.csv`) are the real BenchRec dataset,
included under `real_data/` — see the original source for licensing:
https://www.kaggle.com/datasets/benchmarkteam/benchrec-real-world-cash-reconciliation-dataset
