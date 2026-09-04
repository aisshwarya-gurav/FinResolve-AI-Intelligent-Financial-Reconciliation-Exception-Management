"""
Historical Case Library — loads and normalizes ground-truth cases from
finrca_data/rca_ground_truth.jsonl into the retrieval corpus.

Scope decision (explicit, not silent): kept to the 7 failure categories
already used elsewhere in this project — F05, F06, F07 (invoice/payment
stage, used by finrca_data/three_way_matcher.py) and F12-F15 (bank
matching stage, used by finrca_data/matcher.py). The other categories in
the ground truth (F01-F04, F08-F11 — PO/GL/approval-workflow failures)
are out of scope: no matcher in this project detects them, so retrieving
them would offer precedent for exceptions we don't produce. NO_FAILURE
("hard negative") cases are also excluded here — they're valuable
(we've used them for false-positive testing elsewhere) but are a
different kind of thing than a failure precedent, and weren't in the
requested scope for this retriever. Easy to add later if wanted.
"""

import json
import os

RECONCILIATION_RELEVANT_FAILURE_TYPES = {
    "F05_PAYMENT_WITHOUT_VALID_INVOICE",
    "F06_INVOICE_PAID_TWICE",
    "F07_PARTIAL_PAYMENT_RESIDUAL_BALANCE",
    "F12_ERP_PAYMENT_MISSING_FROM_BANK",
    "F13_BANK_TRANSACTION_MISSING_FROM_ERP",
    "F14_BANK_ERP_AMOUNT_MISMATCH",
    "F15_INCORRECT_PAYMENT_BANK_MATCH",
}

DEFAULT_GROUND_TRUTH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "finrca_data", "rca_ground_truth.jsonl"
)


def _build_searchable_text(observed_symptom: str, root_cause: str, root_cause_category: str) -> str:
    """Per spec: searchable text is observed_symptom + root_cause +
    root_cause_category. expected_resolution is deliberately excluded —
    it's the answer, not the search key, and including it would let a
    query accidentally retrieve on resolution-wording overlap rather
    than symptom/cause similarity."""
    category_readable = root_cause_category.replace("_", " ")
    return f"{observed_symptom} {root_cause} {category_readable}"


def load_historical_cases(ground_truth_path: str = DEFAULT_GROUND_TRUTH_PATH) -> list:
    """Loads the ground-truth file, filters to reconciliation-relevant
    failure types, and normalizes each case to the fields this project's
    retriever and explainer need."""

    if not os.path.exists(ground_truth_path):
        raise FileNotFoundError(f"Ground truth file not found: {ground_truth_path}")

    cases = []
    with open(ground_truth_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)

            if raw.get("failure_type") not in RECONCILIATION_RELEVANT_FAILURE_TYPES:
                continue

            cases.append({
                "case_id": raw["case_id"],
                "failure_type": raw["failure_type"],
                "observed_symptom": raw["observed_symptom"],
                "root_cause": raw["root_cause"],
                "root_cause_category": raw["root_cause_category"],
                "expected_resolution": raw["expected_resolution"],
                "severity": raw.get("severity"),
                "difficulty": raw.get("difficulty"),
                "searchable_text": _build_searchable_text(
                    raw["observed_symptom"], raw["root_cause"], raw["root_cause_category"]
                ),
            })

    return cases


if __name__ == "__main__":
    cases = load_historical_cases()
    print(f"Loaded {len(cases)} reconciliation-relevant historical cases")
    from collections import Counter
    print(Counter(c["failure_type"] for c in cases))
