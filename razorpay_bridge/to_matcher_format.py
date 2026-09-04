"""
Converts normalized Razorpay records (razorpay/normalizer.py's output)
into the EXACT input format finrca_data/matcher.py's match_records()
expects for its `bank` DataFrame argument. No matcher logic lives here
— this module only reshapes data; the matcher itself is imported and
called unmodified in pipeline.py.

Required bank DataFrame columns (confirmed by reading
finrca_data/matcher.py directly): bank_transaction_id, payment_reference,
amount, currency, transaction_date, counterparty_token.

Razorpay-specific richness (fees, tax, utr, mode, source_type, source_id)
is deliberately NOT included here — the matcher doesn't use those
fields and shouldn't see them. That richness is preserved separately in
pipeline.py (keyed by bank_transaction_id) so Case Management can still
show full Razorpay context later, without it ever leaking into the
matcher's world.
"""

import pandas as pd

REQUIRED_MATCHER_BANK_COLUMNS = [
    "bank_transaction_id", "payment_reference", "amount",
    "currency", "transaction_date", "counterparty_token",
]


def normalized_record_to_bank_row(record: dict) -> dict:
    """Maps one Razorpay-normalized record to one row of the matcher's
    expected bank-side schema.

    Field mapping, with reasoning:
      bank_transaction_id <- record['transaction_id']
      payment_reference   <- record['source_id'] or record['utr'] or
                              record['transaction_id'] (best available
                              reference-like id; a production integration
                              would ideally use the payout's
                              reference_id — the merchant-supplied
                              reference — but that field is not part of
                              razorpay/normalizer.py's current output
                              schema, and this bridge does not modify
                              that file, per instructions)
      amount               <- record['amount'] (already decimal)
      currency              <- record['currency']
      transaction_date       <- date portion of record['created_at']
                              (ISO datetime -> YYYY-MM-DD, matching
                              finrca_data/bank_transactions.csv's format)
      counterparty_token      <- record['mode'] or 'unknown' (matcher.py
                              never uses this field for matching
                              decisions, only carries it through into
                              exception output)
    """
    payment_reference = record.get("source_id") or record.get("utr") or record.get("transaction_id")

    created_at = record.get("created_at")
    transaction_date = created_at[:10] if created_at else None

    return {
        "bank_transaction_id": record.get("transaction_id"),
        "payment_reference": payment_reference,
        "amount": record.get("amount"),
        "currency": record.get("currency"),
        "transaction_date": transaction_date,
        "counterparty_token": record.get("mode") or "unknown",
    }


def normalized_records_to_bank_dataframe(records: list) -> pd.DataFrame:
    """Converts a list of Razorpay-normalized records into a DataFrame
    with exactly the columns finrca_data/matcher.py's match_records()
    expects for its `bank` argument."""
    if not records:
        return pd.DataFrame(columns=REQUIRED_MATCHER_BANK_COLUMNS)

    rows = [normalized_record_to_bank_row(r) for r in records]
    return pd.DataFrame(rows, columns=REQUIRED_MATCHER_BANK_COLUMNS)
