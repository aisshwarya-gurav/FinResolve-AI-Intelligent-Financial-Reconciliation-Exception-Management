"""
Tests for the Case Management API.

Uses an isolated, temporary SQLite DB per test run (never touches
case_management/cases.db) and seeds it with a small set of known
synthetic exceptions via the ingestion layer — no dependency on
BenchRec (slow) or a live LLM (Explainer/Q&A agents aren't touched by
this API at all, so no key is needed to run these tests).

Run: pytest case_management/tests/ -v
"""

import os
import sys
import tempfile
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def client(monkeypatch):
    """Fresh temp DB + seeded cases for every test, and a fresh FastAPI
    app instance pointed at that DB — tests never share state."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-case-api-32bytes-long"

    # Import after setting the env var so the module-level `store =
    # CaseStore(DB_PATH)` in api.py picks up the temp path.
    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    from case_management.ingestion import ingest_synthetic
    ingest_synthetic(api_module.store)

    from auth.models import Role
    from auth.tokens import create_access_token
    user = api_module.user_store.create_user(
        email="alice@test.local",
        password="unit-test-password",
        display_name="Alice",
        role=Role.ADMIN,
        user_id="alice",
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


def _first_case_id(client) -> str:
    resp = client.get("/cases")
    assert resp.status_code == 200
    cases = resp.json()["cases"]
    assert len(cases) > 0, "Expected at least one seeded case"
    return cases[0]["case_id"]


# --- list cases --------------------------------------------------------

def test_list_cases_returns_seeded_data(client):
    tc, _ = client
    resp = tc.get("/cases")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["cases"]) == data["count"]


def test_list_cases_filter_by_status(client):
    tc, _ = client
    resp = tc.get("/cases", params={"status": "open"})
    assert resp.status_code == 200
    for case in resp.json()["cases"]:
        assert case["status"] == "open"


def test_list_cases_filter_by_severity(client):
    tc, _ = client
    resp = tc.get("/cases", params={"severity": "low"})
    assert resp.status_code == 200
    for case in resp.json()["cases"]:
        assert case["severity"] == "low"


def test_list_cases_filter_by_priority(client):
    tc, _ = client
    resp = tc.get("/cases", params={"priority": "high"})
    assert resp.status_code == 200
    for case in resp.json()["cases"]:
        assert case["priority"] == "high"


def test_list_cases_filter_by_exception_code(client):
    tc, _ = client
    all_cases = tc.get("/cases").json()["cases"]
    code = all_cases[0]["exception_code"]
    resp = tc.get("/cases", params={"exception_code": code})
    assert resp.status_code == 200
    for case in resp.json()["cases"]:
        assert case["exception_code"] == code


def test_list_cases_combined_filters_no_match_returns_empty(client):
    tc, _ = client
    resp = tc.get("/cases", params={"status": "resolved", "severity": "critical"})
    assert resp.status_code == 200
    assert resp.json()["cases"] == []


# --- get case by id ------------------------------------------------------

def test_get_case_by_id_success(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.get(f"/cases/{case_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case"]["case_id"] == case_id
    assert "notes" in body


def test_get_case_by_id_not_found(client):
    tc, _ = client
    resp = tc.get("/cases/does_not_exist_123")
    assert resp.status_code == 404


# --- update status ---------------------------------------------------------

def test_update_status_valid_transition(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "assigned"


def test_update_status_invalid_value_rejected(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/status", json={"status": "not_a_real_status", "actor": "alice"})
    assert resp.status_code == 400


def test_update_status_cannot_jump_directly_to_resolved(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/status", json={"status": "resolved", "actor": "alice"})
    assert resp.status_code == 400  # must use /resolve instead


def test_update_status_missing_case_404(client):
    tc, _ = client
    resp = tc.patch("/cases/nope/status", json={"status": "assigned", "actor": "alice"})
    assert resp.status_code == 404


def test_update_status_requires_reason_for_escalation(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/status", json={"status": "escalated", "actor": "alice"})
    assert resp.status_code == 400
    assert "reason is required" in resp.json()["detail"].lower()


def test_update_status_unauthorized_transition_forbidden(client):
    tc, store = client
    case_id = _first_case_id(client=tc)
    from auth.models import Role
    from auth.tokens import create_access_token
    from case_management import api as api_module

    user = api_module.user_store.create_user(
        email="viewer@test.local",
        password="unit-test-password",
        display_name="Viewer",
        role=Role.VIEWER,
        user_id="viewer",
    )
    token = create_access_token(user.user_id, user.role)
    viewer_client = TestClient(api_module.app, headers={"Authorization": f"Bearer {token}"})

    resp = viewer_client.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "viewer"})
    assert resp.status_code == 403


# --- assign ------------------------------------------------------------

def test_assign_case_success(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/assign", json={"assignee": "bob", "actor": "alice"})
    assert resp.status_code == 200
    assert resp.json()["assignee"] == "bob"


def test_assign_case_missing_case_404(client):
    tc, _ = client
    resp = tc.patch("/cases/nope/assign", json={"assignee": "bob", "actor": "alice"})
    assert resp.status_code == 404


# --- add note ------------------------------------------------------------

def test_add_note_success(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.post(f"/cases/{case_id}/notes", json={"author": "alice", "text": "Checked with vendor."})
    assert resp.status_code == 200
    assert resp.json()["text"] == "Checked with vendor."

    # Confirm it shows up when fetching the case
    detail = tc.get(f"/cases/{case_id}").json()
    assert any(n["text"] == "Checked with vendor." for n in detail["notes"])


def test_add_note_missing_case_404(client):
    tc, _ = client
    resp = tc.post("/cases/nope/notes", json={"author": "alice", "text": "hi"})
    assert resp.status_code == 404


# --- resolve ------------------------------------------------------------

def test_resolve_case_success(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "investigating", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "pending_review", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "approved", "actor": "alice"})
    resp = tc.post(f"/cases/{case_id}/resolve",
                    json={"resolved_by": "alice", "resolution_note": "Confirmed as a bank fee, no action needed."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_by"] == "alice"


def test_resolve_case_requires_note(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "investigating", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "pending_review", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "approved", "actor": "alice"})
    resp = tc.post(f"/cases/{case_id}/resolve", json={"resolved_by": "alice", "resolution_note": ""})
    assert resp.status_code == 400


def test_resolve_case_twice_fails(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "investigating", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "pending_review", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "approved", "actor": "alice"})
    tc.post(f"/cases/{case_id}/resolve", json={"resolved_by": "alice", "resolution_note": "First resolution."})
    resp = tc.post(f"/cases/{case_id}/resolve", json={"resolved_by": "bob", "resolution_note": "Second attempt."})
    assert resp.status_code == 400


def test_resolve_case_missing_case_404(client):
    tc, _ = client
    resp = tc.post("/cases/nope/resolve", json={"resolved_by": "alice", "resolution_note": "note"})
    assert resp.status_code == 404


# --- audit history ------------------------------------------------------

def test_audit_history_includes_created_event(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.get(f"/cases/{case_id}/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["event_type"] == "created" for e in events)


def test_audit_history_reflects_full_lifecycle(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)

    tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "investigating", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/assign", json={"assignee": "bob", "actor": "alice"})
    tc.post(f"/cases/{case_id}/notes", json={"author": "bob", "text": "Investigating."})
    tc.patch(f"/cases/{case_id}/status", json={"status": "pending_review", "actor": "alice"})
    tc.patch(f"/cases/{case_id}/status", json={"status": "approved", "actor": "alice"})
    tc.post(f"/cases/{case_id}/resolve", json={"resolved_by": "bob", "resolution_note": "Resolved."})

    events = tc.get(f"/cases/{case_id}/audit").json()["events"]
    event_types = [e["event_type"] for e in events]
    assert event_types == ["created", "status_changed", "status_changed", "assigned", "note_added", "status_changed", "status_changed", "resolved"]


def test_status_transition_uses_authenticated_actor_in_audit(client):
    tc, _ = client
    case_id = _first_case_id(client=tc)
    resp = tc.patch(f"/cases/{case_id}/status", json={"status": "assigned", "reason": "owned by analyst", "actor": "not_used"})
    assert resp.status_code == 200
    history = tc.get(f"/cases/{case_id}/audit").json()["events"]
    status_event = [e for e in history if e["event_type"] == "status_changed"][-1]
    assert status_event["actor"] == "alice"
    assert "not_used" not in status_event["detail"]


def test_audit_history_missing_case_404(client):
    tc, _ = client
    resp = tc.get("/cases/nope/audit")
    assert resp.status_code == 404


def test_investigate_does_not_auto_resolve_or_approve_case(client, monkeypatch):
    tc, store = client
    case_id = _first_case_id(client=tc)

    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(
        explainer_rag_module,
        "call_llm",
        lambda *a, **kw: '{"confirmed_facts": "Payment is tracked.", "historical_precedent": "No closely matching precedent.", "likely_hypothesis": "Likely a valid exception.", "suggested_action": "escalate for manual review", "confidence": 0.7}',
    )

    before = store.get_case(case_id)
    resp = tc.post(f"/cases/{case_id}/investigate", params={"k": 3})
    assert resp.status_code == 200
    after = store.get_case(case_id)
    assert after.status == before.status
    assert after.status != "resolved"
    assert after.status != "approved"
