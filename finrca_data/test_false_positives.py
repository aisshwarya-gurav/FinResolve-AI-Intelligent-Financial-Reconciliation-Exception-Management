"""
Tests the Explainer agent against the 3 known false-positive cases from
the Matcher's ground-truth evaluation — the ones where a pure amount/
reference join over-flagged a legitimate transaction as an exception:

  PAY_0420047 -> RCA_000039 (LEGITIMATE_PENDING_SETTLEMENT)
  PAY_0420147 -> RCA_000040 (LEGITIMATE_NET_FEE_ACCOUNTING)
  PAY_0420171 -> RCA_000044 (LEGITIMATE_FX_SETTLEMENT)

This is the before/after story for your pitch: the deterministic Matcher
flags all 3 as problems; the question is whether the LLM Explainer,
given the same record, correctly recognizes them as explainable rather
than escalating them.

Run: python finrca_data/test_false_positives.py
Requires: .env set up with a working LLM_PROVIDER (see .env.example)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matcher import load_data, match_records
from evaluator import load_ground_truth, get_relevant_cases
from explainer import explain_exception

TARGET_CASE_IDS = {"RCA_000039", "RCA_000040", "RCA_000044"}


def run_test():
    payments, bank = load_data(
        payments_path=os.path.join(os.path.dirname(__file__), "payments.csv"),
        bank_path=os.path.join(os.path.dirname(__file__), "bank_transactions.csv"),
    )
    results = match_records(payments, bank)

    ground_truth_path = os.path.join(os.path.dirname(__file__), "rca_ground_truth.jsonl")
    ground_truth = load_ground_truth(ground_truth_path)
    _, hard_negatives = get_relevant_cases(ground_truth)

    target_cases = {c["primary_entity"]["id"]: c for c in hard_negatives if c["case_id"] in TARGET_CASE_IDS}

    # Find the actual exception records the Matcher produced for these payments
    exception_records = []
    for bucket_name in ("amount_mismatch", "payment_no_bank"):
        for record in results[bucket_name]:
            if record["payment_id"] in target_cases:
                exception_records.append((record, target_cases[record["payment_id"]]))

    print(f"Testing Explainer against {len(exception_records)} known false-positive cases...\n")

    correctly_recognized = 0

    for record, case in exception_records:
        print("=" * 70)
        print(f"Case: {case['case_id']} ({case['root_cause_category']})")
        print(f"Real explanation (ground truth): {case['root_cause']}")
        print(f"Observed symptom: {case['observed_symptom']}")
        print()
        print("Matcher flagged this record as:", record.get("issue"))
        print()

        explanation = explain_exception(record)

        print("Explainer's output:")
        print(f"  Reason: {explanation['reason']}")
        print(f"  Suggested action: {explanation['suggested_action']}")
        print(f"  Confidence: {explanation['confidence']}")
        print(f"  Verified (passed grounding check): {explanation['verified']}")
        print()

        # Heuristic check: does the explanation's suggested_action hint at
        # "no action needed" / "likely legitimate" rather than escalation?
        benign_signals = ["no action", "likely legitimate", "expected", "normal",
                           "pending", "fee", "fx", "exchange rate", "timing"]
        reason_and_action = (explanation["reason"] + " " + explanation["suggested_action"]).lower()
        recognized = any(signal in reason_and_action for signal in benign_signals)

        if recognized:
            correctly_recognized += 1
            print("  --> Looks like the Explainer recognized this as likely legitimate.")
        else:
            print("  --> Explainer still treated this as a hard exception needing review.")
        print()

    print("=" * 70)
    print(f"SUMMARY: {correctly_recognized}/{len(exception_records)} false positives")
    print(f"were likely correctly reframed as legitimate by the Explainer.")
    print("=" * 70)
    print("\nNote: this is a heuristic keyword check on the LLM's own wording,")
    print("not a formal metric — read the actual explanations above and judge")
    print("for yourself whether the reasoning is sound before quoting a number.")


if __name__ == "__main__":
    run_test()
