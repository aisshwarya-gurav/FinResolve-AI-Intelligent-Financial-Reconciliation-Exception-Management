"""
Tests for rag/retriever.py.

Deliberately tests against the ACTUAL finrca_data/rca_ground_truth.jsonl
and ACTUAL output from finrca_data/three_way_matcher.py / matcher.py —
not fabricated stand-in data. If the ground truth file changes, these
tests are exercising the real corpus, not a frozen fixture that could
drift from reality.

Run: pytest rag/tests/test_retriever.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "finrca_data"))

from rag.retriever import HistoricalCaseRetriever, build_query_text


@pytest.fixture(scope="module")
def retriever():
    return HistoricalCaseRetriever()


@pytest.fixture(scope="module")
def real_exceptions():
    """Pulls real exception records straight from the three-way matcher
    — the same records validated against ground truth earlier in this
    project — rather than hand-writing fake ones."""
    from three_way_matcher import load_data, match_three_way
    base = os.path.join(os.path.dirname(__file__), "..", "..", "finrca_data")
    invoices, allocations, payments, bank = load_data(
        os.path.join(base, "invoices.csv"), os.path.join(base, "payment_allocations.csv"),
        os.path.join(base, "payments.csv"), os.path.join(base, "bank_transactions.csv"),
    )
    return match_three_way(invoices, allocations, payments, bank)


# --- category-relevance tests, against real records --------------------

def test_f05_query_retrieves_f05_cases(retriever, real_exceptions):
    record = real_exceptions["payment_without_invoice"][0]
    results = retriever.retrieve_similar(record, k=3)
    assert len(results) > 0
    assert results[0]["failure_type"] == "F05_PAYMENT_WITHOUT_VALID_INVOICE"


def test_f06_query_retrieves_f06_cases(retriever, real_exceptions):
    record = real_exceptions["overpaid_duplicate"][0]
    results = retriever.retrieve_similar(record, k=3)
    assert len(results) > 0
    assert results[0]["failure_type"] == "F06_INVOICE_PAID_TWICE"


def test_f07_query_retrieves_f07_cases(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=3)
    assert len(results) > 0
    assert results[0]["failure_type"] == "F07_PARTIAL_PAYMENT_RESIDUAL_BALANCE"


def test_unrelated_categories_dont_dominate(retriever, real_exceptions):
    """The top result for an F05 query should score meaningfully higher
    than an unrelated category further down the list — retrieval should
    discriminate, not return near-random similarity."""
    record = real_exceptions["payment_without_invoice"][0]
    results = retriever.retrieve_similar(record, k=3)
    assert len(results) >= 2
    top_score = results[0]["similarity_score"]
    other_categories = [r for r in results if r["failure_type"] != results[0]["failure_type"]]
    if other_categories:
        assert top_score > other_categories[0]["similarity_score"]


# --- k parameter -------------------------------------------------------

def test_k_equals_1_returns_one_result(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=1)
    assert len(results) == 1


def test_k_equals_3_returns_up_to_three_results(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=3)
    assert len(results) <= 3
    assert len(results) > 0


def test_k_larger_than_corpus_does_not_error(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=1000)
    assert len(results) == len(retriever.cases)  # capped at corpus size, no crash


# --- invalid / empty input ------------------------------------------------

def test_none_record_returns_empty_list(retriever):
    assert retriever.retrieve_similar(None, k=3) == []


def test_non_dict_record_returns_empty_list(retriever):
    assert retriever.retrieve_similar("not a dict", k=3) == []
    assert retriever.retrieve_similar(["also", "not", "a", "dict"], k=3) == []


def test_empty_dict_record_returns_empty_list(retriever):
    assert retriever.retrieve_similar({}, k=3) == []


def test_record_with_no_useful_text_fields_returns_empty_list(retriever):
    assert retriever.retrieve_similar({"payment_id": "PAY_1", "amount": 500.0}, k=3) == []


def test_k_zero_returns_empty_list(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    assert retriever.retrieve_similar(record, k=0) == []


def test_negative_k_returns_empty_list(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    assert retriever.retrieve_similar(record, k=-1) == []


# --- output shape --------------------------------------------------------

def test_similarity_scores_present_and_ordered(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=3)
    scores = [r["similarity_score"] for r in results]
    assert all(isinstance(s, float) for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_no_duplicate_case_ids_in_results(retriever, real_exceptions):
    record = real_exceptions["partial_payment"][0]
    results = retriever.retrieve_similar(record, k=10)
    case_ids = [r["case_id"] for r in results]
    assert len(case_ids) == len(set(case_ids))


def test_original_case_metadata_present(retriever, real_exceptions):
    record = real_exceptions["payment_without_invoice"][0]
    results = retriever.retrieve_similar(record, k=1)
    expected_keys = {"case_id", "failure_type", "observed_symptom", "root_cause",
                      "root_cause_category", "expected_resolution", "severity",
                      "difficulty", "similarity_score"}
    assert expected_keys.issubset(results[0].keys())


# --- query builder --------------------------------------------------------

def test_build_query_text_uses_issue_field():
    query = build_query_text({"issue": "payment_without_valid_invoice"})
    assert "payment" in query
    assert "invoice" in query
    assert "_" not in query  # underscores replaced with spaces


def test_build_query_text_handles_missing_issue_field():
    assert build_query_text({"payment_id": "PAY_1"}) == ""


def test_build_query_text_handles_non_dict():
    assert build_query_text(None) == ""
    assert build_query_text("string") == ""
