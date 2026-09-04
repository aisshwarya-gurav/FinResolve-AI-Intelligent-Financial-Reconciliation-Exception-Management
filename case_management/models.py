"""
Case Management — data models.

A "Case" wraps one exception from any of the four existing matcher
pipelines (synthetic, BenchRec, FinRCA two-way, FinRCA three-way) in a
workflow object a human can act on: change status, assign, annotate,
resolve. The underlying matchers are untouched — this is a layer on top,
not a replacement.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class CaseStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    INVESTIGATING = "investigating"
    IN_REVIEW = "in_review"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Status transitions allowed via the plain "update status" endpoint.
# RESOLVED is intentionally reachable only through the dedicated
# /resolve endpoint, so a resolution always carries a resolution note
# and resolver — never a bare status flip.
ALLOWED_STATUS_TRANSITIONS = {
    CaseStatus.OPEN: {CaseStatus.ASSIGNED, CaseStatus.IN_REVIEW, CaseStatus.PENDING_REVIEW, CaseStatus.RESOLVED, CaseStatus.ESCALATED},
    CaseStatus.ASSIGNED: {CaseStatus.INVESTIGATING, CaseStatus.IN_REVIEW, CaseStatus.PENDING_REVIEW, CaseStatus.RESOLVED, CaseStatus.ESCALATED},
    CaseStatus.INVESTIGATING: {CaseStatus.IN_REVIEW, CaseStatus.PENDING_REVIEW, CaseStatus.RESOLVED, CaseStatus.ESCALATED},
    CaseStatus.IN_REVIEW: {CaseStatus.PENDING_REVIEW, CaseStatus.APPROVED, CaseStatus.RESOLVED, CaseStatus.ESCALATED, CaseStatus.OPEN},
    CaseStatus.PENDING_REVIEW: {CaseStatus.APPROVED, CaseStatus.RESOLVED, CaseStatus.ESCALATED, CaseStatus.IN_REVIEW},
    CaseStatus.APPROVED: {CaseStatus.RESOLVED, CaseStatus.ESCALATED},
    CaseStatus.ESCALATED: {
        CaseStatus.OPEN,
        CaseStatus.ASSIGNED,
        CaseStatus.INVESTIGATING,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_REVIEW,
        CaseStatus.APPROVED,
        CaseStatus.RESOLVED,
    },
    CaseStatus.RESOLVED: set(),  # resolved cases are terminal via this endpoint
}


def normalize_status(value: str) -> CaseStatus:
    raw = (value or "").strip().lower()
    aliases = {
        "in_review": CaseStatus.IN_REVIEW,
        "pending_review": CaseStatus.PENDING_REVIEW,
        "assigned": CaseStatus.ASSIGNED,
        "investigating": CaseStatus.INVESTIGATING,
        "approved": CaseStatus.APPROVED,
        "escalated": CaseStatus.ESCALATED,
        "resolved": CaseStatus.RESOLVED,
        "open": CaseStatus.OPEN,
    }
    return aliases.get(raw, CaseStatus(raw))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Note:
    note_id: int
    case_id: str
    author: str
    text: str
    created_at: str = field(default_factory=now_iso)


@dataclass
class AuditEvent:
    event_id: int
    case_id: str
    event_type: str  # created | status_changed | assigned | note_added | resolved
    detail: str       # human-readable summary
    actor: Optional[str]
    created_at: str = field(default_factory=now_iso)


@dataclass
class Case:
    case_id: str
    source_pipeline: str      # synthetic | benchrec | finrca_two_way | finrca_three_way
    exception_code: str       # e.g. "amount_or_currency_mismatch"
    severity: Severity
    priority: Priority
    status: CaseStatus
    record_json: str          # the original exception record, as JSON text
    assignee: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
