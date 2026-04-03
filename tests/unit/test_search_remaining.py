"""Comprehensive tests for remaining search modules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest

# ===========================================================================
# CRAGRetrievalEvaluator
# ===========================================================================

class TestCRAGEvaluator:
    def setup_method(self):
        from src.search.crag_evaluator import CRAGRetrievalEvaluator
        self.evaluator = CRAGRetrievalEvaluator()

    async def test_evaluate_no_chunks(self):
        result = await self.evaluator.evaluate("test query", [], 100.0)
        assert result.action.value == "incorrect"
        # Score is non-zero because query_specificity still contributes
        assert result.confidence_score < 0.3
        assert result.source_attribution is False

    async def test_evaluate_high_quality_chunks(self):
        chunks = [
            {"score": 0.95, "document_id": "d1", "metadata": {"updated_at": datetime.now(timezone.utc).isoformat()}},
            {"score": 0.90, "document_id": "d2", "metadata": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        ]
        result = await self.evaluator.evaluate("Kubernetes 설치 방법은?", chunks, 50.0)
        assert result.confidence_score > 0
        assert result.source_attribution is True

    async def test_evaluate_low_quality_chunks(self):
        chunks = [
            {"score": 0.1, "document_id": "d1", "metadata": {}},
        ]
        result = await self.evaluator.evaluate("x", chunks, 100.0)
        assert result.confidence_score < 0.5

    def test_calculate_retrieval_relevance_empty(self):
        assert self.evaluator._calculate_retrieval_relevance([]) == 0.0

    def test_calculate_retrieval_relevance_weighted(self):
        chunks = [{"score": 1.0}, {"score": 0.5}, {"score": 0.1}]
        score = self.evaluator._calculate_retrieval_relevance(chunks)
        assert 0 < score <= 1.0

    def test_calculate_source_coverage(self):
        chunks = [
            {"document_id": "d1"}, {"document_id": "d2"}, {"document_id": "d1"},
        ]
        score = self.evaluator._calculate_source_coverage(chunks, "test query")
        assert score > 0

    def test_calculate_source_coverage_empty(self):
        assert self.evaluator._calculate_source_coverage([], "q") == 0.0

    def test_calculate_query_specificity(self):
        score = self.evaluator._calculate_query_specificity("Kubernetes 파드 배포 절차 가이드")
        assert score > 0

    def test_calculate_query_specificity_empty(self):
        assert self.evaluator._calculate_query_specificity("") == 0.0
        assert self.evaluator._calculate_query_specificity("  ") == 0.0

    def test_calculate_source_freshness_empty(self):
        assert self.evaluator._calculate_source_freshness([]) == 0.0

    def test_calculate_source_freshness_no_dates(self):
        chunks = [{"metadata": {}}]
        assert self.evaluator._calculate_source_freshness(chunks) == 0.5

    def test_calculate_source_freshness_recent(self):
        recent = datetime.now(timezone.utc).isoformat()
        chunks = [{"metadata": {"updated_at": recent}}]
        score = self.evaluator._calculate_source_freshness(chunks)
        assert score > 0.9

    def test_estimate_expected_sources(self):
        assert self.evaluator._estimate_expected_sources("a b c") == 2
        assert self.evaluator._estimate_expected_sources("a b c d e f") == 3
        assert self.evaluator._estimate_expected_sources("a b c d e f g h i j k") == 4

    def test_search_time_penalty(self):
        assert self.evaluator._search_time_penalty(0) == 0.0
        assert self.evaluator._search_time_penalty(3000) == pytest.approx(1.0)
        assert 0 < self.evaluator._search_time_penalty(1500) < 1.0

    def test_parse_datetime_valid(self):
        dt = self.evaluator._parse_datetime("2024-01-01T00:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_datetime_invalid(self):
        assert self.evaluator._parse_datetime("not-a-date") is None

    def test_to_confidence_level(self):
        from src.search.crag_evaluator import ConfidenceLevel
        assert self.evaluator._to_confidence_level(0.95) == ConfidenceLevel.HIGH
        assert self.evaluator._to_confidence_level(0.1) == ConfidenceLevel.UNCERTAIN


class TestCRAGEnums:
    def test_retrieval_action_values(self):
        from src.search.crag_evaluator import RetrievalAction
        assert RetrievalAction.CORRECT.value == "correct"
        assert RetrievalAction.AMBIGUOUS.value == "ambiguous"
        assert RetrievalAction.INCORRECT.value == "incorrect"

    def test_confidence_level_values(self):
        from src.search.crag_evaluator import ConfidenceLevel
        assert ConfidenceLevel.HIGH.value == "high"

    def test_abstention_messages(self):
        from src.search.crag_evaluator import ABSTENTION_MESSAGES
        assert "no_knowledge" in ABSTENTION_MESSAGES
        assert "low_confidence" in ABSTENTION_MESSAGES


# ===========================================================================
# CrossEncoder
# ===========================================================================

class TestCrossEncoder:
    def test_sigmoid(self):
        from src.search.cross_encoder_reranker import _sigmoid
        assert 0 < _sigmoid(0) < 1
        assert _sigmoid(0, temperature=3.0) == pytest.approx(0.5, abs=0.01)
        assert _sigmoid(100) > 0.9
        assert _sigmoid(-100) < 0.1

    def test_rerank_no_model(self):
        from src.search.cross_encoder_reranker import rerank_with_cross_encoder
        import src.search.cross_encoder_reranker as mod

        original_model = mod._model
        mod._model = None
        chunks = [{"content": "test", "score": 0.5}]
        result = rerank_with_cross_encoder("query", chunks, top_k=5)
        assert result == chunks
        mod._model = original_model

    def test_rerank_empty_chunks(self):
        from src.search.cross_encoder_reranker import rerank_with_cross_encoder
        result = rerank_with_cross_encoder("query", [], top_k=5)
        assert result == []

    def test_warmup_already_attempted(self):
        from src.search.cross_encoder_reranker import warmup
        import src.search.cross_encoder_reranker as mod

        original = mod._load_attempted
        mod._load_attempted = True
        warmup()  # Should be a no-op
        mod._load_attempted = original


# ===========================================================================
# DenseTermIndex
# ===========================================================================

class TestDenseTermIndex:
    def test_is_ready_empty(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        assert idx.is_ready is False

    def test_build_provider_not_ready(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        provider.is_ready.return_value = False
        idx = DenseTermIndex(provider)
        idx.build([])
        assert idx.is_ready is False

    def test_build_no_terms(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        provider.is_ready.return_value = True
        idx = DenseTermIndex(provider)
        idx.build([])
        assert idx.is_ready is False

    def test_search_not_ready(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        result = idx.search("test query")
        assert result == []

    def test_build_and_search(self):
        import numpy as np
        from src.search.dense_term_index import DenseTermIndex

        provider = MagicMock()
        provider.is_ready.return_value = True
        provider.encode.return_value = {"dense_vecs": [[1.0] * 1024]}

        idx = DenseTermIndex(provider)

        # Create mock precomputed terms
        term1 = MagicMock()
        term1.term.term = "test"
        term1.term.term_ko = "테스트"
        term1.term.definition = "A test definition"
        idx.build([term1])

        assert idx.is_ready is True

        # Search
        provider.encode.return_value = {"dense_vecs": [[1.0] * 1024]}
        results = idx.search("test query", top_k=1)
        assert len(results) == 1
        assert results[0][1] > 0  # cosine score

    def test_search_batch_not_ready(self):
        from src.search.dense_term_index import DenseTermIndex
        provider = MagicMock()
        idx = DenseTermIndex(provider)
        results = idx.search_batch(["q1", "q2"])
        assert results == [[], []]


# ===========================================================================
# GraphSearchExpander
# ===========================================================================

class TestGraphSearchExpander:
    async def test_expand_empty_chunks(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)

        result = await expander.expand("query", [])
        assert result.graph_related_count == 0
        assert result.expanded_source_uris == set()

    async def test_expand_no_entities(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)

        # Single char words should be filtered
        result = await expander.expand("a b c", [{"source_uri": "u1"}])
        assert result.graph_related_count == 0

    async def test_expand_with_results(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        repo.find_related_chunks.return_value = {"http://new-doc"}
        expander = GraphSearchExpander(graph_repo=repo)

        chunks = [{"source_uri": "http://existing"}]
        result = await expander.expand("Kubernetes 설치", chunks, scope_kb_ids=["kb1"])
        assert result.graph_related_count >= 0

    def test_boost_chunks_no_uris(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo)

        chunks = [{"source_uri": "u1", "score": 0.5}]
        result = expander.boost_chunks(chunks, set())
        assert result == chunks

    def test_boost_chunks_with_match(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)

        chunks = [{"source_uri": "u1", "score": 0.5}]
        result = expander.boost_chunks(chunks, {"u1"}, graph_distances={"u1": 1})
        assert result[0]["score"] > 0.5
        assert result[0]["graph_boosted"] is True

    def test_boost_chunks_distance_based(self):
        from src.search.graph_expander import GraphSearchExpander
        repo = AsyncMock()
        expander = GraphSearchExpander(graph_repo=repo, graph_boost=0.1)

        chunks = [
            {"source_uri": "u1", "score": 0.5},
            {"source_uri": "u2", "score": 0.5},
            {"source_uri": "u3", "score": 0.5},
        ]
        distances = {"u1": 1, "u2": 2, "u3": 3}
        result = expander.boost_chunks(chunks, {"u1", "u2", "u3"}, graph_distances=distances)
        # Distance 1 should get highest boost
        assert result[0]["score"] > result[1]["score"]


class TestNoOpGraphSearchExpander:
    async def test_expand(self):
        from src.search.graph_expander import NoOpGraphSearchExpander
        expander = NoOpGraphSearchExpander()
        result = await expander.expand("query", [{"score": 0.5}])
        assert result.graph_related_count == 0

    def test_boost_chunks(self):
        from src.search.graph_expander import NoOpGraphSearchExpander
        expander = NoOpGraphSearchExpander()
        chunks = [{"score": 0.5}]
        assert expander.boost_chunks(chunks, {"u1"}) == chunks


class TestSplitCompoundWords:
    def test_korean_english_split(self):
        from src.search.graph_expander import _split_compound_words
        assert _split_compound_words("K8S담당자") == ["K8S", "담당자"]

    def test_slash_separated(self):
        from src.search.graph_expander import _split_compound_words
        result = _split_compound_words("CI/CD 파이프라인")
        assert "CI" in result
        assert "CD" in result
        assert "파이프라인" in result


# ===========================================================================
# AnswerService
# ===========================================================================

class TestAnswerService:
    async def test_enrich_chitchat(self):
        from src.search.answer_service import AnswerService
        svc = AnswerService(llm_client=None)
        result = await svc.enrich("안녕하세요", [], query_type_hint="chitchat")
        assert result.query_type == "chitchat"
        assert "안녕하세요" in result.answer

    async def test_enrich_no_chunks(self):
        from src.search.answer_service import AnswerService
        svc = AnswerService(llm_client=None)
        result = await svc.enrich("테스트 질문", [])
        assert result.confidence_indicator == "낮음"
        assert result.disclaimer is not None

    async def test_enrich_with_chunks_no_llm(self):
        from src.search.answer_service import AnswerService
        svc = AnswerService(llm_client=None)
        chunks = [
            {"content": "테스트 내용", "document_name": "doc1.pdf", "source_uri": "u1", "score": 0.8},
        ]
        result = await svc.enrich("테스트 질문", chunks)
        assert "1건" in result.answer
        assert result.citation_entries is not None

    async def test_enrich_with_llm(self):
        from src.search.answer_service import AnswerService
        llm = AsyncMock()
        llm.generate.return_value = "LLM 생성 답변입니다."
        svc = AnswerService(llm_client=llm)

        chunks = [
            {"content": "내용", "document_name": "doc.pdf", "source_uri": "u1", "score": 0.9},
        ]
        result = await svc.enrich("질문", chunks)
        assert result.answer == "LLM 생성 답변입니다."

    async def test_enrich_llm_failure(self):
        from src.search.answer_service import AnswerService
        llm = AsyncMock()
        llm.generate.side_effect = Exception("LLM error")
        svc = AnswerService(llm_client=llm)

        chunks = [{"content": "test", "document_name": "d", "source_uri": "u", "score": 0.5}]
        result = await svc.enrich("q", chunks)
        assert "오류" in result.answer

    async def test_enrich_analytical_disclaimer(self):
        from src.search.answer_service import AnswerService
        svc = AnswerService(llm_client=None)
        chunks = [{"content": "c", "document_name": "d", "source_uri": "u", "score": 0.5}]
        result = await svc.enrich("왜 이 시스템이 느린가요?", chunks, query_type_hint="analytical")
        assert result.disclaimer is not None and "추론" in result.disclaimer

    async def test_enrich_invalid_type_hint(self):
        from src.search.answer_service import AnswerService
        svc = AnswerService(llm_client=None)
        chunks = [{"content": "c", "document_name": "d", "source_uri": "u", "score": 0.5}]
        result = await svc.enrich("q", chunks, query_type_hint="invalid_type")
        # Should fall back to classifier
        assert result.query_type is not None
