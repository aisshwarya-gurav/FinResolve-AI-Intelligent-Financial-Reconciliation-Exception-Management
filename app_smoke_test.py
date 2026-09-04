"""
UI smoke test — exercises every function app.py calls, in the same
sequence a manual click-through would, WITHOUT a browser/Selenium
dependency (none was added, per instructions). This directly tests the
underlying functions app.py imports, not Streamlit's rendering layer —
appropriate here since app.py itself contains no new business logic,
only calls to already-tested functions.

Uses a throwaway temp database — never touches case_management/cases.db.

Run: python app_smoke_test.py
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

# Ensure no LLM key is present for this run, to test the no-API-key path deliberately
for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(var, None)

from case_management.store import CaseStore, CaseNotFoundError
from case_management.models import CaseStatus
from case_management.investigation import investigate_case
from case_management.ingestion import ingest_all
from razorpay.client import RazorpayAdapter
from razorpay_bridge.pipeline import run_razorpay_reconciliation
from razorpay_bridge.case_ingestion import ingest_razorpay_exceptions
from rag.retriever import retrieve_similar

PASS = []
FAIL = []


def check(name, condition):
    if condition:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}")


db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
store = CaseStore(db_path)

print("=== 1. Run Reconciliation (ingest_all, skip BenchRec) ===")
before = store.count_cases()
ingest_all(store, include_benchrec=False)
after = store.count_cases()
check("ingest_all creates cases", after > before)

print("\n=== 2. Case filtering (list_cases) ===")
all_cases = store.list_cases(limit=500)
check("cases exist after ingestion", len(all_cases) > 0)
open_cases = store.list_cases(status="open", limit=500)
check("status filter works", all(c.status == "open" for c in open_cases))

print("\n=== 3. Case selection + detail ===")
target = all_cases[0]
case = store.get_case(target.case_id)
check("get_case returns the selected case", case.case_id == target.case_id)
record = json.loads(case.record_json)
check("record_json parses to a dict", isinstance(record, dict))

print("\n=== 4. Retrieved precedent (no LLM needed) ===")
precedents = retrieve_similar(record, k=3)
check("retrieve_similar runs without an API key", isinstance(precedents, list))

print("\n=== 5. Investigate (no API key present — must not crash) ===")
try:
    result = investigate_case(store, case.case_id, k=3)
    investigate_crashed = False
except Exception:
    investigate_crashed = True
    result = None
check("investigate_case does not crash without an API key", not investigate_crashed)
check("investigate_case reports verified=False without an API key", result is not None and result.get("verified") is False)
check("investigate_case includes an escalation reason", result is not None and "human" in result.get("suggested_action", "").lower() or "review" in result.get("suggested_action", "").lower())

print("\n=== 6. Add note ===")
note = store.add_note(case.case_id, "smoke_test_author", "Smoke test note.")
check("add_note succeeds", note.text == "Smoke test note.")
check("note appears in get_notes", any(n.note_id == note.note_id for n in store.get_notes(case.case_id)))

print("\n=== 7. Assign ===")
assigned = store.assign_case(case.case_id, "smoke_test_assignee", "smoke_test_author")
check("assign_case succeeds", assigned.assignee == "smoke_test_assignee")

print("\n=== 8. Update status ===")
updated = store.update_status(case.case_id, CaseStatus.IN_REVIEW, "smoke_test_author")
check("update_status succeeds", updated.status == "in_review")

print("\n=== 9. Resolve ===")
resolved = store.resolve_case(case.case_id, "smoke_test_author", "Resolved during smoke test.")
check("resolve_case succeeds", resolved.status == "resolved")

print("\n=== 10. Audit history reflects full lifecycle ===")
events = store.get_audit_history(case.case_id)
event_types = [e.event_type for e in events]
check("audit trail includes created", "created" in event_types)
check("audit trail includes rag_investigated", "rag_investigated" in event_types)
check("audit trail includes note_added", "note_added" in event_types)
check("audit trail includes assigned", "assigned" in event_types)
check("audit trail includes status_changed", "status_changed" in event_types)
check("audit trail includes resolved", "resolved" in event_types)

print("\n=== 11. Razorpay mock flow -> matcher -> case ===")
adapter = RazorpayAdapter()
check("RazorpayAdapter defaults to mock mode without credentials", adapter.mock_mode is True)

pipeline_result = run_razorpay_reconciliation(adapter, source_type="payout", count=3)
check("run_razorpay_reconciliation returns match_results", "match_results" in pipeline_result)
check("mock Razorpay data produces bank_no_payment exceptions (expected — different ID space)",
      len(pipeline_result["match_results"]["bank_no_payment"]) == 3)

created_case_ids = ingest_razorpay_exceptions(store, pipeline_result)
check("ingest_razorpay_exceptions creates cases", len(created_case_ids) == 3)

razorpay_case = store.get_case(created_case_ids[0])
check("Razorpay-sourced case has source_pipeline='razorpay'", razorpay_case.source_pipeline == "razorpay")
razorpay_record = json.loads(razorpay_case.record_json)
check("Razorpay-sourced case preserves fees/tax/utr context",
      "razorpay_source_record" in razorpay_record and "fees" in razorpay_record["razorpay_source_record"])

store.conn.close()
try:
    os.remove(db_path)
except OSError:
    pass

print("\n" + "=" * 50)
print(f"SMOKE TEST RESULTS: {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 50)
if FAIL:
    print("FAILED CHECKS:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
