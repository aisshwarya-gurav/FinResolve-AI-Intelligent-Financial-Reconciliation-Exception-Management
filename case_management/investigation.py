"""
Wires the RAG Explainer into the Case Management investigation flow.

    Case (from the store) -> record -> agents/explainer_rag.explain_with_rag
    -> explanation, logged as an audit event on the case.

This file is the ONLY new integration surface for this phase.
Untouched: the matcher, rag/retriever.py, agents/explainer.py, and
case_management/store.py's schema/public behavior. This module reuses
the store's existing event-logging so an investigation shows up in the
same audit trail as status changes, assignments, and notes — no schema
change needed.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.explainer_rag import explain_with_rag
from .store import CaseStore


def investigate_case(store: CaseStore, case_id: str, k: int = 3) -> dict:
    """Fetches the case's original exception record, runs it through the
    RAG explainer (retrieval + grounded/verified LLM explanation), logs
    the outcome to the case's audit trail, and returns the explanation.
    Raises CaseNotFoundError (from store.get_case) if the case doesn't exist —
    same error contract the rest of case_management already uses."""

    case = store.get_case(case_id)
    record = json.loads(case.record_json)

    explanation = explain_with_rag(record, k=k)

    summary = (
        f"RAG investigation: verified={explanation['verified']}, "
        f"cited_cases={explanation.get('retrieved_case_ids', [])}"
    )
    store._log_event(case_id, "rag_investigated", summary, actor="rag_explainer")

    return explanation
