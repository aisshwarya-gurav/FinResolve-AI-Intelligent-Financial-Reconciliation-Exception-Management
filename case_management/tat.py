"""
Turn-Around-Time (TAT) / SLA tracking.

Computed entirely from data that already exists (Case.created_at,
Case.resolved_at, Case.severity) — no schema change, no new storage.
Confirmed: case_management/store.py and models.py needed zero changes
for any of this.

SLA targets are keyed on Severity (CRITICAL/HIGH/MEDIUM/LOW — the field
with four levels), deliberately explicit and editable in one place —
the "right" target hours are a business decision, not an engineering
one, and Priority (only three levels: LOW/MEDIUM/HIGH) doesn't map onto
a four-tier SLA table.
"""

from datetime import datetime, timezone, timedelta
from statistics import mean, median
from enum import Enum

from .models import Case, Severity

# Hours within which a case of this severity should be resolved.
# Reasonable, editable defaults — not fabricated as if industry-mandated.
SLA_TARGET_HOURS = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 24,
    Severity.MEDIUM: 72,
    Severity.LOW: 168,  # 7 days
}

# An open case crossing this fraction of its target time, without yet
# breaching, is flagged AT_RISK rather than a plain "pending" state —
# gives operators a warning before the deadline actually passes.
AT_RISK_THRESHOLD = 0.75


class SLAStatus(str, Enum):
    PENDING = "pending"       # open, still well within target
    AT_RISK = "at_risk"       # open, past the warning threshold, not yet breached
    MET = "met"                # resolved within target
    BREACHED = "breached"      # resolved late, OR open and past deadline


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_duration(hours: float) -> str:
    """e.g. 2.23 -> '02h 14m'. Always non-negative; caller decides the sign's meaning."""
    total_minutes = round(abs(hours) * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}h {m:02d}m"


def compute_case_tat(case: Case) -> dict:
    """Returns full TAT/SLA facts for a single case — deadline, time
    remaining or overdue, and a definite 4-state SLA status. Works
    whether the case is resolved or still open."""

    created = _parse_iso(case.created_at)
    severity = Severity(case.severity) if not isinstance(case.severity, Severity) else case.severity
    target_hours = SLA_TARGET_HOURS.get(severity, SLA_TARGET_HOURS[Severity.MEDIUM])
    deadline = created + timedelta(hours=target_hours)

    is_resolved = case.status == "resolved" and case.resolved_at is not None

    if is_resolved:
        resolved = _parse_iso(case.resolved_at)
        elapsed_hours = round((resolved - created).total_seconds() / 3600, 2)
        breached = elapsed_hours > target_hours
        sla_status = SLAStatus.BREACHED if breached else SLAStatus.MET
        time_remaining_hours = None
        overdue_hours = round(elapsed_hours - target_hours, 2) if breached else None
    else:
        now = datetime.now(timezone.utc)
        elapsed_hours = round((now - created).total_seconds() / 3600, 2)
        breached = elapsed_hours > target_hours
        if breached:
            sla_status = SLAStatus.BREACHED
            time_remaining_hours = None
            overdue_hours = round(elapsed_hours - target_hours, 2)
        else:
            fraction_elapsed = elapsed_hours / target_hours if target_hours else 1.0
            sla_status = SLAStatus.AT_RISK if fraction_elapsed >= AT_RISK_THRESHOLD else SLAStatus.PENDING
            time_remaining_hours = round(target_hours - elapsed_hours, 2)
            overdue_hours = None

    return {
        "case_id": case.case_id,
        "severity": severity.value,
        "sla_target_hours": target_hours,
        "sla_deadline_iso": deadline.isoformat(),
        "elapsed_hours": elapsed_hours,
        "is_resolved": is_resolved,
        "sla_breached": breached,          # kept for backward compatibility
        "sla_status": sla_status.value,
        "time_remaining_hours": time_remaining_hours,
        "time_remaining_display": _format_duration(time_remaining_hours) if time_remaining_hours is not None else None,
        "overdue_hours": overdue_hours,
        "overdue_display": _format_duration(overdue_hours) if overdue_hours is not None else None,
        "resolution_display": _format_duration(elapsed_hours) if is_resolved else None,
        "status_label": (
            ("Resolved within SLA" if not breached else "Resolved LATE (SLA breached)")
            if is_resolved else
            ("Open, within SLA" if not breached else "Open, OVERDUE (SLA breached)")
        ),
    }


def compute_tat_summary(cases: list) -> dict:
    """Aggregate TAT/SLA stats across a set of cases — the numbers a
    dashboard or a manager's weekly report actually wants."""

    if not cases:
        return {
            "total_cases": 0, "resolved_count": 0, "open_count": 0,
            "sla_compliance_percent": None, "avg_resolution_hours": None,
            "median_resolution_hours": None, "breached_open_count": 0,
            "breached_open_case_ids": [], "at_risk_count": 0, "met_count": 0,
            "breached_count": 0, "pending_count": 0, "critical_open_count": 0,
            "by_severity": {},
        }

    per_case = [compute_case_tat(c) for c in cases]

    resolved = [c for c in per_case if c["is_resolved"]]
    open_cases = [c for c in per_case if not c["is_resolved"]]
    breached_open = [c for c in open_cases if c["sla_breached"]]

    resolved_within_sla = [c for c in resolved if not c["sla_breached"]]
    compliance_pct = (
        round(len(resolved_within_sla) / len(resolved) * 100, 1) if resolved else None
    )

    resolution_hours = [c["elapsed_hours"] for c in resolved]

    status_counts = {s.value: 0 for s in SLAStatus}
    for c in per_case:
        status_counts[c["sla_status"]] += 1

    critical_open = [c for c in per_case if c["severity"] == Severity.CRITICAL.value and not c["is_resolved"]]

    by_severity = {}
    for sev in Severity:
        sev_cases = [c for c in per_case if c["severity"] == sev.value]
        sev_resolved = [c for c in sev_cases if c["is_resolved"]]
        by_severity[sev.value] = {
            "total": len(sev_cases),
            "resolved": len(sev_resolved),
            "avg_resolution_hours": round(mean([c["elapsed_hours"] for c in sev_resolved]), 2) if sev_resolved else None,
            "sla_target_hours": SLA_TARGET_HOURS[sev],
        }

    return {
        "total_cases": len(cases),
        "resolved_count": len(resolved),
        "open_count": len(open_cases),
        "sla_compliance_percent": compliance_pct,
        "avg_resolution_hours": round(mean(resolution_hours), 2) if resolution_hours else None,
        "median_resolution_hours": round(median(resolution_hours), 2) if resolution_hours else None,
        "breached_open_count": len(breached_open),
        "breached_open_case_ids": [c["case_id"] for c in breached_open],
        "pending_count": status_counts[SLAStatus.PENDING.value],
        "at_risk_count": status_counts[SLAStatus.AT_RISK.value],
        "met_count": status_counts[SLAStatus.MET.value],
        "breached_count": status_counts[SLAStatus.BREACHED.value],
        "critical_open_count": len(critical_open),
        "by_severity": by_severity,
    }


def compute_exception_category_distribution(cases: list) -> dict:
    """Counts per exception_code — for the Exception Category Distribution chart."""
    counts = {}
    for c in cases:
        code = c.exception_code or "unknown"
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def compute_compliance_trend(cases: list, days: int = 30) -> list:
    """Daily SLA compliance % for resolved cases, most recent `days`
    days — for the SLA Compliance Trend chart. Returns a list of
    {date, resolved_count, compliant_count, compliance_percent} dicts,
    oldest first, so it plots left-to-right chronologically."""
    per_case = [compute_case_tat(c) for c in cases if c.status == "resolved" and c.resolved_at]

    by_day = {}
    for case, tat in zip([c for c in cases if c.status == "resolved" and c.resolved_at], per_case):
        day = _parse_iso(case.resolved_at).date().isoformat()
        by_day.setdefault(day, {"resolved": 0, "compliant": 0})
        by_day[day]["resolved"] += 1
        if not tat["sla_breached"]:
            by_day[day]["compliant"] += 1

    today = datetime.now(timezone.utc).date()
    trend = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        stats = by_day.get(day, {"resolved": 0, "compliant": 0})
        pct = round(stats["compliant"] / stats["resolved"] * 100, 1) if stats["resolved"] else None
        trend.append({
            "date": day, "resolved_count": stats["resolved"],
            "compliant_count": stats["compliant"], "compliance_percent": pct,
        })
    return trend
