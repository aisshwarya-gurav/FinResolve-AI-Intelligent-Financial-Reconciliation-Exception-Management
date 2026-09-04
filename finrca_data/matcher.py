"""
FinRCA Matcher Agent — deterministic, no LLM.

Matches payments.csv (internal ERP/AP record) against bank_transactions.csv
(bank-side record) via reference_number <-> payment_reference, then checks
amount/currency agreement. Unlike the synthetic project, this real dataset
has genuine injected failures — payments with no bank transaction (F12),
bank transactions with no payment (F13), and amount mismatches on an
otherwise-matched pair (F14/F15) — so the matcher has real work to do.

Outputs four buckets:
  matched            — clean, amount + currency agree
  amount_mismatch    — reference matches, but amount/currency doesn't (F14)
  payment_no_bank    — payment exists, no bank transaction found (F12)
  bank_no_payment    — bank transaction exists, no payment found (F13)
"""

import pandas as pd

AMOUNT_TOLERANCE = 0.01  # currency rounding tolerance only — this data
                          # doesn't have partial-refund-style variance


def load_data(payments_path="finrca_data/payments.csv",
              bank_path="finrca_data/bank_transactions.csv"):
    payments = pd.read_csv(payments_path)
    bank = pd.read_csv(bank_path)
    return payments, bank


def match_records(payments: pd.DataFrame, bank: pd.DataFrame) -> dict:
    bank_indexed = bank.set_index("payment_reference")

    matched, amount_mismatch, payment_no_bank = [], [], []

    matched_bank_refs = set()

    for _, pay in payments.iterrows():
        ref = pay["reference_number"]

        if ref not in bank_indexed.index:
            payment_no_bank.append({
                "payment_id": pay["payment_id"],
                "reference_number": ref,
                "payment_amount": pay["payment_amount"],
                "payment_currency": pay["payment_currency"],
                "payment_date": pay["payment_date"],
                "vendor_id": pay["vendor_id"],
                "payment_status": pay["payment_status"],
                "settlement_status": pay["settlement_status"],
                "issue": "no_bank_transaction_found",
            })
            continue

        bank_row = bank_indexed.loc[ref]
        # Guard against duplicate reference numbers on the bank side
        if isinstance(bank_row, pd.DataFrame):
            bank_row = bank_row.iloc[0]

        matched_bank_refs.add(ref)

        amount_diff = round(float(pay["payment_amount"]) - float(bank_row["amount"]), 2)
        currency_match = pay["payment_currency"] == bank_row["currency"]

        if abs(amount_diff) <= AMOUNT_TOLERANCE and currency_match:
            matched.append({
                "payment_id": pay["payment_id"],
                "bank_transaction_id": bank_row["bank_transaction_id"],
                "amount": pay["payment_amount"],
                "currency": pay["payment_currency"],
            })
        else:
            amount_mismatch.append({
                "payment_id": pay["payment_id"],
                "bank_transaction_id": bank_row["bank_transaction_id"],
                "payment_amount": pay["payment_amount"],
                "bank_amount": bank_row["amount"],
                "amount_diff": amount_diff,
                "payment_currency": pay["payment_currency"],
                "bank_currency": bank_row["currency"],
                "currency_match": currency_match,
                "vendor_id": pay["vendor_id"],
                "issue": "amount_or_currency_mismatch",
            })

    # Bank transactions with no corresponding payment at all (F13)
    bank_no_payment = []
    for _, b in bank.iterrows():
        if b["payment_reference"] not in matched_bank_refs and \
           b["payment_reference"] not in set(payments["reference_number"]):
            bank_no_payment.append({
                "bank_transaction_id": b["bank_transaction_id"],
                "payment_reference": b["payment_reference"],
                "amount": b["amount"],
                "currency": b["currency"],
                "transaction_date": b["transaction_date"],
                "counterparty_token": b["counterparty_token"],
                "issue": "no_payment_record_found",
            })

    return {
        "matched": matched,
        "amount_mismatch": amount_mismatch,
        "payment_no_bank": payment_no_bank,
        "bank_no_payment": bank_no_payment,
    }


if __name__ == "__main__":
    payments, bank = load_data()
    results = match_records(payments, bank)
    print(f"Payments: {len(payments)}, Bank transactions: {len(bank)}")
    print(f"Matched:            {len(results['matched'])}")
    print(f"Amount mismatch:    {len(results['amount_mismatch'])}")
    print(f"Payment, no bank:   {len(results['payment_no_bank'])}")
    print(f"Bank, no payment:   {len(results['bank_no_payment'])}")
