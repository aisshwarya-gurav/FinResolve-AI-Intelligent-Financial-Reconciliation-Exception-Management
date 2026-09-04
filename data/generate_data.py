"""
Generates synthetic ledger (Razorpay-style) and bank statement records
for the reconciliation agent to match against.

Run: python data/generate_data.py
Outputs: data/ledger.csv, data/bank_statement.csv
"""

import csv
import random
import uuid
from datetime import datetime, timedelta

random.seed(42)  # reproducible runs

NUM_RECORDS = 60  # >50 per track requirement
OUTPUT_DIR = "data"


def random_date(start_days_ago=30):
    base = datetime.now() - timedelta(days=random.randint(0, start_days_ago))
    return base.strftime("%Y-%m-%d")


def generate_records():
    ledger_rows = []
    bank_rows = []

    for i in range(NUM_RECORDS):
        ref_id = f"pay_{uuid.uuid4().hex[:10]}"
        amount = round(random.uniform(100, 50000), 2)
        date = random_date()

        # Ledger record (always exists)
        ledger_rows.append({
            "reference_id": ref_id,
            "ledger_amount": amount,
            "ledger_date": date,
            "merchant": f"merchant_{random.randint(1, 15)}",
            "status": "captured",
        })

        # Decide what kind of bank record to generate (simulates real-world messiness)
        roll = random.random()

        if roll < 0.75:
            # Clean match: same amount, same or +/-1 day date
            bank_rows.append({
                "reference_id": ref_id,
                "bank_amount": amount,
                "bank_date": date,
            })
        elif roll < 0.85:
            # Partial refund: bank amount is lower
            partial = round(amount - random.uniform(10, min(500, amount * 0.3)), 2)
            bank_rows.append({
                "reference_id": ref_id,
                "bank_amount": partial,
                "bank_date": date,
            })
        elif roll < 0.93:
            # Date mismatch: settled a few days later
            settle_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=random.randint(2, 5))).strftime("%Y-%m-%d")
            bank_rows.append({
                "reference_id": ref_id,
                "bank_amount": amount,
                "bank_date": settle_date,
            })
        else:
            # Missing entirely: payment never settled (still processing, or failed silently)
            pass  # no bank row generated

    # Add a few bank-only records (bank fees, unrelated transfers) with no ledger match
    for _ in range(4):
        bank_rows.append({
            "reference_id": f"bank_only_{uuid.uuid4().hex[:8]}",
            "bank_amount": round(random.uniform(50, 2000), 2),
            "bank_date": random_date(),
        })

    return ledger_rows, bank_rows


def write_csv(rows, filename, fieldnames):
    path = f"{OUTPUT_DIR}/{filename}"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    ledger, bank = generate_records()
    write_csv(ledger, "ledger.csv", ["reference_id", "ledger_amount", "ledger_date", "merchant", "status"])
    write_csv(bank, "bank_statement.csv", ["reference_id", "bank_amount", "bank_date"])
    print(f"\nGenerated {len(ledger)} ledger records, {len(bank)} bank records.")
    print("These are 100% synthetic — no real payment data.")
