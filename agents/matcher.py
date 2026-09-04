"""
Matcher Agent — deterministic, no LLM involved.

Matches ledger records against bank records using:
1. Exact match: same reference_id, same amount, same date
2. Partial match: same reference_id, amount/date differ (needs explanation)
3. Unmatched: ledger record has no corresponding bank record at all

Kept 100% rule-based on purpose — the LLM should never touch the matching
logic itself, only explain what the matcher already found. This avoids
hallucinated matches.
"""

import pandas as pd
from datetime import datetime


def load_data(ledger_path="data/ledger.csv", bank_path="data/bank_statement.csv"):
    ledger = pd.read_csv(ledger_path)
    bank = pd.read_csv(bank_path)
    return ledger, bank


def match_records(ledger: pd.DataFrame, bank: pd.DataFrame):
    """
    Returns three buckets: matched, partial (needs explanation), unmatched.
    Each row in partial/unmatched includes exactly what differs, so the
    Explainer agent has real data to reason over — never guesses.
    """
    bank_indexed = bank.set_index("reference_id")

    matched = []
    partial = []
    unmatched = []

    for _, row in ledger.iterrows():
        ref = row["reference_id"]

        if ref not in bank_indexed.index:
            unmatched.append({
                "reference_id": ref,
                "ledger_amount": row["ledger_amount"],
                "ledger_date": row["ledger_date"],
                "merchant": row["merchant"],
                "issue": "no_bank_record_found",
            })
            continue

        bank_row = bank_indexed.loc[ref]
        amount_diff = round(row["ledger_amount"] - bank_row["bank_amount"], 2)
        date_diff_days = (
            datetime.strptime(row["ledger_date"], "%Y-%m-%d")
            - datetime.strptime(bank_row["bank_date"], "%Y-%m-%d")
        ).days

        if amount_diff == 0 and date_diff_days == 0:
            matched.append({
                "reference_id": ref,
                "amount": row["ledger_amount"],
                "date": row["ledger_date"],
            })
        else:
            partial.append({
                "reference_id": ref,
                "ledger_amount": row["ledger_amount"],
                "bank_amount": bank_row["bank_amount"],
                "amount_diff": amount_diff,
                "ledger_date": row["ledger_date"],
                "bank_date": bank_row["bank_date"],
                "date_diff_days": date_diff_days,
                "merchant": row["merchant"],
            })

    return {
        "matched": matched,
        "partial": partial,
        "unmatched": unmatched,
    }


if __name__ == "__main__":
    ledger, bank = load_data()
    results = match_records(ledger, bank)
    print(f"Matched: {len(results['matched'])}")
    print(f"Partial (needs explanation): {len(results['partial'])}")
    print(f"Unmatched (no bank record): {len(results['unmatched'])}")
