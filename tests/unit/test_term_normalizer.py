"""Unit tests for src/nlp/term_normalizer.py

Tests TermNormalizer: normalize, normalize_for_comparison, normalize_for_search,
is_normalized_variant, abbreviation detection/extraction.
"""

from __future__ import annotations

import pytest

from src.nlp.term_normalizer import TermNormalizer


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_empty_string(self) -> None:
        assert TermNormalizer.normalize("") == ""

    def test_strip_whitespace_and_lowercase(self) -> None:
        assert TermNormalizer.normalize("  Graph-RAG  ") == "graph-rag"

    def test_underscore_to_hyphen(self) -> None:
        assert TermNormalizer.normalize("API_Gateway") == "api-gateway"

    def test_korean_underscore(self) -> None:
        assert TermNormalizer.normalize("데이터_마트") == "데이터-마트"

    def test_consecutive_separators_collapsed(self) -> None:
        assert TermNormalizer.normalize("a__b--c") == "a-b-c"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert TermNormalizer.normalize("-test-") == "test"

    def test_unicode_nfc_normalization(self) -> None:
        # Decomposed hangul (NFD) should become composed (NFC)
        nfd = "\u1100\u1161"  # ㄱ + ㅏ = 가 in NFC
        result = TermNormalizer.normalize(nfd)
        assert "\uAC00" in result or result == "가"  # NFC composed


# ---------------------------------------------------------------------------
# normalize_for_comparison
# ---------------------------------------------------------------------------

class TestNormalizeForComparison:
    def test_empty_string(self) -> None:
        assert TermNormalizer.normalize_for_comparison("") == ""

    def test_removes_hyphens_and_spaces(self) -> None:
        assert TermNormalizer.normalize_for_comparison("Graph-RAG") == "graphrag"

    def test_space_separated(self) -> None:
        assert TermNormalizer.normalize_for_comparison("Graph RAG") == "graphrag"

    def test_camelcase(self) -> None:
        assert TermNormalizer.normalize_for_comparison("GraphRAG") == "graphrag"


# ---------------------------------------------------------------------------
# normalize_for_search
# ---------------------------------------------------------------------------

class TestNormalizeForSearch:
    def test_empty_string(self) -> None:
        assert TermNormalizer.normalize_for_search("") == ""

    def test_special_chars_to_space(self) -> None:
        assert TermNormalizer.normalize_for_search("K8s/Pod") == "k8s pod"

    def test_hyphens_to_space(self) -> None:
        result = TermNormalizer.normalize_for_search("graph-rag")
        assert result == "graph rag"


# ---------------------------------------------------------------------------
# is_normalized_variant
# ---------------------------------------------------------------------------

class TestIsNormalizedVariant:
    def test_variant_true(self) -> None:
        assert TermNormalizer.is_normalized_variant("graph-rag", "GraphRAG") is True

    def test_variant_false(self) -> None:
        assert TermNormalizer.is_normalized_variant("RAG", "GraphRAG") is False

    def test_identical_strings(self) -> None:
        assert TermNormalizer.is_normalized_variant("API", "API") is True


# ---------------------------------------------------------------------------
# extract_abbreviation_candidates
# ---------------------------------------------------------------------------

class TestExtractAbbreviationCandidates:
    def test_empty_string(self) -> None:
        assert TermNormalizer.extract_abbreviation_candidates("") == []

    def test_two_word_term(self) -> None:
        result = TermNormalizer.extract_abbreviation_candidates("Data Mart")
        assert "DM" in result
        assert "dm" in result

    def test_hyphenated_term(self) -> None:
        result = TermNormalizer.extract_abbreviation_candidates("Graph-RAG")
        assert "GR" in result or "gr" in result

    def test_single_word_no_candidates(self) -> None:
        result = TermNormalizer.extract_abbreviation_candidates("Server")
        assert result == []


# ---------------------------------------------------------------------------
# is_likely_abbreviation
# ---------------------------------------------------------------------------

class TestIsLikelyAbbreviation:
    def test_empty_string(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("") is False

    def test_uppercase_abbreviation(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("API") is True

    def test_lowercase_abbreviation(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("dm") is True

    def test_alphanumeric_abbreviation(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("K8s") is True
        assert TermNormalizer.is_likely_abbreviation("EC2") is True

    def test_korean_not_abbreviation(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("데이터마트") is False

    def test_long_word_not_abbreviation(self) -> None:
        assert TermNormalizer.is_likely_abbreviation("Gateway") is False
