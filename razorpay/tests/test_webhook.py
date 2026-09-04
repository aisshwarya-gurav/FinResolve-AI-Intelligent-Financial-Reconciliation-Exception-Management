"""
Tests for Razorpay Webhook integration.

Verifies:
- Signature verification (HMAC-SHA256)
- Idempotency with webhook_events table
- Event filtering (payout.processed, payout.failed, payout.reversed vs unsupported)
- Reconciliation pipeline invocation and exception case creation
- Security (untrusted headers, malformed payloads, secret safety, no arbitrary case mutation)
"""

import os
import sys
import hmac
import json
import hashlib
import tempfile
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_WEBHOOK_SECRET = "test-webhook-secret-key-32bytes-min"


def _sign(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def webhook_client():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = TEST_WEBHOOK_SECRET
    os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-webhook-tests-32b"

    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    client = TestClient(api_module.app)
    yield client, api_module.store

    api_module.store.conn.close()
    api_module.user_store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass


def _sample_payout_payload(event_id="evt_001", payout_id="pout_test_001", status="processed", amount=150000, event="payout.processed"):
    return {
        "id": event_id,
        "entity": "event",
        "account_id": "acc_test123",
        "event": event,
        "contains": ["payout"],
        "payload": {
            "payout": {
                "entity": {
                    "id": payout_id,
                    "entity": "payout",
                    "fund_account_id": "fa_001",
                    "amount": amount,
                    "currency": "INR",
                    "fees": 590,
                    "tax": 90,
                    "status": status,
                    "utr": "UTRTEST9999",
                    "mode": "NEFT",
                    "purpose": "payout",
                    "created_at": 1740000000,
                }
            }
        },
        "created_at": 1740000000,
    }


def test_valid_signature_accepted(webhook_client):
    client, store = webhook_client
    payload = _sample_payout_payload(event_id="evt_valid_01")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert data["event_id"] == "evt_valid_01"


def test_invalid_signature_returns_400(webhook_client):
    client, _ = webhook_client
    payload = _sample_payout_payload()
    body = json.dumps(payload).encode("utf-8")

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "invalid_hex_signature", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Invalid or missing webhook signature" in resp.json()["detail"]


def test_missing_signature_returns_400(webhook_client):
    client, _ = webhook_client
    payload = _sample_payout_payload()
    body = json.dumps(payload).encode("utf-8")

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_missing_webhook_secret_returns_400(webhook_client, monkeypatch):
    client, _ = webhook_client
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    payload = _sample_payout_payload()
    body = json.dumps(payload).encode("utf-8")

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "any_sig", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Webhook secret is not configured" in resp.json()["detail"]


def test_malformed_json_returns_400(webhook_client):
    client, _ = webhook_client
    body = b"{not valid json"
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Malformed JSON payload" in resp.json()["detail"]


def test_non_dict_payload_returns_400(webhook_client):
    client, _ = webhook_client
    body = json.dumps(["an", "array"]).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_duplicate_event_returns_duplicate_and_does_not_reprocess(webhook_client):
    client, store = webhook_client
    payload = _sample_payout_payload(event_id="evt_dup_001", payout_id="pout_dup_001")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    # First delivery
    resp1 = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "processed"

    # Count cases after first delivery
    cases_before = store.count_cases()

    # Second delivery with exact same event_id
    resp2 = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"
    assert resp2.json()["event_id"] == "evt_dup_001"

    # Case count must not increase
    assert store.count_cases() == cases_before


def test_unsupported_event_type_ignored_safely(webhook_client):
    client, store = webhook_client
    payload = {
        "id": "evt_order_paid",
        "entity": "event",
        "event": "order.paid",
        "contains": ["order"],
        "payload": {"order": {"entity": {"id": "order_123"}}},
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert resp.json()["event_id"] == "evt_order_paid"

    event_record = store.get_webhook_event("evt_order_paid")
    assert event_record is not None
    assert event_record["status"] == "ignored"


def test_valid_payout_processed_creates_case_on_exception(webhook_client):
    client, store = webhook_client
    payout_id = "pout_uncorrelated_999"
    payload = _sample_payout_payload(event_id="evt_payout_proc", payout_id=payout_id, status="processed", amount=250000)
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert payout_id in data["cases_created"]

    # Verify case in store
    case = store.get_case(payout_id)
    assert case.source_pipeline == "razorpay"
    assert case.status == "open"
    record = json.loads(case.record_json)
    assert "razorpay_source_record" in record
    assert record["razorpay_source_record"]["fees"] == 5.90
    assert record["razorpay_source_record"]["tax"] == 0.90


def test_payout_failed_event_creates_case(webhook_client):
    client, store = webhook_client
    payout_id = "pout_failed_001"
    payload = _sample_payout_payload(event_id="evt_payout_failed", payout_id=payout_id, status="failed", event="payout.failed")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert payout_id in resp.json()["cases_created"]

    case = store.get_case(payout_id)
    assert case.source_pipeline == "razorpay"


def test_payout_reversed_event_creates_case(webhook_client):
    client, store = webhook_client
    payout_id = "pout_reversed_001"
    payload = _sample_payout_payload(event_id="evt_payout_rev", payout_id=payout_id, status="reversed", event="payout.reversed")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert payout_id in resp.json()["cases_created"]


def test_webhook_audit_trail_records_system_webhook_actor(webhook_client):
    client, store = webhook_client
    payout_id = "pout_audit_check_001"
    payload = _sample_payout_payload(event_id="evt_audit_001", payout_id=payout_id)
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    events = store.get_audit_history(payout_id)
    created_events = [e for e in events if e.event_type == "created"]
    assert len(created_events) > 0
    assert created_events[0].actor == "system:webhook"


def test_webhook_cannot_resolve_or_modify_cases(webhook_client):
    client, store = webhook_client
    # Create an initial case
    case = store.create_case(
        case_id="case_to_protect_01",
        source_pipeline="synthetic",
        exception_code="test_code",
        severity=case_management_models_severity(),
        priority=case_management_models_priority(),
        record={"some": "data"},
    )
    assert case.status == "open"

    # Webhook delivery attempting to target this case or pretend to be resolve
    payload = _sample_payout_payload(event_id="evt_try_resolve", payout_id="case_to_protect_01")
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )

    # Status must still be OPEN, never resolved
    case_after = store.get_case("case_to_protect_01")
    assert case_after.status == "open"
    assert case_after.resolved_by is None
    assert case_after.resolved_at is None


def test_secret_and_sensitive_payload_never_leaked_in_errors(webhook_client):
    client, _ = webhook_client
    body = b"malformed json with SECRET_DATA_123"
    sig = _sign(body)

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert TEST_WEBHOOK_SECRET not in resp.text
    assert "SECRET_DATA_123" not in resp.text


def case_management_models_severity():
    from case_management.models import Severity
    return Severity.MEDIUM


def case_management_models_priority():
    from case_management.models import Priority
    return Priority.MEDIUM
