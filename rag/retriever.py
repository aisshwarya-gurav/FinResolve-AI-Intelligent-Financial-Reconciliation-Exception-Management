"""
Historical Case Retriever — no LLM involved, same pattern already used
in real_data/real_matcher.py: scikit-learn TfidfVectorizer + cosine
similarity. No new dependency.

One deliberate difference from real_matcher.py: that file uses
character n-grams (analyzer="char_wb") because BenchRec's reference
text is garbled/obfuscated codes with no real words. This file uses
word-level n-grams instead, because the historical case library and the
query built from an exception record are both natural-language-ish text
(e.g. "payment without valid invoice") — word tokens are the right unit
of comparison here, character n-grams would be the wrong tool for this
particular text.
"""

import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .historical_cases import load_historical_cases


def build_query_text(record: dict) -> str:
    """Builds a search query from an incoming exception record.

    The strongest available signal is the record's 'issue' field (the
    exception_code set by our matchers, e.g. "payment_without_valid_invoice")
    — its vocabulary overlaps directly with the historical cases'
    observed_symptom/root_cause_category text (e.g. "payment", "invoice",
    "valid", "balance", "allocations"). Underscores are replaced with
    spaces so it tokenizes as words, not one long token.
    """
    if not isinstance(record, dict):
        return ""

    parts = []
    issue = record.get("issue")
    if issue and isinstance(issue, str):
        parts.append(issue.replace("_", " "))

    # A few other fields carry useful vocabulary when present, without
    # pulling in IDs/amounts (which are numeric/opaque, not useful for
    # word-level TF-IDF matching against natural-language case text).
    for key in ("status",):
        val = record.get(key)
        if val and isinstance(val, str):
            parts.append(val.replace("_", " "))

    return " ".join(parts).strip()


class HistoricalCaseRetriever:
    def __init__(self, ground_truth_path: str = None):
        if ground_truth_path:
            self.cases = load_historical_cases(ground_truth_path)
        else:
            self.cases = load_historical_cases()

        if not self.cases:
            raise ValueError("No historical cases loaded — check the ground truth path and filters.")

        self.vectorizer = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), stop_words="english")
        self.case_vectors = self.vectorizer.fit_transform([c["searchable_text"] for c in self.cases])

    def retrieve_similar(self, record: dict, k: int = 3) -> list:
        """Returns up to k historical cases most similar to the query
        built from `record`, each with a similarity_score, highest
        first. Returns [] safely on empty/invalid input rather than
        raising — retrieval is a "nice to have" input to the Explainer,
        not something that should crash the pipeline on a bad record."""

        if record is None or not isinstance(record, dict):
            return []
        if k <= 0:
            return []

        query_text = build_query_text(record)
        if not query_text:
            return []

        query_vector = self.vectorizer.transform([query_text])
        similarities = cosine_similarity(query_vector, self.case_vectors)[0]

        k = min(k, len(self.cases))
        top_indices = similarities.argsort()[::-1][:k]

        results = []
        seen_case_ids = set()
        for idx in top_indices:
            case = self.cases[idx]
            if case["case_id"] in seen_case_ids:
                continue  # defensive: corpus shouldn't have dupes, but never return dupes regardless
            seen_case_ids.add(case["case_id"])
            results.append({
                **{k: v for k, v in case.items() if k != "searchable_text"},
                "similarity_score": round(float(similarities[idx]), 4),
            })
        return results


_default_retriever = None


def retrieve_similar(record: dict, k: int = 3) -> list:
    """Module-level convenience function — lazily builds a single shared
    retriever (fitting TF-IDF once) rather than refitting on every call."""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HistoricalCaseRetriever()
    return _default_retriever.retrieve_similar(record, k=k)


if __name__ == "__main__":
    example = {"issue": "payment_without_valid_invoice", "payment_id": "PAY_TEST", "payment_amount": 100.0}
    results = retrieve_similar(example, k=3)
    for r in results:
        print(f"{r['case_id']} ({r['failure_type']}) — score {r['similarity_score']}")
