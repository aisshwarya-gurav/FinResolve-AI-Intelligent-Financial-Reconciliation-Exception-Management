"""
Orchestrator for the FinRCA-Bench pipeline.

Pipeline: Matcher (deterministic) -> Explainer (LLM, only on exceptions,
grounded + verified) -> Auditor (logs everything) -> Evaluator (honest
comparison against real injected-failure ground truth).

Run: python finrca_data/main_finrca.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matcher import load_data, match_records
from explainer import explain_exception
from evaluator import load_ground_truth, evaluate
from agents.auditor import log_event, clear_log

MAX_TO_EXPLAIN = 15  # bounded — every exception here fits easily, but keep the cap as a habit


def run_finrca_pipeline():
    clear_log()

    print("Loading FinRCA-Bench payments and bank transactions...")
    payments, bank = load_data(
        payments_path=os.path.join(os.path.dirname(__file__), "payments.csv"),
        bank_path=os.path.join(os.path.dirname(__file__), "bank_transactions.csv"),
    )
    results = match_records(payments, bank)

    print(f"Matched: {len(results['matched'])}")
    print(f"Amount mismatch: {len(results['amount_mismatch'])}")
    print(f"Payment, no bank record: {len(results['payment_no_bank'])}")
    print(f"Bank, no payment record: {len(results['bank_no_payment'])}")

    for m in results["matched"]:
        log_event("matcher", m["payment_id"], "clean_match", m)

    # Explainer runs on the two exception buckets that map onto real
    # payment records
    exceptions = results["amount_mismatch"] + results["payment_no_bank"]
    exceptions = exceptions[:MAX_TO_EXPLAIN]

    explanations = []
    print(f"\nExplaining {len(exceptions)} exceptions...")
    for record in exceptions:
        explanation = explain_exception(record)
        log_event(
            "explainer",
            explanation["record_id"],
            "explained" if explanation["verified"] else "escalated_unverified",
            explanation,
        )
        explanations.append(explanation)

    for record in results["bank_no_payment"]:
        log_event("matcher", record["bank_transaction_id"], "bank_side_orphan", record)

    verified_count = sum(1 for e in explanations if e["verified"])
    print(f"{verified_count}/{len(explanations)} explanations passed verification")

    # Ground-truth evaluation — the real credibility check
    print("\nEvaluating against FinRCA-Bench's real injected-failure ground truth...")
    ground_truth_path = os.path.join(os.path.dirname(__file__), "rca_ground_truth.jsonl")
    ground_truth_cases = load_ground_truth(ground_truth_path)
    gt_eval = evaluate(results, ground_truth_cases)

    log_event("evaluator", "batch", "ground_truth_evaluated", gt_eval)

    print("\n" + "=" * 60)
    print("GROUND-TRUTH EVALUATION (real injected failures, F12-F15)")
    print("=" * 60)
    print(f"Real bank-matching failures in dataset: {gt_eval['true_failures_total']}")
    print(f"Caught by our Matcher:                  {gt_eval['true_failures_caught']}")
    print(f"Missed:                                  {gt_eval['true_failures_missed']}")
    print(f"Recall on real failures:                 {gt_eval['recall_on_real_failures']}")
    print()
    print(f"Hard-negative legitimate cases in dataset: {gt_eval['hard_negatives_total']}")
    print(f"Correctly left alone:                      {gt_eval['hard_negatives_correctly_left_alone']}")
    print(f"Wrongly flagged (false positives):         {gt_eval['hard_negatives_wrongly_flagged']}")
    print(f"False positive rate on hard negatives:     {gt_eval['false_positive_rate_on_hard_negatives']}")
    print("=" * 60)

    report = {
        "matcher_summary": {
            "matched": len(results["matched"]),
            "amount_mismatch": len(results["amount_mismatch"]),
            "payment_no_bank": len(results["payment_no_bank"]),
            "bank_no_payment": len(results["bank_no_payment"]),
        },
        "explainer_summary": {
            "total_explained": len(explanations),
            "verified": verified_count,
            "escalated_unverified": len(explanations) - verified_count,
        },
        "ground_truth_evaluation": gt_eval,
        "explanations": explanations,
    }

    out_path = os.path.join(os.path.dirname(__file__), "finrca_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report saved to {out_path}")
    print("Audit trail saved to data/audit_log.jsonl")

    return report


if __name__ == "__main__":
    run_finrca_pipeline()
