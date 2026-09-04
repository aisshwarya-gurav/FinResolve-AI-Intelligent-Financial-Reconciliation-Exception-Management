"""
Unit tests for the razorpay/ module. Per instructions, the real
Razorpay API is NEVER called here — mock mode and mocked `requests`
calls only.

Run: pytest razorpay/tests/test_adapter.py -v
"""

import os
import sys
import logging
import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from razorpay.client import RazorpayAdapter, RazorpayAPIError, RazorpayTimeoutError
from razorpay.normalizer import normalize_transaction, NormalizationError
from razorpay.fixtures import MOCK_PAYOUT_TRANSACTION, MOCK_BANK_TRANSFER_TRANSACTION, get_mock_transactions


# --- mock mode ------------------------------------------------------------

def test_mock_mode_active_without_credentials(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    adapter = RazorpayAdapter()
    assert adapter.mock_mode is True


def test_mock_mode_off_with_credentials_present(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter()
    assert adapter.mock_mode is False


def test_explicit_mock_mode_true_overrides_credentials(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=True)
    assert adapter.mock_mode is True


def test_real_mode_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(RazorpayAPIError):
        RazorpayAdapter(mock_mode=False)


def test_fetch_transactions_mock_mode_payout(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    adapter = RazorpayAdapter()
    results = adapter.fetch_transactions("payout", count=5)
    assert len(results) == 5
    assert all(r["source_type"] == "payout" for r in results)


def test_fetch_transactions_mock_mode_bank_transfer(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    adapter = RazorpayAdapter()
    results = adapter.fetch_transactions("bank_transfer", count=3)
    assert len(results) == 3
    assert all(r["source_type"] == "bank_transfer" for r in results)


def test_fetch_transactions_invalid_source_type_raises(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    adapter = RazorpayAdapter()
    with pytest.raises(ValueError):
        adapter.fetch_transactions("not_a_real_source_type")


# --- normalization ----------------------------------------------------

def test_normalize_payout_transaction_preserves_all_required_fields():
    result = normalize_transaction(MOCK_PAYOUT_TRANSACTION, "payout")
    for field in ("transaction_id", "source_id", "amount", "currency", "debit",
                  "credit", "fees", "tax", "status", "utr", "mode", "created_at"):
        assert field in result

    assert result["transaction_id"] == "txn_00000000000001"
    assert result["source_id"] == "pout_00000000000001"
    assert result["amount"] == 10000.0    # 1000000 paise -> 10000.00
    assert result["debit"] == 10000.0
    assert result["credit"] == 0.0
    assert result["fees"] == 5.9          # 590 paise -> 5.90
    assert result["tax"] == 0.9           # 90 paise -> 0.90
    assert result["status"] == "processed"
    assert result["utr"] == "HDFCN00000000001"
    assert result["mode"] == "NEFT"
    assert result["currency"] == "INR"


def test_normalize_bank_transfer_transaction():
    result = normalize_transaction(MOCK_BANK_TRANSFER_TRANSACTION, "bank_transfer")
    assert result["transaction_id"] == "txn_00000000000002"
    assert result["credit"] == 5000.0
    assert result["debit"] == 0.0
    assert result["fees"] == 0.0   # no fees field on this source type
    assert result["tax"] == 0.0
    assert result["status"] == "settled"  # defaulted, no status field on this source
    assert result["utr"] is None
    assert result["mode"] is None


def test_normalize_converts_unix_timestamp_to_iso():
    result = normalize_transaction(MOCK_PAYOUT_TRANSACTION, "payout")
    assert result["created_at"] == "2018-12-21T09:01:10+00:00"


def test_normalize_invalid_source_type_raises():
    with pytest.raises(NormalizationError):
        normalize_transaction(MOCK_PAYOUT_TRANSACTION, "not_a_real_type")


def test_normalize_missing_id_raises():
    with pytest.raises(NormalizationError):
        normalize_transaction({"amount": 100}, "payout")


def test_normalize_handles_missing_source_gracefully():
    raw = {"id": "txn_x", "amount": 1000, "currency": "INR", "debit": 1000, "credit": 0, "created_at": 1600000000}
    result = normalize_transaction(raw, "payout")
    assert result["fees"] == 0.0
    assert result["tax"] == 0.0
    assert result["status"] == "settled"


# --- real-mode error handling (mocked requests, never a live call) ------

class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def test_real_mode_success_calls_requests_get_with_basic_auth(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=False)

    captured = {}

    def fake_get(url, auth=None, params=None, timeout=None):
        captured["url"] = url
        captured["auth"] = auth
        captured["params"] = params
        return _FakeResponse(200, {"items": [MOCK_PAYOUT_TRANSACTION]})

    monkeypatch.setattr(requests, "get", fake_get)
    results = adapter.fetch_transactions("payout", count=1)

    assert captured["auth"] == ("rzp_test_fake_id", "fake_secret_value")
    assert "/transactions" in captured["url"]
    assert len(results) == 1
    assert results[0]["transaction_id"] == "txn_00000000000001"


def test_real_mode_http_error_raises_api_error(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=False)

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(401, {"error": "unauthorized"}))

    with pytest.raises(RazorpayAPIError):
        adapter.fetch_transactions("payout")


def test_real_mode_timeout_raises_timeout_error(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=False)

    def raise_timeout(*a, **kw):
        raise requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(requests, "get", raise_timeout)

    with pytest.raises(RazorpayTimeoutError):
        adapter.fetch_transactions("payout")


def test_real_mode_connection_error_raises_api_error(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=False)

    def raise_conn_error(*a, **kw):
        raise requests.exceptions.ConnectionError("simulated connection failure")

    monkeypatch.setattr(requests, "get", raise_conn_error)

    with pytest.raises(RazorpayAPIError):
        adapter.fetch_transactions("payout")


def test_real_mode_filters_by_source_type(monkeypatch):
    """The fetch-all endpoint isn't documented to support a source-type
    filter param, so filtering happens client-side — confirm it works."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake_id")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret_value")
    adapter = RazorpayAdapter(mock_mode=False)

    mixed_items = [MOCK_PAYOUT_TRANSACTION, MOCK_BANK_TRANSFER_TRANSACTION]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(200, {"items": mixed_items}))

    payout_results = adapter.fetch_transactions("payout")
    assert len(payout_results) == 1
    assert payout_results[0]["source_type"] == "payout"

    bank_results = adapter.fetch_transactions("bank_transfer")
    assert len(bank_results) == 1
    assert bank_results[0]["source_type"] == "bank_transfer"


# --- credential safety --------------------------------------------------

def test_credentials_never_appear_in_logs(monkeypatch, caplog):
    secret_key_id = "rzp_test_SUPER_SECRET_ID"
    secret_key_secret = "SUPER_SECRET_VALUE_DO_NOT_LOG"
    monkeypatch.setenv("RAZORPAY_KEY_ID", secret_key_id)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", secret_key_secret)

    with caplog.at_level(logging.DEBUG):
        adapter = RazorpayAdapter(mock_mode=False)

        def raise_error(*a, **kw):
            raise requests.exceptions.ConnectionError("simulated failure")
        monkeypatch.setattr(requests, "get", raise_error)

        try:
            adapter.fetch_transactions("payout")
        except RazorpayAPIError:
            pass

    all_log_text = " ".join(record.getMessage() for record in caplog.records)
    assert secret_key_id not in all_log_text
    assert secret_key_secret not in all_log_text


def test_credentials_never_appear_in_exception_messages(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_SUPER_SECRET_ID")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "SUPER_SECRET_VALUE_DO_NOT_LOG")
    adapter = RazorpayAdapter(mock_mode=False)

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse(500, {}))

    try:
        adapter.fetch_transactions("payout")
        assert False, "expected RazorpayAPIError"
    except RazorpayAPIError as e:
        assert "SUPER_SECRET" not in str(e)


# --- integrity with real (non-fabricated) fixture shape -----------------

def test_get_mock_transactions_returns_distinct_ids():
    items = get_mock_transactions("payout", count=5)
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
