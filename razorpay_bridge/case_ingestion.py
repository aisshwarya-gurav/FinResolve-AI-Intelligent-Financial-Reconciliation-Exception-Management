
"""
Bridges Razorpay reconciliation exceptions into Case Management.

Only Razorpay-relevant exception buckets are ingested:
  - amount_mismatch
  - bank_no_payment

Existing cases are not recreated or reported as newly created.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from case_management.store import CaseStore
from case_management.models import Severity, Priority

RELEVANT_BUCKETS = ("amount_mismatch", "bank_no_payment")


def _case_id_for_record(record: dict) -> str:
    return str(record.get("bank_transaction_id") or record.get("payment_id"))


def _compute_severity(record: dict) -> Severity:
    amount = 0.0

    for key in ("amount_diff", "bank_amount", "amount"):
        val = record.get(key)

        if val not in (None, ""):
            try:
                amount = abs(float(val))
                break
            except (TypeError, ValueError):
                continue

    if amount >= 10000:
        return Severity.CRITICAL
    elif amount >= 1000:
        return Severity.HIGH
    elif amount >= 100:
        return Severity.MEDIUM

    return Severity.LOW


def _severity_to_priority(severity: Severity) -> Priority:
    return {
        Severity.CRITICAL: Priority.HIGH,
        Severity.HIGH: Priority.HIGH,
        Severity.MEDIUM: Priority.MEDIUM,
        Severity.LOW: Priority.LOW,
    }[severity]


def ingest_razorpay_exceptions(
    store: CaseStore,
    pipeline_result: dict,
    actor: str = "system",
) -> list:
    """
    Creates cases only when they do not already exist.

    Returns only genuinely newly-created case IDs.
    """

    match_results = pipeline_result["match_results"]
    razorpay_records_by_bank_id = pipeline_result[
        "razorpay_records_by_bank_id"
    ]

    created_case_ids = []

    for bucket in RELEVANT_BUCKETS:
        for record in match_results.get(bucket, []):

            case_id = _case_id_for_record(record)
            bank_id = record.get("bank_transaction_id")

            # IMPORTANT:
            # Do not recreate or re-log an existing case.
            try:
                store.get_case(case_id)

                # Case already exists.
                continue

            except Exception:
                # Case does not exist, so create it below.
                pass

            razorpay_context = razorpay_records_by_bank_id.get(
                bank_id,
                {},
            )

            full_record = {
                **record,
                "razorpay_source_record": razorpay_context,
            }

            severity = _compute_severity(record)
            priority = _severity_to_priority(severity)
            exception_code = record.get("issue", bucket)

            store.create_case(
                case_id=case_id,
                source_pipeline="razorpay",
                exception_code=exception_code,
                severity=severity,
                priority=priority,
                record=full_record,
                actor=actor,
            )

            created_case_ids.append(case_id)

    return created_case_ids

