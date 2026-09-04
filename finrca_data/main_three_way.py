"""
Orchestrator for the three-way (Invoice -> Payment -> Bank) pipeline.

Pipeline: ThreeWayMatcher (deterministic) -> Explainer (LLM, only on
exceptions, grounded + verified) -> Auditor (logs everything) ->
ground-truth evaluation against F05/F06/F07 (invoice-payment stage).

Run: python finrca_data/main_three_way.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from three_way_matcher import load_data, match_three_way
from explainer import explain_exception
from agents.auditor import log_event, clear_log

TARGET_FAILURE_TYPES = {
    "F05_PAYMENT_WITHOUT_VALID_INVOICE",
    "F06_INVOICE_PAID_TWICE",
    "F07_PARTIAL_PAYMENT_RESIDUAL_BALANCE",
}
MAX_TO_EXPLAIN = 15


def evaluate_invoice_stage(results: dict, ground_truth_cases: list) -> dict:
    gt_relevant = {c["primary_entity"]["id"]: c["failure_type"]
                   for c in ground_truth_cases if c["failure_type"] in TARGET_FAILURE_TYPES}
    hard_negatives = [c for c in ground_truth_cases
                       if c["failure_type"] == "NO_FAILURE" and c["primary_entity"]["type"] == "invoice"]

    found_f05 = {r["payment_id"] for r in results["payment_without_invoice"]}
    found_f06 = {r["invoice_id"] for r in results["overpaid_duplicate"]}
    found_f07 = {r["invoice_id"] for r in results["partial_payment"]}
    all_flagged_ids = found_f05 | found_f06 | found_f07 | {r["invoice_id"] for r in results["chain_bank_exception"]}

    caught = [gid for gid in gt_relevant if gid in (found_f05 | found_f06 | found_f07)]
    missed = [gid for gid in gt_relevant if gid not in (found_f05 | found_f06 | found_f07)]

    wrongly_flagged = [c["primary_entity"]["id"] for c in hard_negatives
                        if c["primary_entity"]["id"] in all_flagged_ids]
    correctly_left_alone = [c["primary_entity"]["id"] for c in hard_negatives
                             if c["primary_entity"]["id"] not in all_flagged_ids]

    return {
        "real_failures_total": len(gt_relevant),
        "real_failures_caught": len(caught),
        "real_failures_missed": len(missed),
        "recall": round(len(caught) / len(gt_relevant), 3) if gt_relevant else None,
        "hard_negatives_total": len(hard_negatives),
        "hard_negatives_correctly_left_alone": len(correctly_left_alone),
        "hard_negatives_wrongly_flagged": len(wrongly_flagged),
        "false_positive_rate": round(len(wrongly_flagged) / len(hard_negatives), 3) if hard_negatives else None,
    }


def run_three_way_pipeline():
    clear_log()

    print("Loading invoices, payment allocations, payments, and bank transactions...")
    invoices, allocations, payments, bank = load_data(
        invoices_path=os.path.join(os.path.dirname(__file__), "invoices.csv"),
        allocations_path=os.path.join(os.path.dirname(__file__), "payment_allocations.csv"),
        payments_path=os.path.join(os.path.dirname(__file__), "payments.csv"),
        bank_path=os.path.join(os.path.dirname(__file__), "bank_transactions.csv"),
    )
    results = match_three_way(invoices, allocations, payments, bank)

    print(f"Invoices marked 'paid': {len(invoices[invoices['status']=='paid'])}")
    print(f"Full chain complete (invoice->payment->bank): {len(results['chain_complete'])}")
    print(f"Chain bank-side exception:      {len(results['chain_bank_exception'])}")
    print(f"Partial payment (F07):          {len(results['partial_payment'])}")
    print(f"Overpaid/duplicate (F06):       {len(results['overpaid_duplicate'])}")
    print(f"Payment without invoice (F05):  {len(results['payment_without_invoice'])}")

    for r in results["chain_complete"]:
        log_event("three_way_matcher", r["invoice_id"], "chain_complete_clean", r)

    exceptions = (
        results["partial_payment"] + results["overpaid_duplicate"]
        + results["payment_without_invoice"] + results["chain_bank_exception"]
    )
    exceptions = exceptions[:MAX_TO_EXPLAIN]

    explanations = []
    print(f"\nExplaining {len(exceptions)} exceptions...")
    for record in exceptions:
        explanation = explain_exception(record)
        record_id = record.get("invoice_id") or record.get("payment_id")
        log_event(
            "explainer", record_id,
            "explained" if explanation["verified"] else "escalated_unverified",
            explanation,
        )
        explanations.append(explanation)

    verified_count = sum(1 for e in explanations if e["verified"])
    print(f"{verified_count}/{len(explanations)} explanations passed verification")

    print("\nEvaluating against real F05/F06/F07 ground truth...")
    ground_truth_path = os.path.join(os.path.dirname(__file__), "rca_ground_truth.jsonl")
    ground_truth_cases = [json.loads(l) for l in open(ground_truth_path)]
    gt_eval = evaluate_invoice_stage(results, ground_truth_cases)

    log_event("evaluator", "batch", "invoice_stage_ground_truth_evaluated", gt_eval)

    print("\n" + "=" * 60)
    print("THREE-WAY MATCH — GROUND-TRUTH EVALUATION (F05, F06, F07)")
    print("=" * 60)
    print(f"Real failures in dataset:     {gt_eval['real_failures_total']}")
    print(f"Caught:                        {gt_eval['real_failures_caught']}")
    print(f"Missed:                        {gt_eval['real_failures_missed']}")
    print(f"Recall:                        {gt_eval['recall']}")
    print()
    print(f"Hard negatives (invoice-side): {gt_eval['hard_negatives_total']}")
    print(f"Correctly left alone:          {gt_eval['hard_negatives_correctly_left_alone']}")
    print(f"Wrongly flagged:               {gt_eval['hard_negatives_wrongly_flagged']}")
    print(f"False positive rate:           {gt_eval['false_positive_rate']}")
    print("=" * 60)

    report = {
        "matcher_summary": {
            "invoices_evaluated": len(invoices[invoices["status"] == "paid"]),
            "chain_complete": len(results["chain_complete"]),
            "chain_bank_exception": len(results["chain_bank_exception"]),
            "partial_payment": len(results["partial_payment"]),
            "overpaid_duplicate": len(results["overpaid_duplicate"]),
            "payment_without_invoice": len(results["payment_without_invoice"]),
        },
        "explainer_summary": {
            "total_explained": len(explanations),
            "verified": verified_count,
        },
        "ground_truth_evaluation": gt_eval,
        "explanations": explanations,
    }

    out_path = os.path.join(os.path.dirname(__file__), "three_way_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report saved to {out_path}")

    return report


if __name__ == "__main__":
    run_three_way_pipeline()
