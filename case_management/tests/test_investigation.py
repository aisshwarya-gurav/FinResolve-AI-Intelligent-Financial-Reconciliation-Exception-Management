"""
Minimal integration tests for case_management/investigation.py — the
wiring between Case Management and the existing RAG Explainer.

Uses a REAL F05 case (via ingest_finrca_three_way, the same ingestion
path used in production) rather than a hand-built fake case, per
instructions. The LLM call is mocked (no API key in this environment),
but retrieval, grounding verification, and audit logging all run for
real — only the final LLM response is substituted.
"""

import os
import sys
import tempfile
import json
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def seeded_client():
    """Fresh temp DB seeded with REAL F05/F06/F07 cases via the actual
    three-way ingestion path — not fabricated case data."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-investigation-32bytes-long"

    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    from case_management.ingestion import ingest_finrca_three_way
    ingest_finrca_three_way(api_module.store)

    from auth.models import Role
    from auth.tokens import create_access_token
    user = api_module.user_store.create_user(
        email="analyst@test.local",
        password="unit-test-password",
        display_name="Analyst",
        role=Role.ADMIN,
        user_id="analyst",
    )
    token = create_access_token(user.user_id, user.role)

    test_client = TestClient(api_module.app, headers={"Authorization": f"Bearer {token}"})
    yield test_client, api_module.store

    api_module.store.conn.close()
    api_module.user_store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass


def _get_known_f05_case_id(store) -> str:
    """Finds a real, already-ingested F05 case (payment_without_valid_invoice)
    to use as the integration test subject."""
    cases = store.list_cases(exception_code="payment_without_valid_invoice", limit=10)
    assert len(cases) > 0, "Expected at least one real F05 case from three-way ingestion"
    return cases[0].case_id


def test_investigate_known_f05_case_via_function(seeded_client, monkeypatch):
    """Direct call to investigate_case() against a real F05 case — mocks
    only the final LLM response, everything else (retrieval, grounding
    check, audit log write) runs for real."""
    _, store = seeded_client
    case_id = _get_known_f05_case_id(store)
    record = json.loads(store.get_case(case_id).record_json)

    good_response = json.dumps({
        "confirmed_facts": f"Payment {record['payment_id']} for {record['payment_amount']} "
                            f"{record['payment_currency']} has no invoice allocation.",
        "historical_precedent": "Consistent with prior unsupported manual payment cases.",
        "likely_hypothesis": "Likely an ad-hoc payment released without a supporting invoice.",
        "suggested_action": "escalate to AP team for manual review",
        "confidence": 0.7,
    })

    import case_management.investigation as investigation_module
    # Patch the LLM call at its source (agents.explainer_rag) — this is
    # the one call investigation.py depends on indirectly.
    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(explainer_rag_module, "call_llm", lambda *a, **kw: good_response)

    from case_management.investigation import investigate_case
    result = investigate_case(store, case_id, k=3)

    assert result["verified"] is True
    assert len(result["retrieved_case_ids"]) > 0  # retrieval actually ran


def test_investigation_logs_audit_event(seeded_client, monkeypatch):
    _, store = seeded_client
    case_id = _get_known_f05_case_id(store)

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(explainer_rag_module, "call_llm", lambda *a, **kw: json.dumps({
        "confirmed_facts": "Payment has no invoice allocation.",
        "historical_precedent": "No closely matching precedent.",
        "likely_hypothesis": "Possible manual payment.",
        "suggested_action": "escalate for manual review",
        "confidence": 0.5,
    }))

    from case_management.investigation import investigate_case
    investigate_case(store, case_id, k=3)

    events = store.get_audit_history(case_id)
    assert any(e.event_type == "rag_investigated" for e in events)


def test_investigation_preserves_grounding_verification(seeded_client, monkeypatch):
    """The critical check: if the LLM hallucinates a number not present
    in the current case, the wiring must still reject it — grounding
    verification must survive being called through Case Management,
    not just when explainer_rag is called directly."""
    _, store = seeded_client
    case_id = _get_known_f05_case_id(store)

    hallucinated_response = json.dumps({
        "confirmed_facts": "Payment for 999999.99 USD has no invoice allocation.",  # invented figure
        "historical_precedent": "No closely matching precedent.",
        "likely_hypothesis": "Unclear.",
        "suggested_action": "escalate for manual review",
        "confidence": 0.4,
    })

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(explainer_rag_module, "call_llm", lambda *a, **kw: hallucinated_response)

    from case_management.investigation import investigate_case
    result = investigate_case(store, case_id, k=3)

    assert result["verified"] is False


def test_explainer_accepts_source_and_derived_financial_facts(monkeypatch):
    record = {
        "invoice_amount": 6000.0,
        "payment_amount": 5500.0,
        "payment_id": "PAY_123",
    }

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(
        explainer_rag_module,
        "call_llm",
        lambda *a, **kw: json.dumps({
            "confirmed_facts": "Invoice amount is 6000.0 and payment amount is 5500.0.",
            "historical_precedent": "No closely matching precedent.",
            "likely_hypothesis": "The payment is 500 lower than the invoice.",
            "suggested_action": "escalate for manual review",
            "confidence": 0.8,
        }),
    )

    result = explainer_rag_module.explain_with_rag(record)
    assert result["verified"] is True


def test_explainer_accepts_residual_balance_as_derived_fact(monkeypatch):
    record = {
        "invoice_amount": 6000.0,
        "payment_amount": 5500.0,
    }

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(
        explainer_rag_module,
        "call_llm",
        lambda *a, **kw: json.dumps({
            "confirmed_facts": "Invoice amount is 6000 and payment amount is 5500.",
            "historical_precedent": "No closely matching precedent.",
            "likely_hypothesis": "The residual balance is 500.",
            "suggested_action": "escalate for manual review",
            "confidence": 0.9,
        }),
    )

    result = explainer_rag_module.explain_with_rag(record)
    assert result["verified"] is True


def test_explainer_rejects_historical_only_number(monkeypatch):
    record = {
        "invoice_amount": 6000.0,
        "payment_amount": 5500.0,
    }

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(
        explainer_rag_module,
        "call_llm",
        lambda *a, **kw: json.dumps({
            "confirmed_facts": "The current payment is 5500.",
            "historical_precedent": "No closely matching precedent.",
            "likely_hypothesis": "This follows a prior case where the amount was 1000.",
            "suggested_action": "escalate for manual review",
            "confidence": 0.7,
        }),
    )

    result = explainer_rag_module.explain_with_rag(record)
    assert result["verified"] is False
    assert "unverifiable" in result["confirmed_facts"].lower()


def test_explainer_marks_api_failure_as_llm_unavailable(monkeypatch):
    record = {"payment_id": "PAY_123", "payment_amount": 5500.0}

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(
        explainer_rag_module,
        "call_llm",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("quota exceeded")),
    )

    result = explainer_rag_module.explain_with_rag(record)
    assert result["verified"] is False
    assert "llm_call_failed" in result["confirmed_facts"].lower()
    assert result["suggested_action"] == "escalate for manual review"


def test_investigate_via_api_endpoint(seeded_client, monkeypatch):
    tc, store = seeded_client
    case_id = _get_known_f05_case_id(store)

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(explainer_rag_module, "call_llm", lambda *a, **kw: json.dumps({
        "confirmed_facts": "Payment has no invoice allocation.",
        "historical_precedent": "No closely matching precedent.",
        "likely_hypothesis": "Possible manual payment.",
        "suggested_action": "escalate for manual review",
        "confidence": 0.5,
    }))

    resp = tc.post(f"/cases/{case_id}/investigate")
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


def test_investigate_missing_case_returns_404(seeded_client):
    tc, _ = seeded_client
    resp = tc.post("/cases/does_not_exist/investigate")
    assert resp.status_code == 404
