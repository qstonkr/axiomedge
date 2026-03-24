"""Lexical scorer for term similarity.

Purpose:
    Compute language-agnostic lexical similarity for logical/physical names.

Features:
    - Character n-gram Jaccard with adaptive n for Korean (bigram) vs English (trigram).
    - Normalized Levenshtein for physical name variants.
    - Weighted blend without external dependencies.

Usage:
    scorer = LexicalScorer()

Examples:
    score = scorer.score("고객명", "고객성명", "cust_name", "customer_name")

Extracted from: oreo-ecosystem (infrastructure/data_standard/lexical_scorer.py)
"""

from __future__ import annotations

import re

from src.nlp.term_normalizer import TermNormalizer

_KOREAN_CHAR_RE = re.compile(r"[\uac00-\ud7a3]")


def _contains_korean(text: str) -> bool:
    """Check if text contains Korean characters."""
    return bool(_KOREAN_CHAR_RE.search(text))


class LexicalScorer:
    """Lexical similarity scorer for short data-standard terms."""

    def __init__(self, *, n_gram_size: int = 3) -> None:
        self._n_gram_size = max(2, n_gram_size)

    def score(
        self,
        query_logical_name: str,
        candidate_logical_name: str,
        query_physical_name: str | None = None,
        candidate_physical_name: str | None = None,
    ) -> float:
        """Compute lexical similarity score in range [0,1]."""
        query_logical = TermNormalizer.normalize_for_comparison(query_logical_name)
        candidate_logical = TermNormalizer.normalize_for_comparison(candidate_logical_name)

        # Adaptive n-gram: use bigram for Korean short terms (source: SIGIR '96, ACL 2018)
        effective_n = self._effective_n(query_logical, candidate_logical)
        jaccard_score = self._jaccard_ngrams(query_logical, candidate_logical, n=effective_n)

        query_physical = TermNormalizer.normalize_for_comparison(query_physical_name or query_logical_name)
        candidate_physical = TermNormalizer.normalize_for_comparison(
            candidate_physical_name or candidate_logical_name
        )
        levenshtein_score = self._normalized_levenshtein(query_physical, candidate_physical)

        return round(self._clamp((0.7 * jaccard_score) + (0.3 * levenshtein_score)), 6)

    def _effective_n(self, a: str, b: str) -> int:
        """Choose bigram for short Korean terms, configured n otherwise."""
        if _contains_korean(a) or _contains_korean(b):
            shorter = min(len(a), len(b)) if a and b else 0
            if shorter <= 4:
                return 2
        return self._n_gram_size

    def _jaccard_ngrams(self, a: str, b: str, *, n: int | None = None) -> float:
        effective_n = n if n is not None else self._n_gram_size
        a_ngrams = self._ngrams(a, n=effective_n)
        b_ngrams = self._ngrams(b, n=effective_n)

        if not a_ngrams and not b_ngrams:
            return 1.0
        if not a_ngrams or not b_ngrams:
            return 0.0

        intersection = len(a_ngrams & b_ngrams)
        union = len(a_ngrams | b_ngrams)
        if union == 0:
            return 0.0
        return intersection / union

    def _ngrams(self, text: str, *, n: int | None = None) -> set[str]:
        effective_n = n if n is not None else self._n_gram_size
        if not text:
            return set()
        if len(text) < effective_n:
            return {text}

        return {text[i : i + effective_n] for i in range(0, len(text) - effective_n + 1)}

    def _normalized_levenshtein(self, a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0

        distance = self._levenshtein_distance(a, b)
        max_length = max(len(a), len(b))
        if max_length == 0:
            return 1.0

        return 1.0 - (distance / max_length)

    @staticmethod
    def _levenshtein_distance(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)

        previous = list(range(len(b) + 1))
        for i, char_a in enumerate(a, start=1):
            current = [i]
            for j, char_b in enumerate(b, start=1):
                insert_cost = current[j - 1] + 1
                delete_cost = previous[j] + 1
                replace_cost = previous[j - 1] + (0 if char_a == char_b else 1)
                current.append(min(insert_cost, delete_cost, replace_cost))
            previous = current

        return previous[-1]

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
