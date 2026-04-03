"""Unit tests for src/search/tiered_response.py -- TieredResponseGenerator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.search.query_classifier import QueryType
from src.search.tiered_response import (
    ILLMClient,
    NoOpTieredResponseGenerator,
    RAGContext,
    TieredResponse,
    TieredResponseGenerator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_llm() -> AsyncMock:
    llm = AsyncMock(spec=ILLMClient)
    llm.generate = AsyncMock(return_value="LLM response with [1] citation")
    return llm


@pytest.fixture()
def generator(mock_llm: AsyncMock) -> TieredResponseGenerator:
    return TieredResponseGenerator(mock_llm)


def _make_context(
    query: str = "테스트 질문",
    chunks: list[str] | None = None,
    sources: list[dict] | None = None,
    scores: list[float] | None = None,
    glossary: list[dict] | None = None,
    graph_facts: list[str] | None = None,
) -> RAGContext:
    return RAGContext(
        query=query,
        retrieved_chunks=chunks or ["chunk1 content", "chunk2 content"],
        chunk_sources=sources or [
            {"document_name": "Doc A", "source_uri": "http://a.com", "score": 0.9},
            {"document_name": "Doc B", "source_uri": "http://b.com", "score": 0.8},
        ],
        relevance_scores=scores or [0.9, 0.8],
        glossary_definitions=glossary or [],
        graph_facts=graph_facts or [],
    )


# ---------------------------------------------------------------------------
# RAGContext
# ---------------------------------------------------------------------------

class TestRAGContext:
    def test_has_relevant_context_with_scores(self):
        ctx = _make_context(scores=[0.9])
        assert ctx.has_relevant_context() is True

    def test_has_relevant_context_low_scores(self):
        ctx = _make_context(scores=[0.01])
        assert ctx.has_relevant_context() is False

    def test_has_relevant_context_no_scores(self):
        ctx = _make_context(scores=[])
        assert ctx.has_relevant_context() is True  # has chunks

    def test_has_relevant_context_empty(self):
        ctx = RAGContext(query="q", retrieved_chunks=[], relevance_scores=[])
        assert ctx.has_relevant_context() is False

    def test_has_relevant_context_graph_facts(self):
        ctx = RAGContext(
            query="q", retrieved_chunks=[], relevance_scores=[],
            graph_facts=["fact1"],
        )
        assert ctx.has_relevant_context() is True

    def test_custom_threshold(self):
        ctx = _make_context(scores=[0.5])
        assert ctx.has_relevant_context(threshold=0.9) is False
        assert ctx.has_relevant_context(threshold=0.3) is True


# ---------------------------------------------------------------------------
# TieredResponse dataclass
# ---------------------------------------------------------------------------

class TestTieredResponseDataclass:
    def test_basic_creation(self):
        resp = TieredResponse(
            content="answer",
            query_type=QueryType.FACTUAL,
            source_type="document",
            citations=[],
            confidence=0.9,
        )
        assert resp.content == "answer"
        assert resp.disclaimer is None
        assert resp.follow_up_suggestions == []


# ---------------------------------------------------------------------------
# generate -- routing by query type
# ---------------------------------------------------------------------------

class TestGenerateRouting:
    async def test_chitchat(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(return_value="안녕하세요! 무엇을 도와드릴까요?")
        ctx = _make_context(query="안녕하세요")

        resp = await generator.generate(QueryType.CHITCHAT, ctx)
        assert resp.query_type == QueryType.CHITCHAT
        assert resp.source_type == "general"
        assert resp.confidence == 1.0

    async def test_factual(self, generator: TieredResponseGenerator, mock_llm):
        ctx = _make_context()
        resp = await generator.generate(QueryType.FACTUAL, ctx)
        assert resp.query_type == QueryType.FACTUAL
        assert resp.source_type == "document"
        mock_llm.generate.assert_awaited_once()

    async def test_analytical(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(return_value="[분석] 추론 내용 [1]")
        ctx = _make_context()
        resp = await generator.generate(QueryType.ANALYTICAL, ctx)
        assert resp.query_type == QueryType.ANALYTICAL
        assert resp.source_type == "inference"
        assert resp.disclaimer == TieredResponseGenerator.INFERENCE_DISCLAIMER

    async def test_analytical_no_inference_tag(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(return_value="문서 기반 답변 [1]")
        ctx = _make_context()
        resp = await generator.generate(QueryType.ANALYTICAL, ctx)
        assert resp.source_type == "document"
        assert resp.disclaimer is None

    async def test_advisory(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(return_value="[권장 사항] 이렇게 하세요 [1]")
        ctx = _make_context()
        resp = await generator.generate(QueryType.ADVISORY, ctx)
        assert resp.query_type == QueryType.ADVISORY
        assert resp.source_type == "general"
        assert resp.disclaimer == TieredResponseGenerator.GENERAL_KNOWLEDGE_DISCLAIMER
        assert len(resp.follow_up_suggestions) > 0

    async def test_unknown_treated_as_factual(self, generator: TieredResponseGenerator, mock_llm):
        ctx = _make_context()
        resp = await generator.generate(QueryType.UNKNOWN, ctx)
        assert resp.query_type == QueryType.FACTUAL


# ---------------------------------------------------------------------------
# Factual -- no relevant context
# ---------------------------------------------------------------------------

class TestFactualNoContext:
    async def test_no_relevant_context(self, generator: TieredResponseGenerator, mock_llm):
        ctx = _make_context(scores=[0.01, 0.02])
        resp = await generator.generate(QueryType.FACTUAL, ctx)
        assert "찾을 수 없습니다" in resp.content
        assert resp.confidence == 1.0
        assert len(resp.follow_up_suggestions) > 0
        mock_llm.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    async def test_factual_llm_error(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(side_effect=Exception("LLM failed"))
        ctx = _make_context()
        resp = await generator.generate(QueryType.FACTUAL, ctx)
        assert "오류" in resp.content
        assert resp.confidence == 0.0
        assert resp.disclaimer == TieredResponseGenerator.ERROR_DISCLAIMER

    async def test_analytical_llm_error(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(side_effect=Exception("LLM failed"))
        ctx = _make_context()
        resp = await generator.generate(QueryType.ANALYTICAL, ctx)
        assert "오류" in resp.content

    async def test_advisory_llm_error(self, generator: TieredResponseGenerator, mock_llm):
        mock_llm.generate = AsyncMock(side_effect=Exception("LLM failed"))
        ctx = _make_context()
        resp = await generator.generate(QueryType.ADVISORY, ctx)
        assert "오류" in resp.content


# ---------------------------------------------------------------------------
# _format_glossary_section
# ---------------------------------------------------------------------------

class TestFormatGlossarySection:
    def test_no_glossary(self):
        ctx = _make_context(glossary=[])
        assert TieredResponseGenerator._format_glossary_section(ctx) == ""

    def test_with_glossary(self):
        ctx = _make_context(glossary=[
            {"term": "K8s", "term_ko": "쿠버네티스", "definition": "container orchestration"},
        ])
        section = TieredResponseGenerator._format_glossary_section(ctx)
        assert "K8s (쿠버네티스)" in section
        assert "container orchestration" in section

    def test_glossary_without_korean(self):
        ctx = _make_context(glossary=[
            {"term": "CI/CD", "definition": "continuous integration"},
        ])
        section = TieredResponseGenerator._format_glossary_section(ctx)
        assert "CI/CD:" in section


# ---------------------------------------------------------------------------
# _format_graph_facts_section
# ---------------------------------------------------------------------------

class TestFormatGraphFactsSection:
    def test_no_graph_facts(self):
        ctx = _make_context(graph_facts=[])
        assert TieredResponseGenerator._format_graph_facts_section(ctx) == ""

    def test_with_graph_facts(self):
        ctx = _make_context(graph_facts=["홍길동 -> 인프라팀 관리"])
        section = TieredResponseGenerator._format_graph_facts_section(ctx)
        assert "홍길동" in section
        assert "지식 그래프" in section


# ---------------------------------------------------------------------------
# _format_context
# ---------------------------------------------------------------------------

class TestFormatContext:
    def test_with_sources(self, generator: TieredResponseGenerator):
        ctx = _make_context()
        formatted = generator._format_context(ctx)
        assert "[1] Doc A" in formatted
        assert "[2] Doc B" in formatted

    def test_with_metadata(self, generator: TieredResponseGenerator):
        ctx = _make_context(
            sources=[{
                "document_name": "Doc",
                "metadata": {
                    "creator_name": "김철수",
                    "creator_email": "kim@gs.com",
                    "creator_team": "인프라팀",
                },
            }]
        )
        formatted = generator._format_context(ctx)
        assert "김철수" in formatted
        assert "kim@gs.com" in formatted
        assert "인프라팀" in formatted


# ---------------------------------------------------------------------------
# _extract_citations
# ---------------------------------------------------------------------------

class TestExtractCitations:
    def test_extract_citations(self, generator: TieredResponseGenerator):
        ctx = _make_context()
        response_text = "According to [1], something. Also see [2]."
        citations = generator._extract_citations(response_text, ctx)
        assert len(citations) == 2
        assert citations[0]["ref"] == "1"
        assert citations[1]["ref"] == "2"

    def test_no_citations(self, generator: TieredResponseGenerator):
        ctx = _make_context()
        citations = generator._extract_citations("No references here", ctx)
        assert citations == []

    def test_out_of_range_citation_ignored(self, generator: TieredResponseGenerator):
        ctx = _make_context()
        citations = generator._extract_citations("See [99] for details", ctx)
        assert citations == []


# ---------------------------------------------------------------------------
# _calculate_confidence
# ---------------------------------------------------------------------------

class TestCalculateConfidence:
    def test_no_citations(self, generator: TieredResponseGenerator):
        ctx = _make_context()
        assert generator._calculate_confidence([], ctx) == 0.5

    def test_with_citations(self, generator: TieredResponseGenerator):
        ctx = _make_context(scores=[0.9, 0.8])
        citations = [{"ref": "1"}, {"ref": "2"}, {"ref": "3"}]
        conf = generator._calculate_confidence(citations, ctx)
        assert 0.5 <= conf <= 1.0

    def test_no_relevance_scores(self, generator: TieredResponseGenerator):
        ctx = _make_context(scores=[])
        citations = [{"ref": "1"}]
        conf = generator._calculate_confidence(citations, ctx)
        # default relevance = 0.7, citation_score = 1/3
        assert 0.3 <= conf <= 0.7


# ---------------------------------------------------------------------------
# NoOpTieredResponseGenerator
# ---------------------------------------------------------------------------

class TestNoOpGenerator:
    async def test_noop_generate(self):
        gen = NoOpTieredResponseGenerator()
        ctx = _make_context()
        resp = await gen.generate(QueryType.FACTUAL, ctx)
        assert "[NoOp]" in resp.content
        assert resp.query_type == QueryType.FACTUAL
        assert resp.confidence == 1.0
        assert "[NoOp]" in (resp.disclaimer or "")

    async def test_noop_all_query_types(self):
        gen = NoOpTieredResponseGenerator()
        ctx = _make_context()
        for qt in [QueryType.FACTUAL, QueryType.ANALYTICAL, QueryType.ADVISORY, QueryType.CHITCHAT]:
            resp = await gen.generate(qt, ctx)
            assert qt.value in resp.content
