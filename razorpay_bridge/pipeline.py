"""
Razorpay Bridge Pipeline — wires the existing Razorpay adapter to the
EXISTING, UNMODIFIED finrca_data/matcher.py.

    RazorpayAdapter.fetch_transactions()        (razorpay/ — unmodified)
        -> normalized_records_to_bank_dataframe() (this bridge, reshaping only)
        -> finrca_data.matcher.match_records()     (EXISTING matcher, imported as-is)
        -> match results (matched / amount_mismatch / payment_no_bank / bank_no_payment)

The existing payments.csv (internal ERP/AP data) is used unchanged as
the "payments" side of the match — Razorpay Transactions ARE bank-side
data, the same role finrca_data/bank_transactions.csv normally plays,
so this is a like-for-like substitution at the matcher's input
boundary, not a new matching concept.
"""

import sys
import os
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "finrca_data"))

from matcher import match_records  # EXISTING matcher function, unmodified, used as-is
from razorpay.client import RazorpayAdapter  # EXISTING adapter, unmodified

from .to_matcher_format import normalized_records_to_bank_dataframe

DEFAULT_PAYMENTS_PATH = os.path.join(os.path.dirname(__file__), "..", "finrca_data", "payments.csv")


def run_razorpay_reconciliation(adapter: RazorpayAdapter, source_type: str, count: int = 10,
                                 payments_path: str = DEFAULT_PAYMENTS_PATH,
                                 normalized_records: list = None) -> dict:
    """Fetches Razorpay transactions (or accepts pre-built normalized
    records directly, for deterministic testing), reshapes them into
    the matcher's expected input, and runs them through the EXISTING
    match_records() function unchanged.

    Returns both the raw match results and a lookup of the full
    Razorpay-normalized record by bank_transaction_id, so downstream
    Case Management can see full Razorpay context (fees, tax, utr,
    mode) even though the matcher itself never saw those fields.
    """

    if normalized_records is None:
        normalized_records = adapter.fetch_transactions(source_type, count=count)

    bank_df = normalized_records_to_bank_dataframe(normalized_records)
    razorpay_records_by_bank_id = {r["transaction_id"]: r for r in normalized_records}

    # Reading the existing payments.csv directly — this is data loading,
    # not matcher logic, so it doesn't duplicate anything match_records()
    # itself does.
    payments_df = pd.read_csv(payments_path)

    results = match_records(payments_df, bank_df)  # EXISTING matcher, called unmodified

    return {
        "match_results": results,
        "razorpay_records_by_bank_id": razorpay_records_by_bank_id,
        "bank_df": bank_df,
    }
