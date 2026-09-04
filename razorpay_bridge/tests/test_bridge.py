"""
Integration tests for razorpay_bridge/.

Per instructions: mock mode only, no real API calls, existing matcher/
Case Management logic reused unmodified (imported, never reimplemented).

Deterministic 'successful match' and 'mismatch' cases are built from a
REAL row in finrca_data/payments.csv (payment_id PAY_0420001, reference
PMTREF-0000000043, EUR 5866.68) — a mock Razorpay Transaction is
hand-built to correlate with it, since real mock Razorpay fixtures use
Razorpay's own ID space and won't naturally overlap with this project's
ERP reference numbers (confirmed and expected — see the 'missing
record' test, which uses the actual, unmodified mock fixtures for
exactly that reason).

Run: pytest razorpay_bridge/tests/test_bridge.py -v
"""

import os
import sys
import tempfile
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from razorpay.client import RazorpayAdapter
from razorpay_bridge.to_matcher_format import normalized_records_to_bank_dataframe, REQUIRED_MATCHER_BANK_COLUMNS
from razorpay_bridge.pipeline import run_razorpay_reconciliation
from razorpay_bridge.case_ingestion import ingest_razorpay_exceptions
from case_management.store import CaseStore

REAL_PAYMENT_REFERENCE = "PMTREF-0000000043"  # real row in finrca_data/payments.csv
REAL_PAYMENT_AMOUNT = 5866.68
REAL_PAYMENT_CURRENCY = "EUR"


def _build_normalized_record(transaction_id, source_id, amount, currency, created_at="2026-05-13T08:00:00+00:00"):
    """Builds a record in the EXACT shape razorpay/normalizer.py
    produces (transaction_id, source_id, amount, currency, debit,
    credit, fees, tax, status, utr, mode, created_at) — used here to
    deterministically test the bridge's own logic, independent of
    which real Razorpay transaction happened to be mock-generated."""
    return {
        "transaction_id": transaction_id,
        "source_id": source_id,
        "source_type": "payout",
        "amount": amount,
        "currency": currency,
        "debit": amount,
        "credit": 0.0,
        "fees": 5.9,
        "tax": 0.9,
        "status": "processed",
        "utr": "UTRTEST0000001",
        "mode": "NEFT",
        "created_at": created_at,
    }


@pytest.fixture
def temp_store():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    store = CaseStore(db_path)
    yield store
    store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass


# --- to_matcher_format.py -----------------------------------------------

def test_bank_dataframe_has_exact_required_columns():
    records = [_build_normalized_record("txn_A", "pout_A", 100.0, "INR")]
    df = normalized_records_to_bank_dataframe(records)
    assert list(df.columns) == REQUIRED_MATCHER_BANK_COLUMNS


def test_bank_dataframe_empty_input_returns_empty_dataframe_with_correct_columns():
    df = normalized_records_to_bank_dataframe([])
    assert len(df) == 0
    assert list(df.columns) == REQUIRED_MATCHER_BANK_COLUMNS


def test_bank_dataframe_maps_fields_correctly():
    record = _build_normalized_record("txn_X", "pout_X", 250.5, "USD", created_at="2026-01-15T10:00:00+00:00")
    df = normalized_records_to_bank_dataframe([record])
    row = df.iloc[0]
    assert row["bank_transaction_id"] == "txn_X"
    assert row["payment_reference"] == "pout_X"
    assert row["amount"] == 250.5
    assert row["currency"] == "USD"
    assert row["transaction_date"] == "2026-01-15"
    assert row["counterparty_token"] == "NEFT"


# --- pipeline.py: successful match ---------------------------------------

def test_pipeline_produces_successful_match_for_correlated_transaction():
    """A Razorpay transaction whose source_id equals a real payment's
    reference_number, with matching amount/currency, should land in
    'matched' — proving the bridge's data correctly reaches and is
    accepted by the EXISTING matcher's success path."""
    record = _build_normalized_record(
        "txn_match_test", REAL_PAYMENT_REFERENCE, REAL_PAYMENT_AMOUNT, REAL_PAYMENT_CURRENCY
    )
    adapter = RazorpayAdapter()  # unused here (normalized_records passed directly), but required by the function signature
    result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    matched_ids = [m["bank_transaction_id"] for m in result["match_results"]["matched"]]
    assert "txn_match_test" in matched_ids


# --- pipeline.py: mismatch ----------------------------------------------

def test_pipeline_produces_amount_mismatch_for_wrong_amount():
    """Same reference as a real payment, but the wrong amount — should
    land in 'amount_mismatch', proving the existing matcher's mismatch
    detection logic still runs correctly on bridged data."""
    wrong_amount = REAL_PAYMENT_AMOUNT + 500.00
    record = _build_normalized_record(
        "txn_mismatch_test", REAL_PAYMENT_REFERENCE, wrong_amount, REAL_PAYMENT_CURRENCY
    )
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    mismatch_ids = [m["bank_transaction_id"] for m in result["match_results"]["amount_mismatch"]]
    assert "txn_mismatch_test" in mismatch_ids


# --- pipeline.py: missing record ----------------------------------------

def test_pipeline_produces_missing_record_for_uncorrelated_mock_data():
    """Real mock Razorpay fixtures (unmodified, from razorpay/fixtures.py)
    use Razorpay's own ID space and won't correlate with any real
    payments.csv reference — every one should land in 'bank_no_payment'.
    This is the natural, expected, honest outcome, not a bug."""
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", count=5)

    assert len(result["match_results"]["bank_no_payment"]) == 5
    assert len(result["match_results"]["matched"]) == 0
    assert len(result["match_results"]["amount_mismatch"]) == 0


def test_pipeline_preserves_existing_matcher_results_for_untouched_payments():
    """Every real payment in payments.csv should still correctly show
    up as 'payment_no_bank' when the bank feed is entirely Razorpay
    data that doesn't correlate with it — proving the existing
    matcher's behavior on the payments side is unaffected by this
    bridge, not silently altered."""
    import pandas as pd
    payments_count = len(pd.read_csv("finrca_data/payments.csv"))

    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", count=3)

    assert len(result["match_results"]["payment_no_bank"]) == payments_count


# --- case_ingestion.py: case creation -------------------------------------

def test_ingest_creates_case_for_missing_record_exception(temp_store):
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", count=2)

    case_ids = ingest_razorpay_exceptions(temp_store, result)
    assert len(case_ids) == 2

    case = temp_store.get_case(case_ids[0])
    assert case.source_pipeline == "razorpay"
    assert case.exception_code == "no_payment_record_found"


def test_ingest_creates_case_for_mismatch_exception(temp_store):
    record = _build_normalized_record(
        "txn_mismatch_case", REAL_PAYMENT_REFERENCE, REAL_PAYMENT_AMOUNT + 500.0, REAL_PAYMENT_CURRENCY
    )
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    case_ids = ingest_razorpay_exceptions(temp_store, result)
    assert "txn_mismatch_case" in case_ids

    case = temp_store.get_case("txn_mismatch_case")
    assert case.exception_code == "amount_or_currency_mismatch"


def test_ingest_preserves_full_razorpay_context_in_case_record(temp_store):
    """The matcher never saw fees/tax/utr/mode — but the case stored in
    Case Management should still have them, proving isolation was
    preserved without losing information downstream."""
    record = _build_normalized_record("txn_context_test", "pout_context_test", 42.0, "INR")
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    case_ids = ingest_razorpay_exceptions(temp_store, result)
    case = temp_store.get_case(case_ids[0])
    stored_record = json.loads(case.record_json)

    assert "razorpay_source_record" in stored_record
    assert stored_record["razorpay_source_record"]["fees"] == 5.9
    assert stored_record["razorpay_source_record"]["utr"] == "UTRTEST0000001"


def test_ingest_creates_no_cases_when_no_exceptions(temp_store):
    """Successful matches shouldn't create cases at all."""
    record = _build_normalized_record(
        "txn_clean_match", REAL_PAYMENT_REFERENCE, REAL_PAYMENT_AMOUNT, REAL_PAYMENT_CURRENCY
    )
    adapter = RazorpayAdapter()
    result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    case_ids = ingest_razorpay_exceptions(temp_store, result)
    assert case_ids == []


# --- end-to-end: one full mock transaction -> exception -> case ------------

def test_end_to_end_mock_transaction_to_exception_to_case(temp_store):
    """The full requested demonstration in one test: a single mock
    Razorpay transaction, through the unmodified matcher, into an
    unmodified Case Management case, available for investigation."""
    record = _build_normalized_record("txn_e2e_demo", "pout_e2e_unrelated", 777.77, "INR")

    adapter = RazorpayAdapter()
    pipeline_result = run_razorpay_reconciliation(adapter, source_type="payout", normalized_records=[record])

    assert len(pipeline_result["match_results"]["bank_no_payment"]) == 1

    case_ids = ingest_razorpay_exceptions(temp_store, pipeline_result)
    assert case_ids == ["txn_e2e_demo"]

    case = temp_store.get_case("txn_e2e_demo")
    assert case.status == "open"  # ready for investigation, untouched so far
    assert case.source_pipeline == "razorpay"

    # Confirm it's queryable through Case Management's normal filtering,
    # same as any other case — no special-casing needed downstream.
    filtered = temp_store.list_cases(exception_code="no_payment_record_found")
    assert any(c.case_id == "txn_e2e_demo" for c in filtered)
