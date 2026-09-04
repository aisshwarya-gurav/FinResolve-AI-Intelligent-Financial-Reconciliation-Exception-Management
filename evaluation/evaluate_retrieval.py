"""
Retrieval Evaluation — measures Recall@1 and Recall@3 for the historical
case retriever, using REAL exception records produced by
finrca_data/three_way_matcher.py (the same records already validated
against ground truth earlier in this project), not fabricated queries.

For each real F05/F06/F07 exception record, we check: does the top-1
(top-3) retrieved result belong to the SAME failure_type as the query's
real, known category? This is a fair test — we know the true category
because these exact records were already matched to ground truth
case_ids in finrca_data/main_three_way.py's evaluation.

Run: python evaluation/evaluate_retrieval.py
Output: evaluation/retrieval_eval_results.json
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "finrca_data"))

from rag.retriever import HistoricalCaseRetriever
from three_way_matcher import load_data, match_three_way

FAILURE_TYPE_BY_BUCKET = {
    "payment_without_invoice": "F05_PAYMENT_WITHOUT_VALID_INVOICE",
    "overpaid_duplicate": "F06_INVOICE_PAID_TWICE",
    "partial_payment": "F07_PARTIAL_PAYMENT_RESIDUAL_BALANCE",
}


def get_real_query_records():
    """Every exception record in these three buckets IS a real F05/F06/F07
    case — confirmed by the exact ID-level ground-truth match already
    proven in finrca_data/main_three_way.py (100% recall, exact ID match,
    zero false positives on these three categories)."""
    base = os.path.join(os.path.dirname(__file__), "..", "finrca_data")
    invoices, allocations, payments, bank = load_data(
        os.path.join(base, "invoices.csv"), os.path.join(base, "payment_allocations.csv"),
        os.path.join(base, "payments.csv"), os.path.join(base, "bank_transactions.csv"),
    )
    results = match_three_way(invoices, allocations, payments, bank)

    queries = []
    for bucket, true_failure_type in FAILURE_TYPE_BY_BUCKET.items():
        for record in results[bucket]:
            queries.append({"record": record, "true_failure_type": true_failure_type, "bucket": bucket})
    return queries


def evaluate():
    retriever = HistoricalCaseRetriever()
    queries = get_real_query_records()

    per_category = {ft: {"recall_at_1_hits": 0, "recall_at_3_hits": 0, "total": 0}
                     for ft in FAILURE_TYPE_BY_BUCKET.values()}

    per_query_detail = []

    for q in queries:
        true_type = q["true_failure_type"]
        results_top3 = retriever.retrieve_similar(q["record"], k=3)
        results_top1 = results_top3[:1]

        hit_at_1 = any(r["failure_type"] == true_type for r in results_top1)
        hit_at_3 = any(r["failure_type"] == true_type for r in results_top3)

        per_category[true_type]["total"] += 1
        if hit_at_1:
            per_category[true_type]["recall_at_1_hits"] += 1
        if hit_at_3:
            per_category[true_type]["recall_at_3_hits"] += 1

        per_query_detail.append({
            "query_record_id": q["record"].get("payment_id") or q["record"].get("invoice_id"),
            "true_failure_type": true_type,
            "top_3_retrieved": [{"case_id": r["case_id"], "failure_type": r["failure_type"],
                                  "score": r["similarity_score"]} for r in results_top3],
            "hit_at_1": hit_at_1,
            "hit_at_3": hit_at_3,
        })

    summary = {}
    for ft, stats in per_category.items():
        total = stats["total"]
        summary[ft] = {
            "total_queries": total,
            "recall_at_1": round(stats["recall_at_1_hits"] / total, 3) if total else None,
            "recall_at_3": round(stats["recall_at_3_hits"] / total, 3) if total else None,
        }

    report = {
        "historical_cases_loaded": len(retriever.cases),
        "summary": summary,
        "per_query_detail": per_query_detail,
    }
    return report


if __name__ == "__main__":
    report = evaluate()

    print(f"Historical cases loaded: {report['historical_cases_loaded']}")
    print()
    for ft, stats in report["summary"].items():
        short_name = ft.split("_", 1)[0]
        print(f"{short_name} ({ft})")
        print(f"  Queries evaluated: {stats['total_queries']}")
        print(f"  Recall@1: {stats['recall_at_1']}")
        print(f"  Recall@3: {stats['recall_at_3']}")
        print()

    out_path = os.path.join(os.path.dirname(__file__), "retrieval_eval_results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Full results saved to {out_path}")
