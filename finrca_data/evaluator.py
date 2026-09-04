"""
Ground-Truth Evaluator — the part that makes this more than a demo.

Cross-references our Matcher's flagged exceptions against FinRCA-Bench's
actual injected failure labels (F12-F15: the bank-matching failure
categories) AND its "hard negative" legitimate cases (e.g. weekend ACH
clearing, pending settlement) that are deliberately designed to look like
failures but aren't.

This produces two honest numbers a panel can't dismiss as cherry-picked:
  - Did we catch the real injected failures? (recall on true positives)
  - Did we avoid flagging the legitimate lookalikes? (false positive rate
    on hard negatives)
"""

import json

RELEVANT_FAILURE_TYPES = {
    "F12_ERP_PAYMENT_MISSING_FROM_BANK",
    "F13_BANK_TRANSACTION_MISSING_FROM_ERP",
    "F14_BANK_ERP_AMOUNT_MISMATCH",
    "F15_INCORRECT_PAYMENT_BANK_MATCH",
}


def load_ground_truth(path="finrca_data/rca_ground_truth.jsonl"):
    cases = [json.loads(line) for line in open(path)]
    return cases


def get_relevant_cases(cases):
    """Splits ground truth into: real bank-matching failures (what we
    should catch) and no-failure cases involving a payment entity (what
    we should NOT flag — the hard negatives)."""

    true_failures = [c for c in cases if c["failure_type"] in RELEVANT_FAILURE_TYPES]

    hard_negatives = [
        c for c in cases
        if c["failure_type"] == "NO_FAILURE"
        and c["primary_entity"]["type"] == "payment"
    ]

    return true_failures, hard_negatives


def evaluate(matcher_results: dict, ground_truth_cases):
    """Compares flagged payment_ids AND bank_transaction_ids (since F13
    cases are keyed by bank_transaction, not payment) against ground
    truth labels."""

    true_failures, hard_negatives = get_relevant_cases(ground_truth_cases)

    flagged_payment_ids = set()
    for bucket in ("amount_mismatch", "payment_no_bank"):
        for record in matcher_results[bucket]:
            flagged_payment_ids.add(record["payment_id"])

    flagged_bank_transaction_ids = set()
    for record in matcher_results["amount_mismatch"]:
        flagged_bank_transaction_ids.add(record["bank_transaction_id"])
    for record in matcher_results["bank_no_payment"]:
        flagged_bank_transaction_ids.add(record["bank_transaction_id"])

    def is_flagged(case) -> bool:
        entity_type = case["primary_entity"]["type"]
        entity_id = case["primary_entity"]["id"]
        if entity_type == "payment":
            return entity_id in flagged_payment_ids
        elif entity_type == "bank_transaction":
            return entity_id in flagged_bank_transaction_ids
        return False  # entity type we don't track (e.g. audit_event) — can't evaluate

    # Recall: of the real injected bank-matching failures, how many did
    # our matcher flag as an exception?
    caught, missed = [], []
    for case in true_failures:
        if is_flagged(case):
            caught.append(case)
        else:
            missed.append(case)

    # False positive rate on hard negatives: of the legitimate lookalikes,
    # how many did our matcher WRONGLY flag as an exception?
    correctly_left_alone, wrongly_flagged = [], []
    for case in hard_negatives:
        if is_flagged(case):
            wrongly_flagged.append(case)
        else:
            correctly_left_alone.append(case)

    recall = len(caught) / len(true_failures) if true_failures else None
    false_positive_rate = len(wrongly_flagged) / len(hard_negatives) if hard_negatives else None

    return {
        "true_failures_total": len(true_failures),
        "true_failures_caught": len(caught),
        "true_failures_missed": len(missed),
        "recall_on_real_failures": round(recall, 3) if recall is not None else None,
        "hard_negatives_total": len(hard_negatives),
        "hard_negatives_correctly_left_alone": len(correctly_left_alone),
        "hard_negatives_wrongly_flagged": len(wrongly_flagged),
        "false_positive_rate_on_hard_negatives": round(false_positive_rate, 3) if false_positive_rate is not None else None,
        "missed_case_details": [
            {"case_id": c["case_id"], "failure_type": c["failure_type"],
             "observed_symptom": c["observed_symptom"]} for c in missed
        ],
        "wrongly_flagged_case_details": [
            {"case_id": c["case_id"], "root_cause_category": c["root_cause_category"],
             "observed_symptom": c["observed_symptom"]} for c in wrongly_flagged
        ],
    }
