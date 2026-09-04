"""
Razorpay Webhook Handler — verifies HMAC signatures, enforces idempotency,
normalizes incoming payout events, and reconciles against internal records.
"""

import os
import hmac
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from case_management.store import CaseStore
from case_management.models import Severity, Priority
from razorpay_bridge.pipeline import run_razorpay_reconciliation
from razorpay_bridge.case_ingestion import ingest_razorpay_exceptions

logger = logging.getLogger(__name__)

SUPPORTED_PAYOUT_EVENTS = {
    "payout.processed",
    "payout.failed",
    "payout.reversed",
}


def verify_webhook_signature(raw_body: bytes, signature: Optional[str], secret: Optional[str]) -> bool:
    """Verifies the X-Razorpay-Signature HMAC-SHA256 signature using constant-time comparison.
    Never logs the secret or sensitive body content.
    """
    if not signature or not secret or not isinstance(raw_body, bytes):
        return False

    computed = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


def normalize_payout_entity(entity: dict) -> dict:
    """Normalizes a raw Razorpay payout entity into our standard record schema."""
    created_at_unix = entity.get("created_at")
    created_at_iso = None
    if created_at_unix is not None:
        try:
            created_at_iso = datetime.fromtimestamp(int(created_at_unix), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            created_at_iso = None

    def _to_decimal(val) -> float:
        if val is None:
            return 0.0
        try:
            return round(float(val) / 100, 2)
        except (TypeError, ValueError):
            return 0.0

    amount = _to_decimal(entity.get("amount"))
    fees = _to_decimal(entity.get("fees"))
    tax = _to_decimal(entity.get("tax"))
    payout_id = entity.get("id")

    return {
        "transaction_id": payout_id,
        "source_id": payout_id,
        "source_type": "payout",
        "amount": amount,
        "currency": entity.get("currency", "INR"),
        "debit": amount,
        "credit": 0.0,
        "fees": fees,
        "tax": tax,
        "status": entity.get("status") or "processed",
        "utr": entity.get("utr"),
        "mode": entity.get("mode"),
        "created_at": created_at_iso,
        "failure_reason": entity.get("failure_reason"),
    }


def handle_razorpay_webhook(store: CaseStore, raw_body: bytes, payload: dict) -> dict:
    """Processes a verified Razorpay webhook payload:
    1. Idempotency registration in webhook_events.
    2. Event type filtering.
    3. Normalization and reconciliation.
    4. Case ingestion on exception (with actor 'system:webhook').
    """
    event_id = payload.get("id") or payload.get("event_id")
    if not event_id:
        event_id = f"evt_{hashlib.sha256(raw_body).hexdigest()[:24]}"

    payload_hash = hashlib.sha256(raw_body).hexdigest()
    event_type = payload.get("event", "unknown")

    payout_entity = payload.get("payload", {}).get("payout", {}).get("entity") if isinstance(payload.get("payload", {}), dict) else None
    if not isinstance(payout_entity, dict):
        payout_entity = payload.get("payout") if isinstance(payload.get("payout"), dict) else None
    payment_id = None
    entity_id = None
    amount = None
    if isinstance(payout_entity, dict):
        payment_id = payout_entity.get("id")
        entity_id = payout_entity.get("entity")
        amount = payout_entity.get("amount")

    # Idempotency check: atomic insert into webhook_events
    is_new = store.record_webhook_event(
        event_id=event_id,
        event_type=event_type,
        payload_hash=payload_hash,
        status="received",
        payment_id=payment_id,
        entity_id=entity_id,
        amount=float(amount / 100) if isinstance(amount, (int, float)) else None,
    )
    if not is_new:
        logger.info("Duplicate webhook event received (event_id=%s)", event_id)
        return {"status": "duplicate", "event_id": event_id}

    if event_type not in SUPPORTED_PAYOUT_EVENTS:
        logger.info("Ignoring unsupported webhook event (event_type=%s, event_id=%s)", event_type, event_id)
        store.update_webhook_event_status(event_id, "ignored")
        return {"status": "ignored", "event_id": event_id}

    # Extract payout entity from standard Razorpay webhook wrapper
    payout_payload = payload.get("payload", {})
    payout_entity = (
        (payout_payload.get("payout", {}) or {}).get("entity")
        or payload.get("payout")
        or (payload.get("entity") if isinstance(payload.get("entity"), dict) and payload.get("entity", {}).get("entity") == "payout" else None)
    )

    if not isinstance(payout_entity, dict) or not payout_entity.get("id"):
        store.update_webhook_event_status(event_id, "ignored")
        return {"status": "ignored", "event_id": event_id, "detail": "No payout entity in payload"}

    normalized = normalize_payout_entity(payout_entity)

    # Run through existing reconciliation pipeline
    pipeline_result = run_razorpay_reconciliation(
        adapter=None,
        source_type="payout",
        normalized_records=[normalized],
    )

    # Ingest exceptions if generated by matcher (e.g. bank_no_payment, amount_mismatch)
    created_case_ids = ingest_razorpay_exceptions(
        store,
        pipeline_result,
        actor="system:webhook",
    )

    # Handle payout.failed or payout.reversed if not already flagged by amount/id matcher
    payout_status = (normalized.get("status") or "").lower()
    if payout_status in ("failed", "reversed") and not created_case_ids:
        case_id = str(normalized["transaction_id"])
        severity = Severity.HIGH if payout_status == "failed" else Severity.MEDIUM
        priority = Priority.HIGH if payout_status == "failed" else Priority.MEDIUM
        store.create_case(
            case_id=case_id,
            source_pipeline="razorpay",
            exception_code=f"payout_{payout_status}",
            severity=severity,
            priority=priority,
            record={
                "payment_id": None,
                "bank_transaction_id": case_id,
                "issue": f"payout_{payout_status}",
                "amount": normalized["amount"],
                "currency": normalized["currency"],
                "razorpay_source_record": normalized,
            },
            actor="system:webhook",
        )
        created_case_ids.append(case_id)

    store.update_webhook_event_status(
        event_id,
        "processed",
        payment_id=normalized.get("source_id") or normalized.get("transaction_id"),
        entity_id=normalized.get("source_type"),
        amount=float(normalized.get("amount") or 0.0),
        duplicate=False,
    )
    return {
        "status": "processed",
        "event_id": event_id,
        "cases_created": created_case_ids,
    }
