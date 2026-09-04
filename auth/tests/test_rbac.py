"""
Authentication and RBAC tests for the Case Management API.

Uses an isolated temp SQLite DB (never case_management/cases.db).
LLM calls are mocked where investigate is exercised. No live APIs.
"""

import os
import sys
import tempfile
from datetime import timedelta

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from auth.models import Role
from auth.passwords import verify_password
from auth.tokens import create_access_token

TEST_JWT_SECRET = "pytest-jwt-secret-key-not-for-production"
TEST_PASSWORD = "unit-test-only-password"


@pytest.fixture
def api_env():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["JWT_SECRET_KEY"] = TEST_JWT_SECRET
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"

    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    from case_management.ingestion import ingest_synthetic
    ingest_synthetic(api_module.store)

    yield api_module
    api_module.store.conn.close()
    api_module.user_store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass


def _create_user(api_module, email, role, is_active=True, user_id=None):
    return api_module.user_store.create_user(
        email=email,
        password=TEST_PASSWORD,
        display_name=email.split("@")[0],
        role=role,
        is_active=is_active,
        user_id=user_id,
    )


def _authed_client(api_module, user):
    token = create_access_token(user.user_id, user.role)
    return TestClient(api_module.app, headers={"Authorization": f"Bearer {token}"}), token


def _bare_client(api_module):
    return TestClient(api_module.app)


def _first_case_id(client: TestClient) -> str:
    resp = client.get("/cases")
    assert resp.status_code == 200
    return resp.json()["cases"][0]["case_id"]


def test_valid_login(api_env):
    _create_user(api_env, "login-ok@test.local", Role.VIEWER, user_id="login-ok")
    resp = _bare_client(api_env).post(
        "/auth/login", json={"email": "login-ok@test.local", "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert "password" not in body
    assert "password_hash" not in body


def test_invalid_password(api_env):
    _create_user(api_env, "login-bad@test.local", Role.VIEWER)
    resp = _bare_client(api_env).post(
        "/auth/login", json={"email": "login-bad@test.local", "password": "wrong-password"}
    )
    assert resp.status_code == 401
    assert "password_hash" not in resp.json()


def test_inactive_user(api_env):
    user = _create_user(api_env, "inactive@test.local", Role.VIEWER, is_active=False)
    login = _bare_client(api_env).post(
        "/auth/login", json={"email": "inactive@test.local", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401
    assert login.json()["detail"] == "Account is inactive."

    client, _ = _authed_client(api_env, user)
    resp = client.get("/cases")
    assert resp.status_code == 401


def test_expired_jwt(api_env):
    user = _create_user(api_env, "expired@test.local", Role.VIEWER)
    token = create_access_token(user.user_id, user.role, expires_delta=timedelta(seconds=-30))
    client = TestClient(api_env.app, headers={"Authorization": f"Bearer {token}"})
    resp = client.get("/cases")
    assert resp.status_code == 401


def test_missing_token_returns_401(api_env):
    resp = _bare_client(api_env).get("/cases")
    assert resp.status_code == 401


def test_invalid_token_returns_401(api_env):
    client = TestClient(api_env.app, headers={"Authorization": "Bearer not-a-valid-token"})
    resp = client.get("/cases")
    assert resp.status_code == 401


def test_unauthorized_user_cannot_access_protected_endpoint(api_env):
    resp = _bare_client(api_env).post(
        "/cases/any-id/resolve",
        json={"resolved_by": "nobody", "resolution_note": "nope"},
    )
    assert resp.status_code == 401


def test_health_does_not_require_auth(api_env):
    resp = _bare_client(api_env).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_viewer_can_get_cases(api_env):
    user = _create_user(api_env, "viewer@test.local", Role.VIEWER)
    client, _ = _authed_client(api_env, user)
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert resp.json()["total"] > 0


def test_viewer_cannot_modify_case(api_env):
    user = _create_user(api_env, "viewer-mod@test.local", Role.VIEWER)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.patch(
        f"/cases/{case_id}/status",
        json={"status": "in_review", "actor": "spoofed"},
    )
    assert resp.status_code == 403


def test_analyst_can_investigate(api_env, monkeypatch):
    user = _create_user(api_env, "analyst@test.local", Role.FINANCE_ANALYST)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)

    import json
    import agents.explainer_rag as explainer_rag_module
    monkeypatch.setattr(explainer_rag_module, "call_llm", lambda *a, **kw: json.dumps({
        "confirmed_facts": "Record fields are present.",
        "historical_precedent": "No closely matching precedent.",
        "likely_hypothesis": "Possible timing difference.",
        "suggested_action": "escalate for manual review",
        "confidence": 0.5,
    }))

    resp = client.post(f"/cases/{case_id}/investigate")
    assert resp.status_code == 200
    assert "verified" in resp.json()


def test_analyst_cannot_resolve(api_env):
    user = _create_user(api_env, "analyst-res@test.local", Role.FINANCE_ANALYST)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.post(
        f"/cases/{case_id}/resolve",
        json={"resolved_by": "analyst", "resolution_note": "Should be forbidden."},
    )
    assert resp.status_code == 403


def test_manager_can_resolve(api_env):
    user = _create_user(api_env, "manager@test.local", Role.FINANCE_MANAGER)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.post(
        f"/cases/{case_id}/resolve",
        json={"resolved_by": "ignored", "resolution_note": "Confirmed bank fee."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_by"] == user.user_id


def test_auditor_can_view_audit(api_env):
    user = _create_user(api_env, "auditor@test.local", Role.AUDITOR)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.get(f"/cases/{case_id}/audit")
    assert resp.status_code == 200
    assert any(e["event_type"] == "created" for e in resp.json()["events"])


def test_auditor_cannot_change_status(api_env):
    user = _create_user(api_env, "auditor-st@test.local", Role.AUDITOR)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.patch(
        f"/cases/{case_id}/status",
        json={"status": "in_review", "actor": "auditor"},
    )
    assert resp.status_code == 403


def test_admin_can_manage_everything(api_env):
    admin = _create_user(api_env, "admin@test.local", Role.ADMIN, user_id="admin-user")
    client, _ = _authed_client(api_env, admin)

    cases = client.get("/cases")
    assert cases.status_code == 200
    case_id = cases.json()["cases"][0]["case_id"]

    status_resp = client.patch(
        f"/cases/{case_id}/status", json={"status": "in_review", "actor": "ignored"}
    )
    assert status_resp.status_code == 200

    created = client.post(
        "/auth/users",
        json={
            "email": "new-viewer@test.local",
            "password": TEST_PASSWORD,
            "display_name": "New Viewer",
            "role": "VIEWER",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "new-viewer@test.local"
    assert body["role"] == "VIEWER"
    assert "password_hash" not in body
    assert "password" not in body

    viewer_client, _ = _authed_client(
        api_env, api_env.user_store.get_by_email("new-viewer@test.local")
    )
    assert viewer_client.patch(
        f"/cases/{case_id}/status", json={"status": "escalated"}
    ).status_code == 403


def test_spoofed_actor_field_is_ignored(api_env):
    user = _create_user(api_env, "manager-spoof@test.local", Role.FINANCE_MANAGER)
    client, _ = _authed_client(api_env, user)
    case_id = _first_case_id(client)
    resp = client.patch(
        f"/cases/{case_id}/status",
        json={"status": "in_review", "actor": "totally-not-this-user"},
    )
    assert resp.status_code == 200
    events = client.get(f"/cases/{case_id}/audit").json()["events"]
    status_events = [e for e in events if e["event_type"] == "status_changed"]
    assert status_events
    assert status_events[-1]["actor"] == user.user_id
    assert status_events[-1]["actor"] != "totally-not-this-user"


def test_jwt_does_not_contain_password(api_env):
    user = _create_user(api_env, "jwt-check@test.local", Role.VIEWER)
    token = create_access_token(user.user_id, user.role)
    payload = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert set(payload.keys()) == {"user_id", "role", "exp"}
    assert "password" not in payload
    assert "password_hash" not in payload
    assert TEST_PASSWORD not in str(payload)


def test_password_is_stored_as_a_hash(api_env):
    user = _create_user(api_env, "hashed@test.local", Role.VIEWER)
    stored = api_env.user_store.get_by_email("hashed@test.local")
    assert stored.password_hash != TEST_PASSWORD
    assert stored.password_hash.startswith("$2")
    assert verify_password(TEST_PASSWORD, stored.password_hash)
    assert TEST_PASSWORD not in stored.password_hash
