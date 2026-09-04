"""
Reporter Agent — computes and formats the final honest metrics.

Per the brief: "Throughput plus measured accuracy plus an honest exception
list. One cherry-picked match proves nothing." This agent's only job is
to make sure nothing gets hidden — every unresolved record is listed.
"""


def build_report(matched, partial, unmatched, explained):
    total = len(matched) + len(partial) + len(unmatched)

    verified_explanations = [e for e in explained if e.get("verified")]
    unverified_explanations = [e for e in explained if not e.get("verified")]

    auto_resolved = len(matched) + len(verified_explanations)
    match_rate = round(auto_resolved / total * 100, 1) if total else 0.0

    report = {
        "total_records": total,
        "clean_matches": len(matched),
        "auto_resolved_with_explanation": len(verified_explanations),
        "escalated_to_human_review": len(unverified_explanations) + len(unmatched),
        "match_rate_percent": match_rate,
        "exceptions": {
            "explained_and_verified": verified_explanations,
            "needs_human_review": unverified_explanations
                + [{"reference_id": u["reference_id"], "issue": u["issue"]} for u in unmatched],
        },
    }
    return report


def print_summary(report):
    print("=" * 50)
    print("RECONCILIATION REPORT")
    print("=" * 50)
    print(f"Total records:              {report['total_records']}")
    print(f"Clean matches:               {report['clean_matches']}")
    print(f"Auto-resolved w/ explanation:{report['auto_resolved_with_explanation']}")
    print(f"Escalated to human review:   {report['escalated_to_human_review']}")
    print(f"Match rate:                  {report['match_rate_percent']}%")
    print("=" * 50)
