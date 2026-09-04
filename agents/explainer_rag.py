"""
RAG Explainer — Exception -> deterministic facts -> Retriever -> top
historical cases -> LLM -> grounded + verified explanation.

The agent:
1. Retrieves similar historical RCA cases.
2. Separates CURRENT CASE from HISTORICAL CASES.
3. Forces structured JSON output.
4. Verifies current-case numeric grounding.
5. Verifies historical case citations.
6. Blocks irreversible actions.
7. Returns verified=True only after every validation passes.
"""

import json
import re
import sys
import os
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_client import call_llm
from rag.retriever import retrieve_similar


CASE_ID_PATTERN = re.compile(r"\bRCA_\d{6}\b")

BLOCKED_ACTION_TERMS = [
    "cancel",
    "reverse",
    "resubmit",
    "delete",
    "void",
]


SYSTEM_PROMPT = """You are a reconciliation exception analyst.

You will receive:
1. CURRENT CASE — the actual exception being investigated.
2. HISTORICAL CASES — similar resolved cases retrieved from the RCA database.

IMPORTANT:
The CURRENT CASE is the ONLY source of truth about what happened in the
current transaction.

Historical cases are ONLY precedent and must NEVER be presented as facts
about the current transaction.

RULES:

1. confirmed_facts
- ONLY use facts from CURRENT CASE.
- Do not use numbers from historical cases.
- Every number mentioned must exist in CURRENT CASE.
- Keep this to one concise sentence.

2. historical_precedent
- You may refer to historical cases.
- If you refer to a historical case, cite its exact case_id.
- Only cite case_ids that were actually provided.
- Do not invent case IDs.
- If none are relevant, say exactly:
  "No closely matching precedent."

3. likely_hypothesis
- This is a hypothesis, NOT a confirmed fact.
- Base it only on the CURRENT CASE and optionally the pattern seen in
  historical cases.
- Any numbers mentioned must come from CURRENT CASE.
- Clearly use cautious language such as "may", "could", or "likely".

4. suggested_action
- Give ONE advisory action for a human reviewer.
- The action must be reversible.
- NEVER recommend:
  cancel
  reverse
  resubmit
  delete
  void

5. confidence
- Must be a JSON number between 0.0 and 1.0.

OUTPUT REQUIREMENTS:

Return EXACTLY ONE valid JSON object.

Do NOT return:
- markdown
- code fences
- explanations outside JSON
- "Here is the JSON"
- additional fields

The JSON MUST have exactly these fields:

{
  "confirmed_facts": "...",
  "historical_precedent": "...",
  "likely_hypothesis": "...",
  "suggested_action": "...",
  "confidence": 0.0
}

IMPORTANT:
- confirmed_facts MUST be a single string, NOT an array.
- historical_precedent MUST be a single string, NOT an array.
- likely_hypothesis MUST be a single string, NOT an array.
- suggested_action MUST be a single string, NOT an array.
- confidence MUST be a JSON number between 0.0 and 1.0.
- Keep every field concise.
- Do not repeat the case data.
- Output ONLY the JSON object.
Output ONLY the JSON object.
"""


def explain_with_rag(record: dict, k: int = 3) -> dict:
    """
    Retrieve similar historical cases and generate a grounded explanation.

    Returns verified=True only when:
    - valid JSON was produced
    - all required fields exist
    - current-case numbers are grounded
    - historical case IDs are valid
    - suggested action is reversible
    """

    # ---------------------------------------------------------
    # 1. Retrieve historical precedent
    # ---------------------------------------------------------

    try:
        retrieved_cases = retrieve_similar(record, k=k)
    except Exception as e:
        return _fallback(
            record,
            [],
            f"retrieval_failed: {e}"
        )

    retrieved_cases = retrieved_cases or []

    retrieved_case_ids = {
        c["case_id"]
        for c in retrieved_cases
        if c.get("case_id")
    }

    # ---------------------------------------------------------
    # 2. Build CURRENT CASE section
    # ---------------------------------------------------------

    current_case_block = json.dumps(
        record,
        indent=2,
        default=str
    )

    # ---------------------------------------------------------
    # 3. Build HISTORICAL CASES section
    # ---------------------------------------------------------

    if retrieved_cases:

        historical_records = []

        for c in retrieved_cases:
            historical_records.append(
                {
                    "case_id": c.get("case_id"),
                    "failure_type": c.get("failure_type"),
                    "observed_symptom": c.get("observed_symptom"),
                    "root_cause": c.get("root_cause"),
                    "expected_resolution": c.get("expected_resolution"),
                    "similarity_score": c.get("similarity_score"),
                }
            )

        historical_block = json.dumps(
            historical_records,
            indent=2,
            default=str
        )

    else:
        historical_block = "No historical cases retrieved."

    # ---------------------------------------------------------
    # 4. Build LLM input
    # ---------------------------------------------------------

    user_message = (
        "<CURRENT_CASE>\n"
        f"{current_case_block}\n"
        "</CURRENT_CASE>\n\n"
        "<HISTORICAL_CASES>\n"
        f"{historical_block}\n"
        "</HISTORICAL_CASES>\n\n"
        "Remember: CURRENT_CASE is the only source of truth for the "
        "current transaction."
    )

    # ---------------------------------------------------------
    # 5. Call LLM
    #
    # IMPORTANT:
    # 800 tokens prevents the JSON from being truncated.
    # ---------------------------------------------------------

    try:

        raw_text = call_llm(
            SYSTEM_PROMPT,
            user_message,
            max_tokens=1200
        )

        raw_text = raw_text.strip()

    except Exception as e:

        return _fallback(
            record,
            retrieved_cases,
            f"llm_call_failed: {e}"
        )

    # ---------------------------------------------------------
    # 6. Parse JSON
    # ---------------------------------------------------------

    try:

        parsed = json.loads(raw_text)

    except json.JSONDecodeError:

        return _fallback(
            record,
            retrieved_cases,
            "llm_output_not_valid_json"
        )

    # ---------------------------------------------------------
    # 7. Make sure output is an object
    # ---------------------------------------------------------

    if not isinstance(parsed, dict):

        return _fallback(
            record,
            retrieved_cases,
            "llm_output_not_json_object"
        )

    # ---------------------------------------------------------
    # 8. Validate required fields
    # ---------------------------------------------------------

    required_keys = {
        "confirmed_facts",
        "historical_precedent",
        "likely_hypothesis",
        "suggested_action",
        "confidence",
    }

    if not required_keys.issubset(parsed.keys()):

        return _fallback(
            record,
            retrieved_cases,
            "missing_required_fields"
        )

    # ---------------------------------------------------------
    # 9. Validate field types
    # ---------------------------------------------------------

    string_fields = [
        "confirmed_facts",
        "historical_precedent",
        "likely_hypothesis",
        "suggested_action",
    ]

    for field in string_fields:

        if not isinstance(parsed[field], str):

            return _fallback(
                record,
                retrieved_cases,
                f"invalid_{field}_type"
            )

    # ---------------------------------------------------------
    # 10. Validate confidence
    # ---------------------------------------------------------

    confidence = parsed["confidence"]

    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
    ):

        return _fallback(
            record,
            retrieved_cases,
            "invalid_confidence_type"
        )

    confidence = float(confidence)

    if not 0.0 <= confidence <= 1.0:

        return _fallback(
            record,
            retrieved_cases,
            "confidence_out_of_range"
        )

    # ---------------------------------------------------------
    # 11. Verify grounding
    #
    # confirmed_facts + likely_hypothesis can ONLY contain
    # numbers that exist in CURRENT CASE.
    # ---------------------------------------------------------

    combined_current_text = (
        parsed["confirmed_facts"]
        + " "
        + parsed["likely_hypothesis"]
    )

    if not _verify_grounding(
        combined_current_text,
        record
    ):

        return _fallback(
            record,
            retrieved_cases,
            "confirmed_facts_or_hypothesis_references_unverifiable_figures"
        )

    # ---------------------------------------------------------
    # 12. Verify historical case citations
    # ---------------------------------------------------------

    cited_ids = set(
        CASE_ID_PATTERN.findall(
            parsed["historical_precedent"]
        )
    )

    if not cited_ids.issubset(
        retrieved_case_ids
    ):

        return _fallback(
            record,
            retrieved_cases,
            "cited_case_id_not_in_retrieved_set"
        )

    # ---------------------------------------------------------
    # 13. Block irreversible actions
    # ---------------------------------------------------------

    if _mentions_irreversible_action(
        parsed["suggested_action"]
    ):

        return _fallback(
            record,
            retrieved_cases,
            "suggested_irreversible_action_blocked"
        )

    # ---------------------------------------------------------
    # 14. Additional confidence normalization
    # ---------------------------------------------------------

    parsed["confidence"] = confidence

    # ---------------------------------------------------------
    # 15. Add system-generated metadata
    # ---------------------------------------------------------

    parsed["record_id"] = (
        record.get("payment_id")
        or record.get("invoice_id")
        or record.get("bank_transaction_id")
    )

    parsed["retrieved_case_ids"] = sorted(
        retrieved_case_ids
    )

    parsed["verified"] = True

    # ---------------------------------------------------------
    # 16. Return verified result
    # ---------------------------------------------------------

    return parsed


def _verify_grounding(
    text: str,
    record: dict
) -> bool:
    """
    Accepts:
      - direct source facts already present in the current case
      - deterministically derived financial facts (e.g. difference/residual)

    Rejects:
      - unsupported numeric claims that are not in the case evidence and
        cannot be derived from trusted values in the current record.
    """

    numbers_in_text = _extract_numeric_literals(text)
    record_values = _record_numeric_values(record)

    for value in numbers_in_text:
        if _is_supported_numeric_value(value, text, record_values):
            continue
        return False

    return True


def _extract_numeric_literals(text: str) -> list[Decimal]:
    """Extract numeric literals while handling commas and currency symbols."""
    matches = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    values = []
    for match in matches:
        cleaned = match.replace(",", "")
        try:
            values.append(Decimal(cleaned))
        except InvalidOperation:
            continue
    return values


def _record_numeric_values(record: dict) -> list[Decimal]:
    """Flatten trusted record values into numeric literals for grounding checks."""
    values = []

    def walk(node):
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple, set)):
            for child in node:
                walk(child)
        else:
            text = str(node)
            for match in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
                cleaned = match.replace(",", "")
                try:
                    values.append(Decimal(cleaned))
                except InvalidOperation:
                    continue

    walk(record)
    return values


def _is_supported_numeric_value(
    value: Decimal,
    text: str,
    record_values: list[Decimal],
) -> bool:
    """Allow direct record values and deterministically derived differences."""

    if any(_same_decimal(value, candidate) for candidate in record_values):
        return True

    if not _has_derivation_cue(text):
        return False

    return any(
        _same_decimal(value, candidate)
        for candidate in _derived_numeric_candidates(record_values)
    )


def _has_derivation_cue(text: str) -> bool:
    cues = [
        "difference",
        "diff",
        "lower",
        "higher",
        "less",
        "more",
        "shortfall",
        "residual",
        "remaining",
        "balance",
        "delta",
        "gap",
        "excess",
        "variance",
        "over",
        "under",
    ]
    lower = text.lower()
    return any(cue in lower for cue in cues)


def _derived_numeric_candidates(record_values: list[Decimal]) -> list[Decimal]:
    candidates = []
    seen = set()

    for i, left in enumerate(record_values):
        for right in record_values[i + 1:]:
            for fn in (
                lambda a, b: abs(a - b),
                lambda a, b: a - b,
                lambda a, b: b - a,
                lambda a, b: a + b,
            ):
                try:
                    candidate = fn(left, right)
                except Exception:
                    continue
                normalized = _normalize_decimal(candidate)
                if normalized not in seen:
                    candidates.append(normalized)
                    seen.add(normalized)

    return candidates


def _same_decimal(left: Decimal, right: Decimal) -> bool:
    return _normalize_decimal(left) == _normalize_decimal(right)


def _normalize_decimal(value: Decimal) -> Decimal:
    return value.normalize()


def _mentions_irreversible_action(
    action_text: str
) -> bool:
    """
    Prevent the LLM from recommending irreversible financial actions.
    """

    action_lower = action_text.lower()

    return any(
        term in action_lower
        for term in BLOCKED_ACTION_TERMS
    )


def _fallback(
    record: dict,
    retrieved_cases: list,
    reason_code: str
) -> dict:
    """
    Safe fallback.

    Any failed validation results in verified=False.
    """

    retrieved_case_ids = sorted(
        {
            c["case_id"]
            for c in retrieved_cases
            if c.get("case_id")
        }
    )

    return {
        "record_id": (
            record.get("payment_id")
            or record.get("invoice_id")
            or record.get("bank_transaction_id")
        ),
        "confirmed_facts": (
            f"Could not generate a verified explanation "
            f"({reason_code})."
        ),
        "historical_precedent": (
            "N/A — explanation rejected before this could be assessed."
        ),
        "likely_hypothesis": "N/A",
        "suggested_action": "escalate for manual review",
        "confidence": 0.0,
        "retrieved_case_ids": retrieved_case_ids,
        "verified": False,
    }