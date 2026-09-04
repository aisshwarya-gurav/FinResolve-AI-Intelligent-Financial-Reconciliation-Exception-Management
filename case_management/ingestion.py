"""
Case Management — ingestion.

Pulls exceptions out of the four EXISTING matcher pipelines and turns
each into a Case. Every import below is read-only use of code that
already exists elsewhere in this project — nothing in agents/,
finrca_data/, or real_data/ is modified.
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "finrca_data"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "real_data"))

from .models import Severity, Priority
from .store import CaseStore

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _case_id(record: dict) -> str:
    for key in ("payment_id", "bank_transaction_id", "invoice_id", "B_id", "reference_id"):
        if key in record and record[key]:
            return str(record[key])
    return f"case_{abs(hash(json.dumps(record, default=str))) % 10**8}"


def _extract_amount(record: dict) -> float:
    """Best-effort extraction of a money figure from a record, for
    severity scoring. Different matchers name this differently."""
    for key in ("amount_diff", "bank_amount_diff", "diff", "payment_amount",
                "ledger_amount", "invoice_total", "amount"):
        if key in record and record[key] not in (None, ""):
            try:
                return abs(float(record[key]))
            except (TypeError, ValueError):
                continue
    return 0.0


HIGH_RISK_EXCEPTION_CODES = {
    "no_bank_transaction_found", "payment_without_valid_invoice",
    "allocations_exceed_invoice_total", "no_payment_record_found",
}


def _compute_severity(exception_code: str, record: dict) -> Severity:
    amount = _extract_amount(record)
    if exception_code in HIGH_RISK_EXCEPTION_CODES:
        return Severity.HIGH if amount < 10000 else Severity.CRITICAL
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


def _ingest_record(store: CaseStore, record: dict, exception_code: str, source_pipeline: str):
    case_id = _case_id(record)
    severity = _compute_severity(exception_code, record)
    priority = _severity_to_priority(severity)
    store.create_case(
        case_id=case_id, source_pipeline=source_pipeline, exception_code=exception_code,
        severity=severity, priority=priority, record=record,
    )


def ingest_synthetic(store: CaseStore):
    from agents.matcher import load_data, match_records
    ledger, bank = load_data(
        os.path.join(ROOT, "data/ledger.csv"), os.path.join(ROOT, "data/bank_statement.csv")
    )
    results = match_records(ledger, bank)
    for r in results["partial"]:
        _ingest_record(store, r, "amount_or_date_mismatch", "synthetic")
    for r in results["unmatched"]:
        _ingest_record(store, r, r.get("issue", "unmatched"), "synthetic")


def ingest_benchrec(store: CaseStore, sample_b: int = 150):
    from real_matcher import load_sides, RealDataMatcher
    a_side, b_side = load_sides(
        os.path.join(ROOT, "real_data/BenchRec_cash_v1.0_train.csv"), sample_b=sample_b
    )
    matcher = RealDataMatcher(a_side)
    results = matcher.match_batch(b_side)
    for r in results:
        if r["status"] != "matched":
            _ingest_record(store, r, r["status"], "benchrec")


def ingest_finrca_two_way(store: CaseStore):
    from matcher import load_data, match_records
    payments, bank = load_data(
        os.path.join(ROOT, "finrca_data/payments.csv"),
        os.path.join(ROOT, "finrca_data/bank_transactions.csv"),
    )
    results = match_records(payments, bank)
    for bucket in ("amount_mismatch", "payment_no_bank", "bank_no_payment"):
        for r in results[bucket]:
            _ingest_record(store, r, r.get("issue", bucket), "finrca_two_way")


def ingest_finrca_three_way(store: CaseStore):
    from three_way_matcher import load_data, match_three_way
    invoices, allocations, payments, bank = load_data(
        os.path.join(ROOT, "finrca_data/invoices.csv"),
        os.path.join(ROOT, "finrca_data/payment_allocations.csv"),
        os.path.join(ROOT, "finrca_data/payments.csv"),
        os.path.join(ROOT, "finrca_data/bank_transactions.csv"),
    )
    results = match_three_way(invoices, allocations, payments, bank)
    for bucket in ("partial_payment", "overpaid_duplicate", "payment_without_invoice", "chain_bank_exception"):
        for r in results[bucket]:
            _ingest_record(store, r, r.get("issue", bucket), "finrca_three_way")


def ingest_all(store: CaseStore, include_benchrec: bool = True):
    ingest_synthetic(store)
    ingest_finrca_two_way(store)
    ingest_finrca_three_way(store)
    if include_benchrec:
        ingest_benchrec(store)


if __name__ == "__main__":
    store = CaseStore(os.path.join(os.path.dirname(__file__), "cases.db"))
    ingest_all(store)
    print(f"Total cases ingested: {store.count_cases()}")
