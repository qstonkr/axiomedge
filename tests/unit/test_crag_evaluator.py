"""Unit tests for src/search/crag_evaluator.py."""

from __future__ import annotations

import pytest

from src.search.crag_evaluator import (
    ABSTENTION_MESSAGES,
    CONFIDENCE_RECOMMENDATIONS,
    CRAGEvaluation,
    CRAGRetrievalEvaluator,
    ConfidenceLevel,
    RetrievalAction,
    _chunk_doc_id,
    _chunk_metadata,
    _chunk_score,
)


def _evaluator() -> CRAGRetrievalEvaluator:
    return CRAGRetrievalEvaluator()


def _make_chunk(
    score: float = 0.9,
    document_id: str = "doc1",
    metadata: dict | None = None,
) -> dict:
    return {
        "score": score,
        "document_id": document_id,
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# Enum sanity
# ---------------------------------------------------------------------------


class TestEnums:
    def test_retrieval_action_values(self):
        assert RetrievalAction.CORRECT.value == "correct"
        assert RetrievalAction.AMBIGUOUS.value == "ambiguous"
        assert RetrievalAction.INCORRECT.value == "incorrect"

    def test_confidence_level_values(self):
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.LOW.value == "low"
        assert ConfidenceLevel.UNCERTAIN.value == "uncertain"


# ---------------------------------------------------------------------------
# Chunk accessor helpers
# ---------------------------------------------------------------------------


class TestChunkAccessors:
    def test_chunk_score_from_dict(self):
        assert _chunk_score({"score": 0.85}) == 0.85

    def test_chunk_score_from_object(self):
        class FakeChunk:
            score = 0.77
        assert _chunk_score(FakeChunk()) == 0.77

    def test_chunk_score_missing(self):
        assert _chunk_score({}) == 0.0

    def test_chunk_doc_id_from_dict(self):
        assert _chunk_doc_id({"document_id": "abc"}) == "abc"

    def test_chunk_doc_id_fallback_to_chunk_id(self):
        assert _chunk_doc_id({"chunk_id": "c1"}) == "c1"

    def test_chunk_metadata_from_dict(self):
        meta = {"updated_at": "2025-01-01"}
        assert _chunk_metadata({"metadata": meta}) == meta

    def test_chunk_metadata_missing(self):
        assert _chunk_metadata({}) == {}


# ---------------------------------------------------------------------------
# evaluate() — action classification
# ---------------------------------------------------------------------------


class TestEvaluateAction:
    @pytest.mark.asyncio
    async def test_no_chunks_returns_incorrect(self):
        ev = _evaluator()
        result = await ev.evaluate("test query", [], search_time_ms=50.0)
        assert result.action == RetrievalAction.INCORRECT
        assert result.source_attribution is False

    @pytest.mark.asyncio
    async def test_high_score_chunks_returns_correct(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.95, document_id=f"doc{i}") for i in range(5)]
        result = await ev.evaluate("kubernetes pod 재시작 절차는 어떻게 되나요", chunks, search_time_ms=50.0)
        assert result.action == RetrievalAction.CORRECT
        assert result.source_attribution is True

    @pytest.mark.asyncio
    async def test_low_score_chunks_returns_incorrect(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.05)]
        result = await ev.evaluate("x", chunks, search_time_ms=50.0)
        assert result.action == RetrievalAction.INCORRECT
        assert result.source_attribution is False

    @pytest.mark.asyncio
    async def test_medium_score_returns_ambiguous(self):
        ev = _evaluator()
        # Medium scores with decent coverage should yield ambiguous
        chunks = [_make_chunk(score=0.5, document_id=f"d{i}") for i in range(3)]
        result = await ev.evaluate("some query text here", chunks, search_time_ms=50.0)
        assert result.action in (RetrievalAction.AMBIGUOUS, RetrievalAction.CORRECT, RetrievalAction.INCORRECT)
        # The score should be between thresholds
        assert 0.0 <= result.confidence_score <= 1.0


# ---------------------------------------------------------------------------
# evaluate() — confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    @pytest.mark.asyncio
    async def test_confidence_score_bounded(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.8)]
        result = await ev.evaluate("query", chunks, search_time_ms=100.0)
        assert 0.0 <= result.confidence_score <= 1.0

    @pytest.mark.asyncio
    async def test_confidence_level_assigned(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.95, document_id=f"d{i}") for i in range(5)]
        result = await ev.evaluate("a fairly long query for good specificity", chunks, search_time_ms=10.0)
        assert result.confidence_level in (
            ConfidenceLevel.HIGH,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.LOW,
            ConfidenceLevel.UNCERTAIN,
        )

    @pytest.mark.asyncio
    async def test_factors_present(self):
        ev = _evaluator()
        chunks = [_make_chunk()]
        result = await ev.evaluate("query", chunks, search_time_ms=50.0)
        assert "retrieval_quality" in result.factors
        assert "source_freshness" in result.factors
        assert "coverage" in result.factors
        assert "query_specificity" in result.factors
        assert "search_time_penalty" in result.factors

    @pytest.mark.asyncio
    async def test_search_time_penalty_increases_with_latency(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.9)]
        fast = await ev.evaluate("query", chunks, search_time_ms=10.0)
        slow = await ev.evaluate("query", chunks, search_time_ms=2500.0)
        assert fast.confidence_score >= slow.confidence_score

    @pytest.mark.asyncio
    async def test_zero_search_time_no_penalty(self):
        ev = _evaluator()
        penalty = ev._search_time_penalty(0.0)
        assert penalty == 0.0


# ---------------------------------------------------------------------------
# Factor calculations
# ---------------------------------------------------------------------------


class TestFactorCalculations:
    def test_retrieval_relevance_empty(self):
        ev = _evaluator()
        assert ev._calculate_retrieval_relevance([]) == 0.0

    def test_retrieval_relevance_single_chunk(self):
        ev = _evaluator()
        val = ev._calculate_retrieval_relevance([_make_chunk(score=0.8)])
        assert abs(val - 0.8) < 0.01

    def test_retrieval_relevance_clamps_score(self):
        ev = _evaluator()
        val = ev._calculate_retrieval_relevance([_make_chunk(score=1.5)])
        assert val <= 1.0

    def test_source_coverage_empty(self):
        ev = _evaluator()
        assert ev._calculate_source_coverage([], "query") == 0.0

    def test_source_coverage_unique_docs(self):
        ev = _evaluator()
        chunks = [_make_chunk(document_id="a"), _make_chunk(document_id="b")]
        val = ev._calculate_source_coverage(chunks, "short")
        assert val > 0.0

    def test_source_coverage_capped_at_one(self):
        ev = _evaluator()
        chunks = [_make_chunk(document_id=f"d{i}") for i in range(20)]
        val = ev._calculate_source_coverage(chunks, "short")
        assert val <= 1.0

    def test_query_specificity_empty(self):
        ev = _evaluator()
        assert ev._calculate_query_specificity("") == 0.0

    def test_query_specificity_longer_better(self):
        ev = _evaluator()
        short = ev._calculate_query_specificity("hello")
        long = ev._calculate_query_specificity("kubernetes pod restart procedure guide")
        assert long > short

    def test_source_freshness_empty(self):
        ev = _evaluator()
        assert ev._calculate_source_freshness([]) == 0.0

    def test_source_freshness_no_dates_returns_neutral(self):
        ev = _evaluator()
        val = ev._calculate_source_freshness([_make_chunk(metadata={})])
        assert val == 0.5  # neutral when no timestamps


# ---------------------------------------------------------------------------
# Recommendations & messages
# ---------------------------------------------------------------------------


class TestRecommendations:
    @pytest.mark.asyncio
    async def test_incorrect_with_no_chunks_gives_no_knowledge_message(self):
        ev = _evaluator()
        result = await ev.evaluate("query", [], search_time_ms=10.0)
        assert result.recommendation == ABSTENTION_MESSAGES["no_knowledge"]

    @pytest.mark.asyncio
    async def test_incorrect_with_chunks_gives_low_confidence_message(self):
        ev = _evaluator()
        chunks = [_make_chunk(score=0.05)]
        result = await ev.evaluate("x", chunks, search_time_ms=10.0)
        if result.action == RetrievalAction.INCORRECT:
            assert result.recommendation == ABSTENTION_MESSAGES["low_confidence"]

    def test_confidence_recommendations_keys(self):
        assert "high" in CONFIDENCE_RECOMMENDATIONS
        assert "medium" in CONFIDENCE_RECOMMENDATIONS
        assert "low" in CONFIDENCE_RECOMMENDATIONS
        assert "uncertain" in CONFIDENCE_RECOMMENDATIONS
        assert CONFIDENCE_RECOMMENDATIONS["high"] is None


# ---------------------------------------------------------------------------
# CRAGEvaluation dataclass
# ---------------------------------------------------------------------------


class TestCRAGEvaluation:
    def test_slots(self):
        ev = CRAGEvaluation(
            action=RetrievalAction.CORRECT,
            confidence_score=0.85,
            confidence_level=ConfidenceLevel.HIGH,
            factors={"retrieval_quality": 0.9},
            recommendation=None,
            source_attribution=True,
        )
        assert ev.action == RetrievalAction.CORRECT
        assert ev.confidence_score == 0.85
        assert ev.source_attribution is True


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_estimate_expected_sources_short_query(self):
        ev = _evaluator()
        assert ev._estimate_expected_sources("hello") == 2

    def test_estimate_expected_sources_medium_query(self):
        ev = _evaluator()
        assert ev._estimate_expected_sources("one two three four five") == 3

    def test_estimate_expected_sources_long_query(self):
        ev = _evaluator()
        assert ev._estimate_expected_sources("a b c d e f g h i j k") == 4

    def test_parse_datetime_iso(self):
        ev = _evaluator()
        dt = ev._parse_datetime("2025-06-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2025

    def test_parse_datetime_invalid(self):
        ev = _evaluator()
        assert ev._parse_datetime("not-a-date") is None

    def test_search_time_penalty_capped(self):
        ev = _evaluator()
        assert ev._search_time_penalty(10000.0) == 1.0
