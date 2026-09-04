import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from razorpay.settlement import (
    SettlementMatchRule,
    run_settlement_reconciliation,
    validate_settlement_csv,
)


def test_settlement_rule_matches_by_utr_and_net_amount():
    settlement = {
        "transaction_id": "sett_001",
        "utr": "UTR-100",
        "gross_amount": "1500.00",
        "fee_amount": "10.00",
        "tax_amount": "2.50",
        "refund_amount": "0.00",
        "net_amount": "1487.50",
        "currency": "INR",
    }
    bank = {
        "bank_transaction_id": "bank_001",
        "utr": "UTR-100",
        "amount": "1487.50",
        "currency": "INR",
    }

    rule = SettlementMatchRule(name="utr_then_net_amount")
    result = rule.matches(settlement, bank)
    assert result is True


def test_run_settlement_reconciliation_returns_rule_and_summary():
    records = [{
        "transaction_id": "sett_001",
        "utr": "UTR-100",
        "gross_amount": "1500.00",
        "fee_amount": "10.00",
        "tax_amount": "2.50",
        "refund_amount": "0.00",
        "net_amount": "1487.50",
        "currency": "INR",
    }]
    bank_records = [{
        "bank_transaction_id": "bank_001",
        "utr": "UTR-100",
        "amount": "1487.50",
        "currency": "INR",
    }]

    result = run_settlement_reconciliation(records, bank_records, rule_name="utr_then_net_amount")
    assert result["rule_used"] == "utr_then_net_amount"
    assert len(result["matched"]) == 1
    assert len(result["unmatched_settlement"]) == 0


def test_validate_settlement_csv_requires_expected_columns():
    csv_text = "transaction_id,utr,gross_amount,fee_amount,tax_amount,refund_amount,net_amount,currency\nsett_1,UTR-1,100,5,2,0,93,INR\n"
    ok, errors = validate_settlement_csv(csv_text)
    assert ok is True
    assert errors == []

    csv_text_bad = "transaction_id,utr,gross_amount\nsett_1,UTR-1,100\n"
    ok, errors = validate_settlement_csv(csv_text_bad)
    assert ok is False
    assert any("net_amount" in err for err in errors)


def test_settlement_endpoint_works_in_api():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-settlement-32bytes-long"

    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    from auth.models import Role
    from auth.tokens import create_access_token
    from auth.store import UserStore
    user = UserStore(db_path).create_user(
        email="settlement@test.local",
        password="unit-test-password",
        display_name="Settlement",
        role=Role.ADMIN,
        user_id="settlement-admin",
    )
    token = create_access_token(user.user_id, user.role)
    client = TestClient(api_module.app, headers={"Authorization": f"Bearer {token}"})

    payload = {
        "settlement_records": [{
            "transaction_id": "sett_001",
            "utr": "UTR-100",
            "gross_amount": "1500.00",
            "fee_amount": "10.00",
            "tax_amount": "2.50",
            "refund_amount": "0.00",
            "net_amount": "1487.50",
            "currency": "INR",
        }],
        "bank_records": [{
            "bank_transaction_id": "bank_001",
            "utr": "UTR-100",
            "amount": "1487.50",
            "currency": "INR",
        }],
        "rule_name": "utr_then_net_amount",
    }

    resp = client.post("/reconciliation/razorpay/settlement", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["rule_used"] == "utr_then_net_amount"
    assert body["summary"]["matched"] == 1

    api_module.store.conn.close()
    api_module.user_store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass
