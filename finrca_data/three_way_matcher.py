"""
Three-Way Matcher Agent — Invoice -> Payment -> Bank Settlement.

This is a real accounting concept ("three-way match") extended one hop
further than the classic PO/invoice/payment version: here it's
invoice-to-payment-to-bank-settlement, closing the full money trail from
"what we owe" to "what actually left the bank."

Stage 1 — Invoice <-> Payment (via payment_allocations.csv):
  For every invoice marked "paid", sum its allocated payments and compare
  to the invoice total:
    - allocated == total          -> fully_paid, proceed to Stage 2
    - allocated < total           -> partial_payment (F07)
    - allocated > total           -> overpaid_or_duplicate (F06)
  Also: any "completed" payment with NO invoice allocation at all is a
  payment_without_invoice (F05).

Stage 2 — Payment <-> Bank (reuses the same logic as matcher.py):
  For every payment resulting from a "fully_paid" invoice in Stage 1,
  check it settled at the bank with matching amount/currency — same as
  the two-way matcher, just now gated behind a confirmed invoice match
  first.

Fully deterministic, no LLM — same design principle as every other
Matcher in this project: reasoning stays in code, the LLM only explains
what the matcher already found.
"""

import pandas as pd

INVOICE_AMOUNT_TOLERANCE = 0.01
BANK_AMOUNT_TOLERANCE = 0.01


def load_data(
    invoices_path="finrca_data/invoices.csv",
    allocations_path="finrca_data/payment_allocations.csv",
    payments_path="finrca_data/payments.csv",
    bank_path="finrca_data/bank_transactions.csv",
):
    invoices = pd.read_csv(invoices_path)
    allocations = pd.read_csv(allocations_path)
    payments = pd.read_csv(payments_path)
    bank = pd.read_csv(bank_path)
    return invoices, allocations, payments, bank


def match_three_way(invoices: pd.DataFrame, allocations: pd.DataFrame,
                     payments: pd.DataFrame, bank: pd.DataFrame) -> dict:

    bank_indexed = bank.set_index("payment_reference")
    payments_indexed = payments.set_index("payment_id")

    # --- Stage 1: Invoice <-> Payment ---
    alloc_by_invoice = allocations.groupby("invoice_id")["allocated_amount"].sum()

    fully_paid = []          # ready for Stage 2
    partial_payment = []     # F07
    overpaid_duplicate = []  # F06

    for _, inv in invoices[invoices["status"] == "paid"].iterrows():
        invoice_id = inv["invoice_id"]
        allocated = float(alloc_by_invoice.get(invoice_id, 0.0))
        total = float(inv["invoice_total"])
        diff = round(allocated - total, 2)

        record_base = {
            "invoice_id": invoice_id,
            "vendor_id": inv["vendor_id"],
            "invoice_total": total,
            "allocated_amount": allocated,
            "diff": diff,
            "invoice_date": inv["invoice_date"],
            "due_date": inv["due_date"],
        }

        if abs(diff) <= INVOICE_AMOUNT_TOLERANCE:
            payment_ids = allocations[allocations["invoice_id"] == invoice_id]["payment_id"].tolist()
            fully_paid.append({**record_base, "payment_ids": payment_ids})
        elif diff < 0:
            record_base["issue"] = "partial_payment_residual_balance"
            record_base["percent_covered"] = round(allocated / total * 100, 1) if total else 0
            partial_payment.append(record_base)
        else:
            record_base["issue"] = "allocations_exceed_invoice_total"
            overpaid_duplicate.append(record_base)

    # Payments with no invoice allocation at all, but marked completed (F05)
    allocated_payment_ids = set(allocations["payment_id"])
    payment_without_invoice = []
    for _, pay in payments.iterrows():
        if pay["payment_status"] == "completed" and pay["payment_id"] not in allocated_payment_ids:
            payment_without_invoice.append({
                "payment_id": pay["payment_id"],
                "vendor_id": pay["vendor_id"],
                "payment_amount": pay["payment_amount"],
                "payment_currency": pay["payment_currency"],
                "payment_date": pay["payment_date"],
                "issue": "payment_without_valid_invoice",
            })

    # --- Stage 2: Payment <-> Bank, only for fully-paid invoices' payments ---
    chain_complete = []       # invoice -> payment -> bank, all clean
    chain_bank_exception = [] # invoice/payment matched, but bank side has a problem

    for record in fully_paid:
        for payment_id in record["payment_ids"]:
            if payment_id not in payments_indexed.index:
                chain_bank_exception.append({
                    **record, "payment_id": payment_id,
                    "issue": "payment_record_not_found",
                })
                continue

            pay_row = payments_indexed.loc[payment_id]
            ref = pay_row["reference_number"]

            if ref not in bank_indexed.index:
                chain_bank_exception.append({
                    **record, "payment_id": payment_id,
                    "payment_amount": pay_row["payment_amount"],
                    "issue": "no_bank_transaction_found",
                })
                continue

            bank_row = bank_indexed.loc[ref]
            if isinstance(bank_row, pd.DataFrame):
                bank_row = bank_row.iloc[0]

            amount_diff = round(float(pay_row["payment_amount"]) - float(bank_row["amount"]), 2)
            currency_match = pay_row["payment_currency"] == bank_row["currency"]

            if abs(amount_diff) <= BANK_AMOUNT_TOLERANCE and currency_match:
                chain_complete.append({
                    **record, "payment_id": payment_id,
                    "bank_transaction_id": bank_row["bank_transaction_id"],
                })
            else:
                chain_bank_exception.append({
                    **record, "payment_id": payment_id,
                    "payment_amount": pay_row["payment_amount"],
                    "bank_amount": bank_row["amount"],
                    "bank_amount_diff": amount_diff,
                    "currency_match": currency_match,
                    "issue": "bank_amount_or_currency_mismatch",
                })

    return {
        "chain_complete": chain_complete,
        "chain_bank_exception": chain_bank_exception,
        "partial_payment": partial_payment,
        "overpaid_duplicate": overpaid_duplicate,
        "payment_without_invoice": payment_without_invoice,
    }


if __name__ == "__main__":
    invoices, allocations, payments, bank = load_data()
    results = match_three_way(invoices, allocations, payments, bank)
    print(f"Invoices marked 'paid': {len(invoices[invoices['status']=='paid'])}")
    print(f"Full chain complete (invoice->payment->bank, all clean): {len(results['chain_complete'])}")
    print(f"Chain bank-side exception:  {len(results['chain_bank_exception'])}")
    print(f"Partial payment (F07):      {len(results['partial_payment'])}")
    print(f"Overpaid/duplicate (F06):   {len(results['overpaid_duplicate'])}")
    print(f"Payment without invoice (F05): {len(results['payment_without_invoice'])}")
