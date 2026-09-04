"""
Q&A Agent — generalizes the Explainer into free-form querying.

Same discipline as every Explainer in this project: grounded only in the
record it's given, schema-validated, grounding-verified, blocked from
suggesting irreversible actions. The difference is the question is
open-ended (whatever the user types) instead of a fixed "explain this
exception" prompt — this is the "Settlement Q&A agent" direction from
the track, built on top of the same verified infrastructure.
"""

import json
import re
from llm_client import call_llm

SYSTEM_PROMPT = """You are a reconciliation case assistant. You will be
given one record (a payment, invoice, or transaction) and a question
about it from a finance team member. Answer only using information in
the <record> block — never invent, estimate, or round figures that
aren't given to you. If the question can't be answered from the record
alone, say so plainly rather than guessing.

Never suggest cancelling, reversing, resubmitting, or voiding anything —
you are advisory only; a human makes any final call.

Respond with ONLY a JSON object, no other text:
{"answer": "<direct answer, plain English, 1-3 sentences>",
 "confidence": <float 0.0-1.0>}
"""

BLOCKED_ACTION_TERMS = ["cancel", "reverse", "resubmit", "delete", "void"]


def ask_about_case(record: dict, question: str) -> dict:
    record_block = json.dumps(record, indent=2, default=str)
    user_message = f"<record>\n{record_block}\n</record>\n\n<question>\n{question}\n</question>"

    try:
        raw_text = call_llm(SYSTEM_PROMPT, user_message, max_tokens=250).strip()
    except Exception as e:
        return _fallback(f"LLM call failed: {e}")

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return _fallback("Model did not return valid JSON.")

    if "answer" not in parsed:
        return _fallback("Model response missing required field.")

    if not _verify_grounding(parsed["answer"], record):
        return _fallback("Answer referenced figures not present in the record — rejected.")

    if any(term in parsed["answer"].lower() for term in BLOCKED_ACTION_TERMS):
        return _fallback("Answer suggested a blocked irreversible action — rejected.")

    parsed["verified"] = True
    return parsed


def _verify_grounding(answer_text: str, record: dict) -> bool:
    numbers_in_answer = set(re.findall(r"\d+\.?\d*", answer_text))
    numbers_in_record = set()
    for v in record.values():
        numbers_in_record.update(re.findall(r"\d+\.?\d*", str(v)))
    return numbers_in_answer.issubset(numbers_in_record)


def _fallback(reason: str) -> dict:
    return {
        "answer": f"Couldn't produce a verified answer ({reason}). Please review this case manually.",
        "confidence": 0.0,
        "verified": False,
    }
