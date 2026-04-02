"""Unit tests for src/search/rag_pipeline.py -- KnowledgeRAGPipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.search.rag_pipeline import (
    KnowledgeRAGPipeline,
    QueryIntent,
    RAGRequest,
    RAGResponse,
)
from src.vectordb.client import QdrantSearchResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@dataclass
class FakePreprocessResult:
    corrected_query: str
    was_corrected: bool


class FakePreprocessor:
    def __init__(self, correction: str | None = None):
        self._correction = correction

    def preprocess(self, query: str) -> FakePreprocessResult:
        if self._correction:
            return FakePreprocessResult(self._correction, True)
        return FakePreprocessResult(query, False)


class FakeExpandResult:
    def __init__(self, expanded: str):
        self.expanded_query = expanded


class FakeExpander:
    def __init__(self, expansion: str | None = None):
        self._expansion = expansion

    async def expand_query(self, *, kb_id: str, query: str) -> FakeExpandResult:
        return FakeExpandResult(self._expansion or query)


def _make_search_result(pid: str, score: float, content: str) -> QdrantSearchResult:
    return QdrantSearchResult(
        point_id=pid,
        score=score,
        content=content,
        metadata={"document_name": f"doc_{pid}", "source_uri": f"http://x/{pid}", "kb_id": "test"},
    )


def _make_embedder(colbert: bool = False):
    """Create a mock embedder. Always includes colbert_vecs since config defaults to enabled."""
    def encode(texts, dense, sparse, colbert_flag):
        result = {
            "dense_vecs": [[0.1] * 1024],
            "lexical_weights": [{1: 0.5, 2: 0.3}],
        }
        if colbert_flag:
            result["colbert_vecs"] = [[[0.1] * 128]]
        return result
    m = MagicMock()
    m.encode = encode
    return m


def _make_search_mock(results: list[QdrantSearchResult] | None = None):
    """Create a mock search engine with both search and search_with_colbert_rerank."""
    search = AsyncMock()
    search.search = AsyncMock(return_value=results or [])
    search.search_with_colbert_rerank = AsyncMock(return_value=results or [])
    return search


# ---------------------------------------------------------------------------
# RAGRequest / RAGResponse
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_rag_request_defaults(self):
        req = RAGRequest(query="test")
        assert req.kb_id is None
        assert req.stream is False

    def test_rag_response_to_dict(self):
        resp = RAGResponse(
            query="q", answer="a", query_type=QueryIntent.GENERAL, confidence=0.9,
        )
        d = resp.to_dict()
        assert d["query"] == "q"
        assert d["query_type"] == "general"
        assert d["confidence"] == 0.9


# ---------------------------------------------------------------------------
# _classify_query
# ---------------------------------------------------------------------------

class TestClassifyQuery:
    @pytest.fixture()
    def pipeline(self) -> KnowledgeRAGPipeline:
        return KnowledgeRAGPipeline()

    def test_owner_query(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("서버 담당자 누구인가요?") == QueryIntent.OWNER_QUERY

    def test_procedure_query(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("배포 절차 알려주세요") == QueryIntent.PROCEDURE

    def test_troubleshoot_query(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("에러 발생 원인은?") == QueryIntent.TROUBLESHOOT

    def test_concept_query(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("GraphRAG 개념이 뭐야?") == QueryIntent.CONCEPT

    def test_general_query(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("오늘 날씨 어때?") == QueryIntent.GENERAL

    def test_english_owner_pattern(self, pipeline: KnowledgeRAGPipeline):
        assert pipeline._classify_query("who is responsible for infra?") == QueryIntent.OWNER_QUERY

    def test_procedure_keywords(self, pipeline: KnowledgeRAGPipeline):
        for kw in ["방법", "순서", "가이드", "매뉴얼"]:
            assert pipeline._classify_query(f"이 {kw}은?") == QueryIntent.PROCEDURE

    def test_troubleshoot_keywords(self, pipeline: KnowledgeRAGPipeline):
        for kw in ["오류", "문제", "안됨", "실패"]:
            assert pipeline._classify_query(f"이 {kw}은?") == QueryIntent.TROUBLESHOOT


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

class TestProcess:
    @pytest.mark.asyncio
    async def test_process_no_search_engine(self):
        pipeline = KnowledgeRAGPipeline(
            embedder=_make_embedder(),
        )
        resp = await pipeline.process(RAGRequest(query="test"))
        assert "검색 엔진이 초기화되지 않았습니다" in resp.answer

    @pytest.mark.asyncio
    async def test_process_no_embedder(self):
        pipeline = KnowledgeRAGPipeline(
            search_engine=AsyncMock(),
        )
        resp = await pipeline.process(RAGRequest(query="test"))
        assert "임베딩 프로바이더가 초기화되지 않았습니다" in resp.answer

    @pytest.mark.asyncio
    async def test_process_no_results(self):
        search = _make_search_mock([])
        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
        )
        resp = await pipeline.process(RAGRequest(query="nothing found"))
        assert "찾을 수 없습니다" in resp.answer

    @pytest.mark.asyncio
    async def test_process_with_results_and_llm(self):
        results = [_make_search_result("p1", 0.9, "result content")]
        search = _make_search_mock(results)
        llm = AsyncMock()
        llm.generate_with_context = AsyncMock(return_value="Generated answer")

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            llm_client=llm,
            embedder=_make_embedder(),
        )
        resp = await pipeline.process(RAGRequest(query="test question", kb_id="test"))
        assert resp.answer == "Generated answer"
        assert len(resp.sources) == 1
        assert resp.metadata["search_count"] == 1

    @pytest.mark.asyncio
    async def test_process_without_llm(self):
        results = [_make_search_result("p1", 0.8, "some content")]
        search = _make_search_mock(results)

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
        )
        resp = await pipeline.process(RAGRequest(query="test"))
        assert "검색 결과" in resp.answer
        assert "some content" in resp.answer

    @pytest.mark.asyncio
    async def test_process_with_preprocessor(self):
        search = _make_search_mock([])
        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
            query_preprocessor=FakePreprocessor(correction="corrected query"),
        )
        resp = await pipeline.process(RAGRequest(query="original"))
        # Should have called either search or search_with_colbert_rerank
        total_calls = search.search.await_count + search.search_with_colbert_rerank.await_count
        assert total_calls == 1

    @pytest.mark.asyncio
    async def test_process_preprocessor_failure(self):
        search = _make_search_mock([])
        pp = MagicMock()
        pp.preprocess.side_effect = Exception("preprocessor error")

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
            query_preprocessor=pp,
        )
        resp = await pipeline.process(RAGRequest(query="test"))
        # Should still work with original query
        assert "찾을 수 없습니다" in resp.answer

    @pytest.mark.asyncio
    async def test_process_with_query_expander(self):
        search = _make_search_mock([])
        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
            query_expander=FakeExpander("expanded query"),
        )
        resp = await pipeline.process(RAGRequest(query="test", kb_id="kb1"))
        total_calls = search.search.await_count + search.search_with_colbert_rerank.await_count
        assert total_calls == 1

    @pytest.mark.asyncio
    async def test_process_with_colbert_reranking(self):
        """ColBERT reranking is enabled by default in config."""
        results = [_make_search_result("p1", 0.9, "colbert result")]
        search = _make_search_mock(results)
        llm = AsyncMock()
        llm.generate_with_context = AsyncMock(return_value="answer")

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            llm_client=llm,
            embedder=_make_embedder(colbert=True),
        )
        resp = await pipeline.process(RAGRequest(query="test", kb_id="test"))
        search.search_with_colbert_rerank.assert_awaited_once()


# ---------------------------------------------------------------------------
# _handle_owner_query
# ---------------------------------------------------------------------------

class TestHandleOwnerQuery:
    @pytest.mark.asyncio
    async def test_owner_query_with_results(self):
        graph = AsyncMock()
        graph.execute_query = AsyncMock(return_value=[
            {"person": "홍길동", "role": "RESPONSIBLE_FOR", "topic": "인프라"},
        ])

        pipeline = KnowledgeRAGPipeline(graph_client=graph)
        resp = await pipeline._handle_owner_query(
            RAGRequest(query="인프라 담당자 누구?"),
            "인프라 담당자",
        )
        assert "홍길동" in resp.answer
        assert resp.confidence == 1.0
        assert resp.query_type == QueryIntent.OWNER_QUERY

    @pytest.mark.asyncio
    async def test_owner_query_no_results(self):
        graph = AsyncMock()
        graph.execute_query = AsyncMock(return_value=[])

        pipeline = KnowledgeRAGPipeline(graph_client=graph)
        resp = await pipeline._handle_owner_query(
            RAGRequest(query="담당자?"),
            "없는시스템",
        )
        assert "찾을 수 없습니다" in resp.answer
        assert resp.confidence == 0.0

    @pytest.mark.asyncio
    async def test_owner_query_graph_error(self):
        graph = AsyncMock()
        graph.execute_query = AsyncMock(side_effect=Exception("neo4j down"))

        pipeline = KnowledgeRAGPipeline(graph_client=graph)
        resp = await pipeline._handle_owner_query(
            RAGRequest(query="담당자?"),
            "test",
        )
        assert "오류" in resp.answer


# ---------------------------------------------------------------------------
# process routes owner query to graph
# ---------------------------------------------------------------------------

class TestOwnerQueryRouting:
    @pytest.mark.asyncio
    async def test_owner_query_routes_to_graph(self):
        graph = AsyncMock()
        graph.execute_query = AsyncMock(return_value=[
            {"person": "김철수", "role": "OWNS", "topic": "서버"},
        ])

        pipeline = KnowledgeRAGPipeline(
            graph_client=graph,
            embedder=_make_embedder(),
        )
        resp = await pipeline.process(RAGRequest(query="서버 담당자 누구?"))
        assert resp.query_type == QueryIntent.OWNER_QUERY
        assert "김철수" in resp.answer

    @pytest.mark.asyncio
    async def test_owner_query_without_graph_falls_through(self):
        search = _make_search_mock([])

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
            # No graph_client
        )
        resp = await pipeline.process(RAGRequest(query="담당자 알려줘"))
        # Without graph, falls through to vector search
        assert "찾을 수 없습니다" in resp.answer


# ---------------------------------------------------------------------------
# _calculate_confidence
# ---------------------------------------------------------------------------

class TestCalculateConfidence:
    def test_empty_results(self):
        assert KnowledgeRAGPipeline._calculate_confidence([]) == 0.0

    def test_high_score(self):
        results = [{"score": 0.95}]
        conf = KnowledgeRAGPipeline._calculate_confidence(results)
        assert conf == 0.9

    def test_low_score(self):
        results = [{"score": 0.1}]
        conf = KnowledgeRAGPipeline._calculate_confidence(results)
        assert conf == 0.3


# ---------------------------------------------------------------------------
# process_stream
# ---------------------------------------------------------------------------

class TestProcessStream:
    @pytest.mark.asyncio
    async def test_stream_no_search(self):
        pipeline = KnowledgeRAGPipeline(embedder=_make_embedder())
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="test")):
            tokens.append(token)
        assert any("초기화" in t for t in tokens)

    @pytest.mark.asyncio
    async def test_stream_no_embedder(self):
        pipeline = KnowledgeRAGPipeline(search_engine=AsyncMock())
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="test")):
            tokens.append(token)
        assert any("임베딩" in t for t in tokens)

    @pytest.mark.asyncio
    async def test_stream_no_results(self):
        search = _make_search_mock([])

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            embedder=_make_embedder(),
        )
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="nothing")):
            tokens.append(token)
        assert any("찾을 수 없습니다" in t for t in tokens)

    @pytest.mark.asyncio
    async def test_stream_with_llm_stream(self):
        results = [_make_search_result("p1", 0.9, "content")]
        search = _make_search_mock(results)

        async def fake_stream(query, context):
            yield "Hello "
            yield "World"

        llm = AsyncMock()
        llm.generate_stream = fake_stream

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            llm_client=llm,
            embedder=_make_embedder(),
        )
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="test")):
            tokens.append(token)
        assert tokens == ["Hello ", "World"]

    @pytest.mark.asyncio
    async def test_stream_owner_query(self):
        graph = AsyncMock()
        graph.execute_query = AsyncMock(return_value=[
            {"person": "A", "role": "OWNS", "topic": "B"},
        ])

        pipeline = KnowledgeRAGPipeline(
            graph_client=graph,
            embedder=_make_embedder(),
        )
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="담당자 누구?")):
            tokens.append(token)
        assert len(tokens) == 1
        assert "A" in tokens[0]

    @pytest.mark.asyncio
    async def test_stream_fallback_to_process(self):
        """When LLM has no generate_stream, falls back to process()."""
        results = [_make_search_result("p1", 0.9, "content")]
        search = _make_search_mock(results)
        llm = AsyncMock()
        llm.generate_with_context = AsyncMock(return_value="non-stream answer")
        # No generate_stream attribute
        del llm.generate_stream

        pipeline = KnowledgeRAGPipeline(
            search_engine=search,
            llm_client=llm,
            embedder=_make_embedder(),
        )
        tokens = []
        async for token in pipeline.process_stream(RAGRequest(query="test")):
            tokens.append(token)
        assert any("non-stream answer" in t for t in tokens)


# ---------------------------------------------------------------------------
# QueryIntent enum
# ---------------------------------------------------------------------------

class TestQueryIntent:
    def test_values(self):
        assert QueryIntent.OWNER_QUERY == "owner_query"
        assert QueryIntent.PROCEDURE == "procedure"
        assert QueryIntent.TROUBLESHOOT == "troubleshoot"
        assert QueryIntent.CONCEPT == "concept"
        assert QueryIntent.GENERAL == "general"
