"""
Focused tests for the two new TAT/SLA endpoints — specifically their
RBAC gating, using ONLY pre-existing permissions (VIEW_CASES,
EXPORT_DATA). No new permission or role is introduced anywhere.
"""

import os
import sys
import tempfile
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def client(monkeypatch):
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["CASE_DB_PATH"] = db_path
    os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-tat-api-32bytes-long"

    import importlib
    from case_management import api as api_module
    importlib.reload(api_module)

    from case_management.ingestion import ingest_synthetic
    ingest_synthetic(api_module.store)

    from auth.models import Role
    from auth.tokens import create_access_token

    admin = api_module.user_store.create_user(
        email="admin_tat@test.local", password="unit-test-password",
        display_name="Admin", role=Role.ADMIN, user_id="admin_tat",
    )
    viewer = api_module.user_store.create_user(
        email="viewer_tat@test.local", password="unit-test-password",
        display_name="Viewer", role=Role.VIEWER, user_id="viewer_tat",
    )
    admin_token = create_access_token(admin.user_id, admin.role)
    viewer_token = create_access_token(viewer.user_id, viewer.role)

    admin_client = TestClient(api_module.app, headers={"Authorization": f"Bearer {admin_token}"})
    viewer_client = TestClient(api_module.app, headers={"Authorization": f"Bearer {viewer_token}"})

    yield admin_client, viewer_client, api_module.store

    api_module.store.conn.close()
    api_module.user_store.conn.close()
    try:
        os.remove(db_path)
    except OSError:
        pass


def _first_case_id(admin_client) -> str:
    resp = admin_client.get("/cases")
    return resp.json()["cases"][0]["case_id"]


def test_admin_can_get_case_tat(client):
    admin_client, _, _ = client
    case_id = _first_case_id(admin_client)
    resp = admin_client.get(f"/cases/{case_id}/tat")
    assert resp.status_code == 200
    body = resp.json()
    assert "sla_status" in body
    assert "sla_deadline_iso" in body


def test_viewer_can_get_case_tat(client):
    """Viewer has VIEW_CASES — same permission that already gates
    viewing the case itself, so viewing its SLA is consistent."""
    admin_client, viewer_client, _ = client
    case_id = _first_case_id(admin_client)
    resp = viewer_client.get(f"/cases/{case_id}/tat")
    assert resp.status_code == 200


def test_case_tat_not_found_returns_404(client):
    admin_client, _, _ = client
    resp = admin_client.get("/cases/does_not_exist/tat")
    assert resp.status_code == 404


def test_admin_can_get_tat_summary(client):
    admin_client, _, _ = client
    resp = admin_client.get("/reports/tat-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_cases" in body
    assert "sla_compliance_percent" in body


def test_viewer_cannot_get_tat_summary(client):
    """Viewer lacks EXPORT_DATA — confirms the endpoint is genuinely
    permission-gated, not just reachable by anyone logged in."""
    _, viewer_client, _ = client
    resp = viewer_client.get("/reports/tat-summary")
    assert resp.status_code == 403


def test_unauthenticated_request_rejected(client):
    admin_client, _, _ = client
    case_id = _first_case_id(admin_client)
    import requests
    from fastapi.testclient import TestClient
    # A client with no Authorization header at all
    from case_management import api as api_module
    bare_client = TestClient(api_module.app)
    resp = bare_client.get(f"/cases/{case_id}/tat")
    assert resp.status_code == 401


def test_tat_summary_respects_filters(client):
    admin_client, _, _ = client
    resp = admin_client.get("/reports/tat-summary", params={"severity": "critical"})
    assert resp.status_code == 200
