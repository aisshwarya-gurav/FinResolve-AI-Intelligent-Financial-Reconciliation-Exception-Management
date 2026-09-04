"""
Explainer Agent

The only component that calls an LLM.

Safety principles:
1. LLM receives only the already-computed exception record.
2. LLM output must be valid JSON.
3. Output schema is validated in Python.
4. Numeric grounding is verified against the source record.
5. Irreversible actions are blocked.
6. Any validation failure falls back to human review.
"""

import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_client import call_llm


SYSTEM_PROMPT = """
You are a payment-reconciliation exception analyst.

Analyze ONLY the payment record inside <record>.

Return EXACTLY ONE valid JSON object.

Required fields:
{
  "reason": "one short sentence explaining the discrepancy",
  "suggested_action": "one short advisory action for a human reviewer",
  "confidence": 0.0
}

Rules:

1. confidence MUST be a JSON number between 0.0 and 1.0.
2. Only reference numbers and dates that appear in <record>.
3. Never invent, estimate, calculate, or round a number.
4. Never recommend cancelling a payment.
5. Never recommend reversing a payment.
6. Never recommend resubmitting a payment.
7. Never recommend deleting a payment.
8. Never recommend voiding a payment.
9. The suggested action must be advisory and reversible.
10. Do not output markdown.
11. Do not output code fences.
12. Do not write "Here is the JSON".
13. Do not output any additional fields.
14. Output ONLY the JSON object.
"""


def explain_exception(record: dict) -> dict:
    """
    Generate and validate an explanation for one exception record.

    Returns verified=True only when all safety checks pass.
    """

    record_block = json.dumps(
        record,
        indent=2,
        default=str
    )

    try:
        raw_text = call_llm(
            SYSTEM_PROMPT,
            f"<record>\n{record_block}\n</record>",
            max_tokens=500
        ).strip()

    except Exception as exc:
        return _fallback(
            record,
            f"llm_call_failed: {exc}"
        )

    # --------------------------------------------------------
    # JSON validation
    # --------------------------------------------------------

    try:
        parsed = json.loads(raw_text)

    except json.JSONDecodeError:
        return _fallback(
            record,
            "llm_output_not_valid_json"
        )

    if not isinstance(parsed, dict):
        return _fallback(
            record,
            "llm_output_not_json_object"
        )

    # --------------------------------------------------------
    # Required fields
    # --------------------------------------------------------

    required_keys = {
        "reason",
        "suggested_action",
        "confidence"
    }

    if not required_keys.issubset(parsed.keys()):
        return _fallback(
            record,
            "missing_required_fields"
        )

    # --------------------------------------------------------
    # Reject unexpected fields
    # --------------------------------------------------------

    allowed_keys = {
        "reason",
        "suggested_action",
        "confidence"
    }

    if not set(parsed.keys()).issubset(allowed_keys):
        return _fallback(
            record,
            "unexpected_fields"
        )

    # --------------------------------------------------------
    # Type validation
    # --------------------------------------------------------

    if not isinstance(parsed["reason"], str):
        return _fallback(
            record,
            "invalid_reason_type"
        )

    if not isinstance(parsed["suggested_action"], str):
        return _fallback(
            record,
            "invalid_action_type"
        )

    if (
        not isinstance(parsed["confidence"], (int, float))
        or isinstance(parsed["confidence"], bool)
    ):
        return _fallback(
            record,
            "invalid_confidence_type"
        )

    # --------------------------------------------------------
    # Confidence range
    # --------------------------------------------------------

    confidence = float(parsed["confidence"])

    if not 0.0 <= confidence <= 1.0:
        return _fallback(
            record,
            "confidence_out_of_range"
        )

    # --------------------------------------------------------
    # Grounding verification
    # --------------------------------------------------------

    if not verify_grounding(
        parsed["reason"],
        record
    ):
        return _fallback(
            record,
            "explanation_references_unverifiable_figures"
        )

    # --------------------------------------------------------
    # Irreversible-action protection
    # --------------------------------------------------------

    if _mentions_irreversible_action(
        parsed["suggested_action"]
    ):
        return _fallback(
            record,
            "suggested_irreversible_action_blocked"
        )

    # --------------------------------------------------------
    # Success
    # --------------------------------------------------------

    parsed["record_id"] = (
        record.get("payment_id")
        or record.get("bank_transaction_id")
        or record.get("reference_id")
    )

    parsed["confidence"] = confidence
    parsed["verified"] = True

    return parsed


def verify_grounding(
    explanation_text: str,
    record: dict
) -> bool:
    """
    Verify that every number appearing in the explanation
    exists somewhere in the original record.
    """

    numbers_in_explanation = set(
        re.findall(
            r"\d+(?:\.\d+)?",
            explanation_text
        )
    )

    numbers_in_record = set()

    for value in record.values():
        numbers_in_record.update(
            re.findall(
                r"\d+(?:\.\d+)?",
                str(value)
            )
        )

    return numbers_in_explanation.issubset(
        numbers_in_record
    )


def _mentions_irreversible_action(
    action_text: str
) -> bool:
    """
    Block actions that could directly modify financial state.
    """

    blocked_terms = [
        "cancel",
        "reverse",
        "resubmit",
        "delete",
        "void"
    ]

    text = action_text.lower()

    return any(
        term in text
        for term in blocked_terms
    )


def _fallback(
    record: dict,
    reason_code: str
) -> dict:
    """
    Safe fallback whenever validation fails.
    """

    return {
        "record_id": (
            record.get("payment_id")
            or record.get("bank_transaction_id")
            or record.get("reference_id")
        ),
        "reason": (
            "Could not generate a verified explanation "
            f"({reason_code})."
        ),
        "suggested_action": (
            "escalate for manual review"
        ),
        "confidence": 0.0,
        "verified": False
    }