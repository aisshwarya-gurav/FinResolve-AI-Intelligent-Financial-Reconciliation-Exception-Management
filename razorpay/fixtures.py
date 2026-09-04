"""
Mock fixtures for the Razorpay adapter's mock mode.

SOURCING, stated plainly:

- MOCK_PAYOUT_TRANSACTION is built from a REAL, CONFIRMED example in
  Razorpay's official docs — the "Fetch a Payout with ID" response
  (https://razorpay.com/docs/api/x/payouts/fetch-with-id/), wrapped in
  the Transactions entity's documented fields (id, entity, account_number,
  amount, currency, credit, debit, balance, source, utr, mode, created_at
  — confirmed via https://razorpay.com/docs/api/x/transactions/fetch-all/
  and /fetch-with-id/, both fetched during this build).

- MOCK_BANK_TRANSFER_TRANSACTION's `source` object is a BEST-EFFORT
  CONSTRUCTION, not verified against a real API response. Razorpay's
  docs describe the credit-side source only in prose ("details of the
  bank account from which money was added to your business account")
  — no full JSON example for this case was found in the official docs
  during this build. If this source type matters for a real
  integration, validate the actual field names against a live
  RazorpayX test-mode account before relying on this branch.
"""

import copy

MOCK_PAYOUT_TRANSACTION = {
    "id": "txn_00000000000001",
    "entity": "transaction",
    "account_number": "7878780080316316",
    "amount": 1000000,
    "currency": "INR",
    "credit": 0,
    "debit": 1000000,
    "balance": 5000000,
    "utr": "HDFCN00000000001",
    "mode": "NEFT",
    "created_at": 1545382870,
    "source": {
        "id": "pout_00000000000001",
        "entity": "payout",
        "fund_account_id": "fa_00000000000001",
        "amount": 1000000,
        "currency": "INR",
        "fees": 590,
        "tax": 90,
        "status": "processed",
        "purpose": "payout",
        "utr": "HDFCN00000000001",
        "mode": "NEFT",
        "reference_id": "Acme Transaction ID 12345",
        "narration": "Acme Corp Fund Transfer",
        "debit_account_number": "002281300012871",
        "batch_id": None,
        "status_details": {
            "description": "Payout is processed and the money has been credited into the beneficiaries account",
            "source": "beneficiary_bank",
            "reason": "payout_processed",
        },
        "created_at": 1545382870,
        "fee_type": "",
    },
}

# UNCONFIRMED structure — see module docstring.
MOCK_BANK_TRANSFER_TRANSACTION = {
    "id": "txn_00000000000002",
    "entity": "transaction",
    "account_number": "7878780080316316",
    "amount": 500000,
    "currency": "INR",
    "credit": 500000,
    "debit": 0,
    "balance": 5500000,
    "utr": None,   # docs: utr is only returned when the source entity is 'payout'
    "mode": None,  # docs: mode is only returned when the source entity is 'payout'
    "created_at": 1545320320,
    "source": {
        "id": "bt_00000000000001",
        "entity": "bank_transfer",  # NOT confirmed against a real API response
        "bank_name": "HDFC Bank",
        "account_number": "765432123456789",
        "ifsc": "HDFC0000053",
        "status": "settled",
    },
}

_TEMPLATES = {
    "payout": MOCK_PAYOUT_TRANSACTION,
    "bank_transfer": MOCK_BANK_TRANSFER_TRANSACTION,
}


def get_mock_transactions(source_type: str, count: int = 10) -> list:
    """Returns `count` mock transactions for the given source type, each
    with a distinct id/created_at so they behave like a real list rather
    than identical repeats."""
    if source_type not in _TEMPLATES:
        raise ValueError(f"Unknown source_type '{source_type}'. Use 'payout' or 'bank_transfer'.")

    template = _TEMPLATES[source_type]
    results = []
    for i in range(count):
        item = copy.deepcopy(template)
        suffix = str(i + 1).zfill(2)
        item["id"] = item["id"][:-2] + suffix
        item["source"]["id"] = item["source"]["id"][:-2] + suffix
        item["created_at"] = item["created_at"] + i * 3600
        item["source"]["created_at"] = item["source"].get("created_at", item["created_at"])
        results.append(item)
    return results
