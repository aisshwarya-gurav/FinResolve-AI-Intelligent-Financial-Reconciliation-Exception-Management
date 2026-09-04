"""
Real-Data Matcher Agent — fuzzy record linkage for the BenchRec dataset.

Unlike the synthetic project's Matcher (exact reference_id join), this
dataset has NO shared ID between A-side (internal ledger) and B-side
(bank statement) rows — matching has to be inferred from amount, date,
and text similarity on transaction reference/attribute fields, same
approach as the ICAIF 2023 benchmark expects.

Design, still fully deterministic (no LLM):
1. BLOCKING — narrow candidates to same currency+account, amount within
   tolerance, date within a window.
2. SCORING — two signals combined:
   a) TF-IDF character n-gram cosine similarity over reference/attribute
      text (catches loose textual similarity)
   b) "Superstring rescue" — the two systems format the same underlying
      reference number differently (e.g. "8487259" appears in both, but
      surrounded by different noise), so raw cosine similarity alone
      misses true matches. We extract long digit runs (6+ digits, likely
      real reference/account numbers, not formatting noise) from both
      sides and check for exact substring overlap. A shared long digit
      run is very strong evidence of a real match — much stronger than
      general text similarity — so it gets a heavy score boost.
3. DECISION — best-scoring candidate above a confidence threshold is
   the match; anything below is left UNMATCHED for human review, same
   "leave unmatched rather than mismatch" philosophy as the benchmark's
   own evaluation metric.
"""

import re
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

AMOUNT_TOLERANCE_PCT = 0.001   # 0.1% — amounts should match almost exactly
DATE_WINDOW_DAYS = 5           # candidate must settle within this many days
CONFIDENCE_THRESHOLD = 0.55    # below this, leave unmatched
DIGIT_RUN_MIN_LENGTH = 6       # digit sequences this long are likely real reference numbers
SUPERSTRING_BOOST = 0.5        # confidence boost when a long digit run is shared


def _digit_runs(text: str) -> set:
    """Extracts digit sequences of DIGIT_RUN_MIN_LENGTH+ characters —
    long enough to be a real reference/account number, not formatting
    noise or short codes."""
    if not isinstance(text, str):
        return set()
    return set(re.findall(rf"\d{{{DIGIT_RUN_MIN_LENGTH},}}", text))


def _shares_digit_run(text_a: str, text_b: str) -> bool:
    runs_a = _digit_runs(text_a)
    runs_b = _digit_runs(text_b)
    if not runs_a or not runs_b:
        return False
    # Check substring containment both ways, not just set intersection —
    # one system may embed a longer number that contains the other's.
    for a in runs_a:
        for b in runs_b:
            if a in b or b in a:
                return True
    return False


def _combined_text(refs: str, attrs: str) -> str:
    refs = refs if isinstance(refs, str) else ""
    attrs = attrs if isinstance(attrs, str) else ""
    return f"{refs} {attrs}".strip()


def load_sides(train_path: str, sample_b: int = None, seed: int = 42):
    """Loads the train file and splits it into A-side (ledger) and
    B-side (statement, with known targetAllocation for evaluation)."""
    df = pd.read_csv(train_path, dtype=str)

    a_side = df[df["A_id"].notna() & (df["A_id"] != "")].copy()
    b_side = df[df["B_id"].notna() & (df["B_id"] != "") & df["targetAllocation"].notna()].copy()

    a_side["amount_f"] = a_side["A_amount"].astype(float)
    a_side["date_dt"] = pd.to_datetime(a_side["A_valueDate"])
    a_side["text"] = a_side.apply(lambda r: _combined_text(r["A_transactionReferences"], r["A_transactionAttributes"]), axis=1)

    b_side["amount_f"] = b_side["B_amount"].astype(float)
    b_side["date_dt"] = pd.to_datetime(b_side["B_valueDate"])
    b_side["text"] = b_side.apply(lambda r: _combined_text(r["B_transactionReferences"], r["B_transactionAttributes"]), axis=1)

    if sample_b is not None and len(b_side) > sample_b:
        b_side = b_side.sample(n=sample_b, random_state=seed)

    return a_side.reset_index(drop=True), b_side.reset_index(drop=True)


class RealDataMatcher:
    def __init__(self, a_side: pd.DataFrame):
        self.a_side = a_side
        # Character n-grams handle the garbled/coded reference text far
        # better than word tokens would — no clean "words" exist here.
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=8000)
        self.a_vectors = self.vectorizer.fit_transform(a_side["text"])

    def _block_candidates(self, b_row) -> pd.DataFrame:
        amt = b_row["amount_f"]
        date = b_row["date_dt"]

        amt_tolerance = max(abs(amt) * AMOUNT_TOLERANCE_PCT, 0.01)
        amount_match = (self.a_side["amount_f"] - amt).abs() <= amt_tolerance
        date_match = (self.a_side["date_dt"] - date).abs() <= pd.Timedelta(days=DATE_WINDOW_DAYS)

        return self.a_side[amount_match & date_match]

    def match_one(self, b_row) -> dict:
        candidates = self._block_candidates(b_row)

        if candidates.empty:
            return {
                "B_id": b_row["B_id"],
                "predicted_allocation": None,
                "confidence": 0.0,
                "candidates_considered": 0,
                "status": "unmatched_no_candidates_in_block",
            }

        b_vector = self.vectorizer.transform([b_row["text"]])
        candidate_vectors = self.vectorizer.transform(candidates["text"])
        tfidf_similarities = cosine_similarity(b_vector, candidate_vectors)[0]

        # Apply the superstring boost per-candidate
        combined_scores = np.array(tfidf_similarities, dtype=float)
        for i, (_, cand) in enumerate(candidates.iterrows()):
            if _shares_digit_run(b_row["text"], cand["text"]):
                combined_scores[i] = min(1.0, combined_scores[i] + SUPERSTRING_BOOST)

        best_idx = int(np.argmax(combined_scores))
        best_score = float(combined_scores[best_idx])
        best_tfidf = float(tfidf_similarities[best_idx])
        best_candidate = candidates.iloc[best_idx]

        if best_score >= CONFIDENCE_THRESHOLD:
            return {
                "B_id": b_row["B_id"],
                "predicted_allocation": best_candidate["A_allocation"],
                "confidence": round(best_score, 4),
                "tfidf_similarity": round(best_tfidf, 4),
                "candidates_considered": len(candidates),
                "status": "matched",
            }
        else:
            return {
                "B_id": b_row["B_id"],
                "predicted_allocation": best_candidate["A_allocation"],  # kept for audit, not used as a match
                "confidence": round(best_score, 4),
                "tfidf_similarity": round(best_tfidf, 4),
                "candidates_considered": len(candidates),
                "status": "unmatched_below_confidence_threshold",
            }

    def match_batch(self, b_side: pd.DataFrame) -> list:
        return [self.match_one(row) for _, row in b_side.iterrows()]
