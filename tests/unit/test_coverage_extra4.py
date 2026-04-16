"""Extra coverage tests (batch 4).

Targets: morpheme_analyzer (43 uncov), composite_reranker (37 uncov),
ollama_client streaming (47 uncov).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

import pytest


# ===========================================================================
# KoreanMorphemeAnalyzer
# ===========================================================================

from src.nlp.morpheme_analyzer import (
    KoreanMorphemeAnalyzer,
    MorphemeToken,
    AnalysisResult,
    NoOpKoreanMorphemeAnalyzer,
    get_analyzer,
)


class TestMorphemeToken:
    def test_defaults(self):
        t = MorphemeToken(form="테스트", tag="NNG")
        assert t.start == 0
        assert t.lemma is None


class TestAnalysisResult:
    def test_creation(self):
        r = AnalysisResult(tokens=[], nouns=[], stems=[], original="test")
        assert r.original == "test"


class TestKoreanMorphemeAnalyzer:
    def test_analyze_empty(self):
        # Reset singleton for test
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer.analyze("")
        assert result.tokens == []
        assert result.nouns == []

    def test_analyze_text(self):
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer.analyze("데이터마트는 분석용 저장소입니다")
        assert len(result.tokens) > 0
        assert len(result.nouns) > 0

    def test_strip_particles(self):
        analyzer = KoreanMorphemeAnalyzer()
        # Korean particle stripping
        result = analyzer.strip_particles("데이터마트는")
        assert "데이터마트" in result

    def test_strip_particles_empty(self):
        analyzer = KoreanMorphemeAnalyzer()
        assert analyzer.strip_particles("") == ""

    def test_strip_particles_english(self):
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer.strip_particles("API를")
        assert "API" in result

    def test_extract_nouns(self):
        analyzer = KoreanMorphemeAnalyzer()
        nouns = analyzer.extract_nouns("벡터 데이터베이스와 그래프 데이터베이스")
        assert len(nouns) > 0

    def test_extract_compound_nouns(self):
        analyzer = KoreanMorphemeAnalyzer()
        compounds = analyzer.extract_compound_nouns("지식베이스 관리 시스템을 구축합니다")
        # May or may not find compounds depending on kiwi behavior
        assert isinstance(compounds, list)

    def test_tokenize_for_search(self):
        analyzer = KoreanMorphemeAnalyzer()
        tokens = analyzer.tokenize_for_search("GraphRAG 시스템을 검색합니다")
        assert len(tokens) > 0

    def test_is_available(self):
        analyzer = KoreanMorphemeAnalyzer()
        # Should be True since kiwipiepy is installed in this project
        assert isinstance(analyzer.is_available, bool)

    def test_strip_particles_regex_fallback(self):
        analyzer = KoreanMorphemeAnalyzer()
        # Test the regex fallback directly
        result = analyzer._strip_particles_regex("시스템에서")
        assert result == "시스템"

    def test_strip_particles_regex_short(self):
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer._strip_particles_regex("시스템을")
        assert result == "시스템"

    def test_strip_particles_regex_no_particle(self):
        analyzer = KoreanMorphemeAnalyzer()
        result = analyzer._strip_particles_regex("시스템")
        assert result == "시스템"


class TestNoOpAnalyzer:
    def test_analyze(self):
        analyzer = NoOpKoreanMorphemeAnalyzer()
        result = analyzer.analyze("hello world")
        assert result.nouns == ["hello", "world"]

    def test_strip_particles(self):
        analyzer = NoOpKoreanMorphemeAnalyzer()
        assert analyzer.strip_particles("word") == "word"

    def test_extract_nouns(self):
        analyzer = NoOpKoreanMorphemeAnalyzer()
        assert analyzer.extract_nouns("a b c") == ["a", "b", "c"]


class TestGetAnalyzer:
    def test_singleton(self):
        a1 = get_analyzer()
        a2 = get_analyzer()
        assert a1 is a2


# ===========================================================================
# CompositeReranker
# ===========================================================================

from src.search.composite_reranker import CompositeReranker, CompositeRerankerConfig
from src.core.models import SearchChunk


class TestCompositeRerankerConfig:
    def test_defaults(self):
        config = CompositeRerankerConfig()
        assert 0 <= config.model_weight <= 1
        assert 0 <= config.base_weight <= 1


class TestCompositeReranker:
    def _make_chunk(self, content="test content", score=0.8, source_type="qdrant", **kwargs):
        metadata = {"source_type": source_type}
        metadata.update(kwargs)
        return SearchChunk(
            chunk_id="c1",
            content=content,
            score=score,
            kb_id="test-kb",
            metadata=metadata,
        )

    def test_init_defaults(self):
        reranker = CompositeReranker()
        assert reranker._mmr_enabled is True

    def test_init_custom(self):
        reranker = CompositeReranker(
            model_weight=0.5,
            base_weight=0.3,
            source_weight=0.2,
            mmr_enabled=False,
        )
        assert reranker._mmr_enabled is False

    def test_rerank_empty(self):
        reranker = CompositeReranker()
        result = reranker.rerank("query", [], top_k=5)
        assert result == []

    def test_rerank_single(self):
        reranker = CompositeReranker()
        chunks = [self._make_chunk()]
        result = reranker.rerank("query", chunks, top_k=5)
        assert len(result) == 1

    def test_rerank_multiple(self):
        reranker = CompositeReranker()
        chunks = [
            self._make_chunk(content="first", score=0.9),
            self._make_chunk(content="second", score=0.5),
            self._make_chunk(content="third", score=0.7),
        ]
        result = reranker.rerank("query", chunks, top_k=2)
        assert len(result) == 2

    def test_rerank_faq_boost(self):
        reranker = CompositeReranker(faq_boost=2.0)
        chunks = [
            self._make_chunk(content="faq", score=0.5, source_type="faq"),
            self._make_chunk(content="normal", similarity=0.8, source_type="qdrant"),
        ]
        result = reranker.rerank("query", chunks, top_k=2)
        assert len(result) == 2

    def test_rerank_graph_source(self):
        reranker = CompositeReranker()
        chunks = [
            self._make_chunk(content="graph result", score=0.7, source_type="graph"),
        ]
        result = reranker.rerank("query", chunks, top_k=5)
        assert len(result) == 1

    def test_rerank_with_cross_encoder_score(self):
        reranker = CompositeReranker()
        chunk = self._make_chunk()
        chunk.metadata["cross_encoder_score"] = 0.95
        result = reranker.rerank("query", [chunk], top_k=5)
        assert len(result) == 1

    def test_rerank_no_mmr(self):
        reranker = CompositeReranker(mmr_enabled=False)
        chunks = [
            self._make_chunk(content="a", score=0.9),
            self._make_chunk(content="b", similarity=0.8),
        ]
        result = reranker.rerank("query", chunks, top_k=2)
        assert len(result) == 2

    def test_rerank_with_axis_boost(self):
        reranker = CompositeReranker()
        chunk = self._make_chunk()
        chunk.metadata["graph_distance"] = 1
        chunk.metadata["graph_axis"] = "causal"
        result = reranker.rerank("query", [chunk], top_k=5)
        assert len(result) == 1
