
"""
AI Finance Controller - Reconciliation API

Pipeline:
Matcher -> Explainer -> Auditor -> Reporter

Run API:
    py -m uvicorn main:app --reload

Run standalone:
    py main.py
"""

import json

from fastapi import FastAPI

from agents.matcher import load_data, match_records
from agents.explainer import explain_exception
from agents.auditor import log_event, clear_log
from agents.reporter import build_report, print_summary


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="AI Finance Controller - Reconciliation API",
    version="1.0.0",
)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "AI Finance Controller",
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "message": "AI Finance Controller API is running",
        "docs": "/docs",
        "health": "/health",
    }


# ============================================================
# RECONCILIATION PIPELINE
# ============================================================

def run_pipeline():
    clear_log()

    # 1. Matcher
    ledger, bank = load_data()
    results = match_records(ledger, bank)

    # Log matched records
    for record in results["matched"]:
        log_event(
            "matcher",
            record["reference_id"],
            "clean_match",
            record,
        )

    # Log unmatched records
    for record in results["unmatched"]:
        log_event(
            "matcher",
            record["reference_id"],
            "no_bank_record",
            record,
        )

    # 2. Explainer
    # Only partial matches are sent to the LLM.
    explained = []

    for record in results["partial"]:
        explanation = explain_exception(record)

        log_event(
            "explainer",
            record["reference_id"],
            (
                "explained"
                if explanation["verified"]
                else "escalated_unverified"
            ),
            explanation,
        )

        explained.append(explanation)

    # 3. Reporter
    report = build_report(
        results["matched"],
        results["partial"],
        results["unmatched"],
        explained,
    )

    # Audit report generation
    log_event(
        "reporter",
        "batch",
        "report_generated",
        {
            "match_rate": report["match_rate_percent"]
        },
    )

    print_summary(report)

    # Save report
    with open("data/latest_report.json", "w") as f:
        json.dump(report, f, indent=2)

    print("\nFull report saved to data/latest_report.json")
    print("Audit trail saved to data/audit_log.jsonl")

    return report


# ============================================================
# API ENDPOINT
# ============================================================

@app.post("/reconciliation/run")
def reconciliation_run():
    report = run_pipeline()

    return {
        "status": "completed",
        "report": report,
    }


# ============================================================
# STANDALONE EXECUTION
# ============================================================

if __name__ == "__main__":
    run_pipeline()

