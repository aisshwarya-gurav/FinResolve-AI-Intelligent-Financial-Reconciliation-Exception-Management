"""
Tests for case_management/tat.py and its API endpoints.
"""

import os
import sys
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from case_management.store import CaseStore
from case_management.models import Severity, Priority
from case_management.tat import (
    compute_case_tat, compute_tat_summary, compute_exception_category_distribution,
    compute_compliance_trend, SLA_TARGET_HOURS, SLAStatus,
)


@pytest.fixture
def temp_store():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    store = CaseStore(db_path)
    yield store
    store.conn.close()
    os.remove(db_path)


def _backdate(store, case_id, hours_ago):
    old_time = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    store.conn.execute("UPDATE cases SET created_at = ? WHERE case_id = ?", (old_time, case_id))
    store.conn.commit()


# --- 1-4: per-severity SLA target calculation ------------------------------

def test_critical_sla_calculation(temp_store):
    temp_store.create_case(case_id="crit1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("crit1"))
    assert tat["sla_target_hours"] == 4


def test_high_sla_calculation(temp_store):
    temp_store.create_case(case_id="high1", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("high1"))
    assert tat["sla_target_hours"] == 24


def test_medium_sla_calculation(temp_store):
    temp_store.create_case(case_id="med1", source_pipeline="t", exception_code="x",
                            severity=Severity.MEDIUM, priority=Priority.MEDIUM, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("med1"))
    assert tat["sla_target_hours"] == 72


def test_low_sla_calculation(temp_store):
    temp_store.create_case(case_id="low1", source_pipeline="t", exception_code="x",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("low1"))
    assert tat["sla_target_hours"] == 168


def test_all_four_severities_have_distinct_targets():
    assert SLA_TARGET_HOURS[Severity.CRITICAL] < SLA_TARGET_HOURS[Severity.HIGH]
    assert SLA_TARGET_HOURS[Severity.HIGH] < SLA_TARGET_HOURS[Severity.MEDIUM]
    assert SLA_TARGET_HOURS[Severity.MEDIUM] < SLA_TARGET_HOURS[Severity.LOW]


# --- 5: deadline calculation ------------------------------------------------

def test_deadline_calculation(temp_store):
    temp_store.create_case(case_id="dl1", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    case = temp_store.get_case("dl1")
    tat = compute_case_tat(case)
    created = datetime.fromisoformat(case.created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    deadline = datetime.fromisoformat(tat["sla_deadline_iso"])
    assert abs((deadline - created).total_seconds() - 24 * 3600) < 2  # 24h target, allow rounding


# --- 6-8: the 4-state SLA status (PENDING / AT_RISK / MET / BREACHED) -----

def test_sla_met(temp_store):
    temp_store.create_case(case_id="met1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    temp_store.resolve_case("met1", resolved_by="a", resolution_note="done fast")
    tat = compute_case_tat(temp_store.get_case("met1"))
    assert tat["sla_status"] == SLAStatus.MET.value


def test_sla_breached_resolved_late(temp_store):
    temp_store.create_case(case_id="br1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "br1", hours_ago=10)  # > 4h target
    temp_store.resolve_case("br1", resolved_by="a", resolution_note="done late")
    tat = compute_case_tat(temp_store.get_case("br1"))
    assert tat["sla_status"] == SLAStatus.BREACHED.value
    assert tat["overdue_hours"] is not None


def test_sla_breached_still_open_past_deadline(temp_store):
    temp_store.create_case(case_id="br2", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "br2", hours_ago=48)  # > 24h target
    tat = compute_case_tat(temp_store.get_case("br2"))
    assert tat["sla_status"] == SLAStatus.BREACHED.value
    assert tat["overdue_hours"] > 0


def test_sla_at_risk(temp_store):
    """AT_RISK: open, past the 75% warning threshold, not yet breached."""
    temp_store.create_case(case_id="risk1", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "risk1", hours_ago=20)  # 20/24 = 83% of target, not yet breached
    tat = compute_case_tat(temp_store.get_case("risk1"))
    assert tat["sla_status"] == SLAStatus.AT_RISK.value
    assert tat["time_remaining_hours"] is not None
    assert tat["time_remaining_hours"] > 0


def test_sla_pending_well_within_target(temp_store):
    temp_store.create_case(case_id="pend1", source_pipeline="t", exception_code="x",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("pend1"))
    assert tat["sla_status"] == SLAStatus.PENDING.value


# --- 9: resolution time -----------------------------------------------------

def test_resolution_time_computed_correctly(temp_store):
    temp_store.create_case(case_id="res1", source_pipeline="t", exception_code="x",
                            severity=Severity.MEDIUM, priority=Priority.MEDIUM, record={}, actor="s")
    _backdate(temp_store, "res1", hours_ago=5)
    temp_store.resolve_case("res1", resolved_by="a", resolution_note="done")
    tat = compute_case_tat(temp_store.get_case("res1"))
    assert 4.9 <= tat["elapsed_hours"] <= 5.1
    assert tat["resolution_display"] is not None


# --- 10: SLA compliance percentage -----------------------------------------

def test_sla_compliance_percentage(temp_store):
    temp_store.create_case(case_id="c1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    temp_store.resolve_case("c1", resolved_by="a", resolution_note="fast")

    temp_store.create_case(case_id="c2", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "c2", hours_ago=10)
    temp_store.resolve_case("c2", resolved_by="a", resolution_note="late")

    cases = temp_store.list_cases(limit=100)
    summary = compute_tat_summary(cases)
    assert summary["sla_compliance_percent"] == 50.0  # 1 of 2 resolved cases within SLA


# --- compute_tat_summary — existing behavior preserved ---------------------

def test_summary_empty_cases_handled_gracefully():
    summary = compute_tat_summary([])
    assert summary["total_cases"] == 0
    assert summary["sla_compliance_percent"] is None


def test_summary_aggregates_correctly(temp_store):
    temp_store.create_case(case_id="s1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    temp_store.resolve_case("s1", resolved_by="a", resolution_note="fast")

    temp_store.create_case(case_id="s2", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "s2", hours_ago=48)

    cases = temp_store.list_cases(limit=100)
    summary = compute_tat_summary(cases)

    assert summary["total_cases"] == 2
    assert summary["resolved_count"] == 1
    assert summary["open_count"] == 1
    assert summary["breached_open_count"] == 1
    assert "s2" in summary["breached_open_case_ids"]
    assert summary["sla_compliance_percent"] == 100.0


# --- 11: dashboard KPI calculations ------------------------------------------

def test_dashboard_kpi_calculations(temp_store):
    temp_store.create_case(case_id="k1", source_pipeline="t", exception_code="x",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    temp_store.create_case(case_id="k2", source_pipeline="t", exception_code="x",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    temp_store.resolve_case("k2", resolved_by="a", resolution_note="done")

    cases = temp_store.list_cases(limit=100)
    summary = compute_tat_summary(cases)
    assert summary["total_cases"] == 2
    assert summary["open_count"] == 1
    assert summary["critical_open_count"] == 1
    assert summary["met_count"] + summary["breached_count"] == 1  # the resolved one


# --- 12: dashboard filters (exercised via CaseStore.list_cases, already tested
#         elsewhere — here confirming TAT computation works correctly on a
#         pre-filtered subset, which is how the dashboard will use it) --------

def test_tat_summary_works_on_filtered_subset(temp_store):
    temp_store.create_case(case_id="f1", source_pipeline="t", exception_code="amount_mismatch",
                            severity=Severity.CRITICAL, priority=Priority.HIGH, record={}, actor="s")
    temp_store.create_case(case_id="f2", source_pipeline="t", exception_code="bank_no_payment",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")

    filtered = temp_store.list_cases(severity="critical", limit=100)
    summary = compute_tat_summary(filtered)
    assert summary["total_cases"] == 1


# --- 13: case-level SLA (the fields the Investigation page will render) ----

def test_case_level_sla_has_all_display_fields(temp_store):
    temp_store.create_case(case_id="cl1", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    tat = compute_case_tat(temp_store.get_case("cl1"))
    for field in ("sla_status", "sla_deadline_iso", "sla_target_hours", "time_remaining_display"):
        assert field in tat


def test_case_level_sla_breached_shows_overdue_not_remaining(temp_store):
    temp_store.create_case(case_id="cl2", source_pipeline="t", exception_code="x",
                            severity=Severity.HIGH, priority=Priority.HIGH, record={}, actor="s")
    _backdate(temp_store, "cl2", hours_ago=48)
    tat = compute_case_tat(temp_store.get_case("cl2"))
    assert tat["overdue_display"] is not None
    assert tat["time_remaining_display"] is None


# --- exception category distribution ----------------------------------------

def test_exception_category_distribution(temp_store):
    temp_store.create_case(case_id="e1", source_pipeline="t", exception_code="amount_mismatch",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    temp_store.create_case(case_id="e2", source_pipeline="t", exception_code="amount_mismatch",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    temp_store.create_case(case_id="e3", source_pipeline="t", exception_code="bank_no_payment",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")

    cases = temp_store.list_cases(limit=100)
    dist = compute_exception_category_distribution(cases)
    assert dist["amount_mismatch"] == 2
    assert dist["bank_no_payment"] == 1


# --- compliance trend --------------------------------------------------------

def test_compliance_trend_returns_requested_number_of_days(temp_store):
    temp_store.create_case(case_id="t1", source_pipeline="t", exception_code="x",
                            severity=Severity.LOW, priority=Priority.LOW, record={}, actor="s")
    temp_store.resolve_case("t1", resolved_by="a", resolution_note="done")

    cases = temp_store.list_cases(limit=100)
    trend = compute_compliance_trend(cases, days=7)
    assert len(trend) == 7
    assert trend[-1]["date"] == datetime.now(timezone.utc).date().isoformat()  # today is last (most recent)


def test_compliance_trend_empty_cases_no_crash():
    trend = compute_compliance_trend([], days=5)
    assert len(trend) == 5
    assert all(t["compliance_percent"] is None for t in trend)
