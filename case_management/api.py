"""
Case Management API.

Endpoints:
  GET    /cases
  GET    /cases/{case_id}
  PATCH  /cases/{case_id}/status
  PATCH  /cases/{case_id}/assign
  POST   /cases/{case_id}/notes
  POST   /cases/{case_id}/resolve
  GET    /cases/{case_id}/audit
  POST   /cases/{case_id}/investigate

Razorpay:
  POST   /reconciliation/razorpay
  POST   /webhooks/razorpay

Run:
  uvicorn case_management.api:app --reload --port 8001
"""

import os
import json

from dotenv import load_dotenv

load_dotenv()

from dataclasses import asdict

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Header,
    status,
)

from pydantic import BaseModel

from .store import (
    CaseStore,
    CaseNotFoundError,
    InvalidStatusTransitionError,
)

from .models import CaseStatus
from .investigation import investigate_case

from auth.models import Role, Permission, User
from auth.passwords import PasswordError
from auth.store import UserAlreadyExistsError, UserStore
from auth.deps import require_permission
from auth.router import router as auth_router

from razorpay.client import RazorpayAdapter
from razorpay.webhook import (
    verify_webhook_signature,
    handle_razorpay_webhook,
)

from razorpay_bridge.pipeline import run_razorpay_reconciliation
from razorpay_bridge.case_ingestion import ingest_razorpay_exceptions
from razorpay.settlement import run_settlement_reconciliation, ingest_settlement_exceptions


# -------------------------------------------------------------------
# Database
# -------------------------------------------------------------------

DB_PATH = os.environ.get(
    "CASE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "cases.db"),
)


# -------------------------------------------------------------------
# Application
# -------------------------------------------------------------------

app = FastAPI(
    title="Reconciliation Case Management API",
    version="1.0",
)

store = CaseStore(DB_PATH)

user_store = UserStore(DB_PATH)

app.state.user_store = user_store

app.include_router(auth_router)


# -------------------------------------------------------------------
# Admin bootstrap
# -------------------------------------------------------------------

def ensure_admin_user():
    email = os.environ.get("AUTH_ADMIN_EMAIL", "").strip()
    password = os.environ.get("AUTH_ADMIN_PASSWORD", "")

    if not email or not password:
        return

    try:
        user_store.get_by_email(email)

    except Exception:
        try:
            user_store.create_user(
                email=email,
                password=password,
                display_name=os.environ.get(
                    "AUTH_ADMIN_DISPLAY_NAME",
                    "Administrator",
                ),
                role=Role.ADMIN,
            )

        except UserAlreadyExistsError:
            pass

        except PasswordError as e:
            raise RuntimeError(
                f"Admin password configuration error: {e}"
            ) from e


ensure_admin_user()


# -------------------------------------------------------------------
# Request models
# -------------------------------------------------------------------

class StatusUpdateRequest(BaseModel):
    status: str
    actor: str = "unknown"
    reason: str | None = None


class AssignRequest(BaseModel):
    assignee: str
    actor: str = "unknown"


class NoteRequest(BaseModel):
    author: str = ""
    text: str


class ResolveRequest(BaseModel):
    resolved_by: str = ""
    resolution_note: str


class SettlementReconciliationRequest(BaseModel):
    settlement_records: list[dict] = []
    bank_records: list[dict] = []
    rule_name: str = "utr_then_net_amount"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _case_dict(case):
    return asdict(case)


# -------------------------------------------------------------------
# Health
# -------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# -------------------------------------------------------------------
# Razorpay reconciliation
# -------------------------------------------------------------------

@app.post("/reconciliation/razorpay")
def run_razorpay_reconciliation_endpoint(
    source_type: str = Query(default="payout"),
    count: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(
        require_permission(Permission.RUN_RECONCILIATION)
    ),
):
    """
    Runs Razorpay reconciliation using the existing matcher
    and creates Case Management cases for exceptions.
    """

    if source_type not in {"payout", "bank_transfer"}:
        raise HTTPException(
            status_code=400,
            detail="source_type must be 'payout' or 'bank_transfer'",
        )

    try:
        adapter = RazorpayAdapter()

        pipeline_result = run_razorpay_reconciliation(
            adapter=adapter,
            source_type=source_type,
            count=count,
        )

        created_case_ids = ingest_razorpay_exceptions(
            store=store,
            pipeline_result=pipeline_result,
            actor=current_user.user_id,
        )

        match_results = pipeline_result["match_results"]

        return {
            "status": "completed",
            "source": "razorpay",
            "source_type": source_type,
            "transactions_processed": count,
            "summary": {
                "matched": len(
                    match_results.get("matched", [])
                ),
                "amount_mismatch": len(
                    match_results.get("amount_mismatch", [])
                ),
                "bank_no_payment": len(
                    match_results.get("bank_no_payment", [])
                ),
                "payment_no_bank": len(
                    match_results.get("payment_no_bank", [])
                ),
            },
            "cases_created": len(created_case_ids),
            "case_ids": created_case_ids,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "Razorpay reconciliation failed: "
                f"{type(e).__name__}"
            ),
        ) from e


@app.post("/reconciliation/razorpay/settlement")
def run_razorpay_settlement_reconciliation_endpoint(
    body: SettlementReconciliationRequest,
    current_user: User = Depends(
        require_permission(Permission.RUN_RECONCILIATION)
    ),
):
    """Runs settlement reconciliation against a provided bank feed using the
    configured settlement matching rule while preserving the existing matcher
    and Case Management pathway."""
    try:
        pipeline_result = run_settlement_reconciliation(
            settlement_records=body.settlement_records,
            bank_records=body.bank_records,
            rule_name=body.rule_name,
        )
        created_case_ids = ingest_settlement_exceptions(
            store=store,
            pipeline_result=pipeline_result,
            actor=current_user.user_id,
        )
        return {
            "status": "completed",
            "source": "razorpay_settlement",
            "rule_used": pipeline_result["rule_used"],
            "summary": {
                "matched": len(pipeline_result["matched"]),
                "unmatched_settlement": len(pipeline_result["unmatched_settlement"]),
                "unmatched_bank": len(pipeline_result["unmatched_bank"]),
            },
            "cases_created": len(created_case_ids),
            "case_ids": created_case_ids,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive API guard
        raise HTTPException(
            status_code=500,
            detail=f"Settlement reconciliation failed: {type(exc).__name__}",
        ) from exc


# -------------------------------------------------------------------
# Razorpay webhook
# -------------------------------------------------------------------

@app.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(
        None,
        alias="X-Razorpay-Signature",
    ),
):
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook secret is not configured",
        )

    raw_body = await request.body()

    if not x_razorpay_signature or not verify_webhook_signature(
        raw_body,
        x_razorpay_signature,
        secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing webhook signature",
        )

    try:
        payload = json.loads(
            raw_body.decode("utf-8")
        )

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        ) from e

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload structure",
        )

    return handle_razorpay_webhook(
        store,
        raw_body,
        payload,
    )


# -------------------------------------------------------------------
# Case listing
# -------------------------------------------------------------------

@app.get("/cases")
def list_cases(
    exception_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(
        require_permission(Permission.VIEW_CASES)
    ),
):
    cases = store.list_cases(
        exception_code=exception_code,
        status=status,
        severity=severity,
        priority=priority,
        limit=limit,
        offset=offset,
    )

    total = store.count_cases(
        exception_code=exception_code,
        status=status,
        severity=severity,
        priority=priority,
    )

    return {
        "total": total,
        "count": len(cases),
        "cases": [
            _case_dict(case)
            for case in cases
        ],
    }


# -------------------------------------------------------------------
# Get one case
# -------------------------------------------------------------------

@app.get("/cases/{case_id}")
def get_case(
    case_id: str,
    _: User = Depends(
        require_permission(Permission.VIEW_CASES)
    ),
):
    try:
        case = store.get_case(case_id)

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    notes = store.get_notes(case_id)

    return {
        "case": _case_dict(case),
        "notes": [
            asdict(note)
            for note in notes
        ],
    }


# -------------------------------------------------------------------
# Update case status
# -------------------------------------------------------------------

@app.patch("/cases/{case_id}/status")
def update_status(
    case_id: str,
    body: StatusUpdateRequest,
    current_user: User = Depends(
        require_permission(Permission.CHANGE_STATUS)
    ),
):
    # ---------------------------------------------------------------
    # Validate requested status
    # ---------------------------------------------------------------

    try:
        new_status = CaseStatus(body.status)

    except ValueError:
        valid = [
            s.value
            for s in CaseStatus
        ]

        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid status '{body.status}'. "
                f"Valid values: {valid}"
            ),
        )

    # ---------------------------------------------------------------
    # Validate escalation reason BEFORE changing the case
    # ---------------------------------------------------------------

    if (
        new_status == CaseStatus.ESCALATED
        and (
            not body.reason
            or not body.reason.strip()
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="reason is required to escalate a case",
        )

    # ---------------------------------------------------------------
    # Perform status transition
    # ---------------------------------------------------------------

    try:
        case = store.update_status(
            case_id,
            new_status,
            actor=current_user.user_id,
            reason=body.reason,
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    except InvalidStatusTransitionError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    return _case_dict(case)


# -------------------------------------------------------------------
# Assign case
# -------------------------------------------------------------------

@app.patch("/cases/{case_id}/assign")
def assign_case(
    case_id: str,
    body: AssignRequest,
    current_user: User = Depends(
        require_permission(Permission.ASSIGN_CASE)
    ),
):
    try:
        case = store.assign_case(
            case_id,
            body.assignee,
            actor=current_user.user_id,
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    return _case_dict(case)


# -------------------------------------------------------------------
# Add note
# -------------------------------------------------------------------

@app.post("/cases/{case_id}/notes")
def add_note(
    case_id: str,
    body: NoteRequest,
    current_user: User = Depends(
        require_permission(Permission.ADD_NOTE)
    ),
):
    try:
        note = store.add_note(
            case_id,
            author=current_user.user_id,
            text=body.text,
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    return asdict(note)


# -------------------------------------------------------------------
# Resolve case
# -------------------------------------------------------------------

@app.post("/cases/{case_id}/resolve")
def resolve_case(
    case_id: str,
    body: ResolveRequest,
    current_user: User = Depends(
        require_permission(Permission.RESOLVE_CASE)
    ),
):
    if (
        not body.resolution_note
        or not body.resolution_note.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "resolution_note is required "
                "to resolve a case"
            ),
        )

    try:
        case = store.resolve_case(
            case_id,
            resolved_by=current_user.user_id,
            resolution_note=body.resolution_note,
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    except InvalidStatusTransitionError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    return _case_dict(case)


# -------------------------------------------------------------------
# Audit history
# -------------------------------------------------------------------

@app.get("/cases/{case_id}/audit")
def get_audit_history(
    case_id: str,
    _: User = Depends(
        require_permission(Permission.VIEW_AUDIT)
    ),
):
    try:
        events = store.get_audit_history(
            case_id
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    return {
        "case_id": case_id,
        "events": [
            asdict(event)
            for event in events
        ],
    }


# -------------------------------------------------------------------
# RAG Investigation
# -------------------------------------------------------------------

@app.post("/cases/{case_id}/investigate")
def investigate(
    case_id: str,
    k: int = Query(default=3),
    _: User = Depends(
        require_permission(Permission.INVESTIGATE_CASE)
    ),
):
    """
    Runs the RAG Explainer against the case's
    original exception record.

    The investigation:
      - retrieves historical cases
      - generates a grounded explanation
      - verifies grounding
      - records the investigation in the audit trail

    It does NOT automatically resolve or approve the case.
    """

    try:
        explanation = investigate_case(
            store,
            case_id,
            k=k,
        )

    except CaseNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Case '{case_id}' not found",
        )

    return explanation


# -------------------------------------------------------------------
# TAT / SLA — read-only, gated by permissions that already exist
# (VIEW_CASES, EXPORT_DATA). No new permission or role added.
# -------------------------------------------------------------------

from .tat import compute_case_tat, compute_tat_summary


@app.get("/cases/{case_id}/tat")
def get_case_tat(
    case_id: str,
    _: User = Depends(
        require_permission(Permission.VIEW_CASES)
    ),
):
    try:
        case = store.get_case(case_id)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    return compute_case_tat(case)


@app.get("/reports/tat-summary")
def get_tat_summary(
    exception_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    _: User = Depends(
        require_permission(Permission.EXPORT_DATA)
    ),
):
    """Aggregate TAT/SLA report — gated behind EXPORT_DATA, matching
    the permission Viewer alone lacks."""
    cases = store.list_cases(
        exception_code=exception_code, status=status,
        severity=severity, priority=priority, limit=5000,
    )
    return compute_tat_summary(cases)