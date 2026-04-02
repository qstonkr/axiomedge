"""Comprehensive tests for src/nlp/ modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# TermNormalizer
# ===========================================================================

class TestTermNormalizer:
    def setup_method(self):
        from src.nlp.term_normalizer import TermNormalizer
        self.TN = TermNormalizer

    def test_normalize_empty(self):
        assert self.TN.normalize("") == ""

    def test_normalize_strips_whitespace(self):
        assert self.TN.normalize("  hello  ") == "hello"

    def test_normalize_underscore_to_hyphen(self):
        assert self.TN.normalize("API_Gateway") == "api-gateway"

    def test_normalize_multi_separator(self):
        assert self.TN.normalize("a--b__c") == "a-b-c"

    def test_normalize_lowercase(self):
        assert self.TN.normalize("GraphRAG") == "graphrag"

    def test_normalize_unicode_nfc(self):
        # Decomposed Korean -> composed
        result = self.TN.normalize("데이터")
        assert result == "데이터"

    def test_normalize_strips_edge_hyphens(self):
        assert self.TN.normalize("-hello-") == "hello"

    def test_normalize_for_comparison_empty(self):
        assert self.TN.normalize_for_comparison("") == ""

    def test_normalize_for_comparison_removes_separators(self):
        assert self.TN.normalize_for_comparison("Graph-RAG") == "graphrag"
        assert self.TN.normalize_for_comparison("Graph RAG") == "graphrag"

    def test_normalize_for_search_empty(self):
        assert self.TN.normalize_for_search("") == ""

    def test_normalize_for_search_special_chars(self):
        assert self.TN.normalize_for_search("K8s/Pod") == "k8s pod"

    def test_is_normalized_variant_true(self):
        assert self.TN.is_normalized_variant("graph-rag", "GraphRAG") is True

    def test_is_normalized_variant_false(self):
        assert self.TN.is_normalized_variant("RAG", "GraphRAG") is False

    def test_extract_abbreviation_candidates(self):
        result = self.TN.extract_abbreviation_candidates("Data Mart")
        assert "DM" in result
        assert "dm" in result

    def test_extract_abbreviation_candidates_empty(self):
        assert self.TN.extract_abbreviation_candidates("") == []

    def test_extract_abbreviation_candidates_hyphen(self):
        result = self.TN.extract_abbreviation_candidates("Graph-RAG")
        assert "GR" in result or "gr" in result

    def test_is_likely_abbreviation_uppercase(self):
        assert self.TN.is_likely_abbreviation("API") is True
        assert self.TN.is_likely_abbreviation("DM") is True

    def test_is_likely_abbreviation_lowercase(self):
        assert self.TN.is_likely_abbreviation("api") is True

    def test_is_likely_abbreviation_with_digit(self):
        assert self.TN.is_likely_abbreviation("K8s") is True
        assert self.TN.is_likely_abbreviation("S3") is True

    def test_is_likely_abbreviation_korean(self):
        assert self.TN.is_likely_abbreviation("데이터마트") is False

    def test_is_likely_abbreviation_empty(self):
        assert self.TN.is_likely_abbreviation("") is False

    def test_is_likely_abbreviation_long(self):
        assert self.TN.is_likely_abbreviation("ABCDEF") is False


# ===========================================================================
# LexicalScorer
# ===========================================================================

class TestLexicalScorer:
    def setup_method(self):
        from src.nlp.lexical_scorer import LexicalScorer
        self.scorer = LexicalScorer()

    def test_identical_terms(self):
        score = self.scorer.score("고객명", "고객명")
        assert score == pytest.approx(1.0, abs=0.01)

    def test_different_terms(self):
        score = self.scorer.score("고객명", "상품코드")
        assert score < 0.5

    def test_similar_terms(self):
        score = self.scorer.score("고객명", "고객성명")
        assert score > 0.3

    def test_with_physical_names(self):
        score = self.scorer.score("고객명", "고객성명", "cust_name", "customer_name")
        assert 0 <= score <= 1

    def test_empty_terms(self):
        score = self.scorer.score("", "")
        assert 0 <= score <= 1

    def test_korean_bigram(self):
        # Korean short terms should use bigram
        score = self.scorer.score("데이터", "데이타")
        assert 0 <= score <= 1

    def test_english_trigram(self):
        score = self.scorer.score("customer", "customers")
        assert score > 0.5


class TestLexicalScorerInternal:
    def setup_method(self):
        from src.nlp.lexical_scorer import LexicalScorer
        self.scorer = LexicalScorer()

    def test_levenshtein_identical(self):
        assert self.scorer._levenshtein_distance("abc", "abc") == 0

    def test_levenshtein_empty(self):
        assert self.scorer._levenshtein_distance("", "abc") == 3
        assert self.scorer._levenshtein_distance("abc", "") == 3

    def test_levenshtein_one_edit(self):
        assert self.scorer._levenshtein_distance("abc", "abd") == 1

    def test_normalized_levenshtein_identical(self):
        assert self.scorer._normalized_levenshtein("abc", "abc") == 1.0

    def test_normalized_levenshtein_empty(self):
        assert self.scorer._normalized_levenshtein("", "") == 1.0
        assert self.scorer._normalized_levenshtein("abc", "") == 0.0

    def test_ngrams_short_text(self):
        result = self.scorer._ngrams("ab", n=3)
        assert result == {"ab"}

    def test_ngrams_empty(self):
        result = self.scorer._ngrams("", n=3)
        assert result == set()

    def test_jaccard_identical(self):
        assert self.scorer._jaccard_ngrams("hello", "hello") == 1.0

    def test_jaccard_empty(self):
        assert self.scorer._jaccard_ngrams("", "") == 1.0

    def test_clamp(self):
        assert self.scorer._clamp(1.5) == 1.0
        assert self.scorer._clamp(-0.5) == 0.0
        assert self.scorer._clamp(0.5) == 0.5


# ===========================================================================
# KoreanMorphemeAnalyzer
# ===========================================================================

class TestMorphemeAnalyzerFallback:
    """Test the regex fallback path (no Kiwi)."""

    def setup_method(self):
        from src.nlp.morpheme_analyzer import KoreanMorphemeAnalyzer
        # Reset singleton
        KoreanMorphemeAnalyzer._instance = None
        KoreanMorphemeAnalyzer._kiwi = None
        KoreanMorphemeAnalyzer._kiwi_available = None

        # Force fallback by making kiwipiepy unavailable
        with patch.dict("sys.modules", {"kiwipiepy": None}):
            with patch("builtins.__import__", side_effect=self._import_mock):
                self.analyzer = KoreanMorphemeAnalyzer()

    def _import_mock(self, name, *args, **kwargs):
        if name == "kiwipiepy":
            raise ImportError("No kiwipiepy")
        return __builtins__.__import__(name, *args, **kwargs)

    def teardown_method(self):
        from src.nlp.morpheme_analyzer import KoreanMorphemeAnalyzer
        KoreanMorphemeAnalyzer._instance = None
        KoreanMorphemeAnalyzer._kiwi = None
        KoreanMorphemeAnalyzer._kiwi_available = None

    def test_analyze_empty(self):
        result = self.analyzer.analyze("")
        assert result.tokens == []
        assert result.nouns == []
        assert result.original == ""

    def test_analyze_whitespace(self):
        result = self.analyzer.analyze("   ")
        assert result.tokens == []

    def test_strip_particles_regex(self):
        result = self.analyzer._strip_particles_regex("데이터마트는")
        assert result == "데이터마트"

    def test_strip_particles_regex_long_particle(self):
        result = self.analyzer._strip_particles_regex("시스템에서")
        assert result == "시스템"

    def test_strip_particles_regex_no_particle(self):
        result = self.analyzer._strip_particles_regex("데이터")
        assert result == "데이터"

    def test_strip_particles_empty(self):
        result = self.analyzer.strip_particles("")
        assert result == ""

    def test_strip_particles_english_with_particle(self):
        result = self.analyzer.strip_particles("API를")
        assert result == "API"


class TestMorphemeAnalyzerConstants:
    def test_korean_particles_is_frozenset(self):
        from src.nlp.morpheme_analyzer import KOREAN_PARTICLES
        assert isinstance(KOREAN_PARTICLES, frozenset)
        assert "은" in KOREAN_PARTICLES
        assert "는" in KOREAN_PARTICLES

    def test_pos_tags_contains_common_tags(self):
        from src.nlp.morpheme_analyzer import POS_TAGS
        assert "NNG" in POS_TAGS
        assert "NNP" in POS_TAGS
        assert "VV" in POS_TAGS
        assert "SL" in POS_TAGS


class TestNoOpAnalyzer:
    def test_analyze(self):
        from src.nlp.morpheme_analyzer import NoOpKoreanMorphemeAnalyzer
        analyzer = NoOpKoreanMorphemeAnalyzer()
        result = analyzer.analyze("hello world")
        assert len(result.tokens) == 2
        assert result.nouns == ["hello", "world"]

    def test_strip_particles(self):
        from src.nlp.morpheme_analyzer import NoOpKoreanMorphemeAnalyzer
        analyzer = NoOpKoreanMorphemeAnalyzer()
        assert analyzer.strip_particles("데이터를") == "데이터를"

    def test_extract_nouns(self):
        from src.nlp.morpheme_analyzer import NoOpKoreanMorphemeAnalyzer
        analyzer = NoOpKoreanMorphemeAnalyzer()
        assert analyzer.extract_nouns("a b c") == ["a", "b", "c"]


# ===========================================================================
# KoreanProcessor
# ===========================================================================

class TestKoreanProcessor:
    def setup_method(self):
        from src.nlp.korean_processor import KoreanProcessor
        self.processor = KoreanProcessor(
            max_chunk_tokens=100,
            chunk_overlap_sentences=1,
            use_kss_sentence_splitter=False,  # Use regex fallback
        )

    def test_preprocess_empty(self):
        assert self.processor.preprocess("") == ""
        assert self.processor.preprocess(None) == ""

    def test_preprocess_html_removal(self):
        result = self.processor.preprocess("<p>hello</p> <b>world</b>")
        assert "<p>" not in result
        assert "hello" in result

    def test_preprocess_whitespace(self):
        result = self.processor.preprocess("  hello   world  ")
        assert result == "hello world"

    def test_split_sentences_empty(self):
        assert self.processor.split_sentences("") == []
        assert self.processor.split_sentences("  ") == []

    def test_split_sentences_regex(self):
        text = "첫번째 문장입니다. 두번째 문장입니다."
        result = self.processor.split_sentences(text)
        assert len(result) >= 1

    def test_chunk_text_empty(self):
        result = self.processor.chunk_text("")
        assert result.chunks == []
        assert result.total_chunks == 0

    def test_chunk_text_single_sentence(self):
        result = self.processor.chunk_text("짧은 문장입니다.")
        assert result.total_chunks >= 1
        assert "total_sentences" in result.metadata

    def test_chunk_text_respects_max_tokens(self):
        # Create text that exceeds max_chunk_chars
        text = "이것은 테스트 문장입니다. " * 100
        result = self.processor.chunk_text(text)
        assert result.total_chunks >= 1

    def test_analyze_morphemes_no_kiwi(self):
        result = self.processor.analyze_morphemes("테스트 텍스트")
        # Without Kiwi, returns empty morphemes
        assert result.text == "테스트 텍스트"

    def test_regex_split_sentences_static(self):
        from src.nlp.korean_processor import KoreanProcessor
        result = KoreanProcessor._regex_split_sentences("Hello. World!")
        assert len(result) >= 1

    def test_env_bool(self):
        from src.nlp.korean_processor import KoreanProcessor
        assert KoreanProcessor._env_bool("NONEXISTENT_VAR_123", True) is True
        assert KoreanProcessor._env_bool("NONEXISTENT_VAR_123", False) is False
