"""Unit tests for src/search/transparency_formatter.py -- TransparencyFormatter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.search.query_classifier import QueryType
from src.search.tiered_response import TieredResponse
from src.search.transparency_formatter import (
    FormattedSection,
    NoOpTransparencyFormatter,
    SourceType,
    TransparencyFormatter,
    TransparentResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def formatter() -> TransparencyFormatter:
    return TransparencyFormatter()


def _make_response(
    content: str = "Test response content",
    query_type: QueryType = QueryType.FACTUAL,
    source_type: str = "document",
    citations: list[dict] | None = None,
    confidence: float = 0.85,
    disclaimer: str | None = None,
) -> TieredResponse:
    return TieredResponse(
        content=content,
        query_type=query_type,
        source_type=source_type,
        citations=citations or [],
        confidence=confidence,
        disclaimer=disclaimer,
    )


# ---------------------------------------------------------------------------
# SourceType enum
# ---------------------------------------------------------------------------

class TestSourceType:
    def test_values(self):
        assert SourceType.DOCUMENT == "document"
        assert SourceType.INFERENCE == "inference"
        assert SourceType.GENERAL == "general"

    def test_string_conversion(self):
        assert str(SourceType.DOCUMENT) == "document"


# ---------------------------------------------------------------------------
# FormattedSection dataclass
# ---------------------------------------------------------------------------

class TestFormattedSection:
    def test_creation(self):
        section = FormattedSection(
            content="test content",
            source_type=SourceType.DOCUMENT,
            citations=[],
        )
        assert section.content == "test content"
        assert section.source_type == SourceType.DOCUMENT


# ---------------------------------------------------------------------------
# TransparentResponse dataclass
# ---------------------------------------------------------------------------

class TestTransparentResponse:
    def test_creation(self):
        resp = TransparentResponse(
            formatted_content="formatted",
            sections=[],
            summary_label="label",
            citations_section=None,
            disclaimer=None,
            confidence_indicator="high",
        )
        assert resp.formatted_content == "formatted"
        assert resp.sections == []


# ---------------------------------------------------------------------------
# format
# ---------------------------------------------------------------------------

class TestFormat:
    def test_basic_format(self, formatter: TransparencyFormatter):
        response = _make_response()
        result = formatter.format(response)
        assert isinstance(result, TransparentResponse)
        assert result.formatted_content
        assert len(result.sections) >= 1

    def test_format_with_disclaimer(self, formatter: TransparencyFormatter):
        response = _make_response(disclaimer="This is a test disclaimer")
        result = formatter.format(response)
        assert result.disclaimer == "This is a test disclaimer"

    def test_format_high_confidence(self, formatter: TransparencyFormatter):
        response = _make_response(confidence=0.95)
        result = formatter.format(response)
        assert "높은 신뢰도" in result.confidence_indicator

    def test_format_medium_confidence(self, formatter: TransparencyFormatter):
        response = _make_response(confidence=0.75)
        result = formatter.format(response)
        assert "중간 신뢰도" in result.confidence_indicator

    def test_format_low_confidence(self, formatter: TransparencyFormatter):
        response = _make_response(confidence=0.55)
        result = formatter.format(response)
        assert "낮은 신뢰도" in result.confidence_indicator

    def test_format_uncertain_confidence(self, formatter: TransparencyFormatter):
        response = _make_response(confidence=0.1)
        result = formatter.format(response)
        assert "확인 필요" in result.confidence_indicator

    def test_format_with_citations(self, formatter: TransparencyFormatter):
        citations = [
            {
                "ref": "1",
                "document_name": "K8s Guide",
                "source_uri": "http://wiki.example.com/k8s",
                "relevance_score": 0.92,
            },
        ]
        response = _make_response(citations=citations)
        result = formatter.format(response)
        assert result.citations_section is not None

    def test_format_no_citations(self, formatter: TransparencyFormatter):
        response = _make_response(citations=[])
        result = formatter.format(response)
        assert result.citations_section is None

    def test_format_summary_label(self, formatter: TransparencyFormatter):
        response = _make_response(query_type=QueryType.FACTUAL, source_type="document")
        result = formatter.format(response)
        assert "사실 확인" in result.summary_label

    def test_format_analytical_label(self, formatter: TransparencyFormatter):
        response = _make_response(query_type=QueryType.ANALYTICAL, source_type="inference")
        result = formatter.format(response)
        assert "분석" in result.summary_label


# ---------------------------------------------------------------------------
# format_simple
# ---------------------------------------------------------------------------

class TestFormatSimple:
    def test_basic_simple(self, formatter: TransparencyFormatter):
        response = _make_response()
        result = formatter.format_simple(response)
        assert isinstance(result, str)
        assert "문서 기반" in result
        assert "Test response content" in result

    def test_simple_with_disclaimer(self, formatter: TransparencyFormatter):
        response = _make_response(disclaimer="주의사항")
        result = formatter.format_simple(response)
        assert "*주의사항*" in result

    def test_simple_with_citations(self, formatter: TransparencyFormatter):
        citations = [
            {
                "ref": "1",
                "document_name": "Doc",
                "source_uri": "http://x.com",
                "relevance_score": 0.9,
            },
        ]
        response = _make_response(citations=citations)
        result = formatter.format_simple(response)
        assert "---" in result

    def test_simple_inference_source(self, formatter: TransparencyFormatter):
        response = _make_response(source_type="inference")
        result = formatter.format_simple(response)
        assert "추론" in result

    def test_simple_general_source(self, formatter: TransparencyFormatter):
        response = _make_response(source_type="general")
        result = formatter.format_simple(response)
        assert "일반 지식" in result


# ---------------------------------------------------------------------------
# _split_into_sections
# ---------------------------------------------------------------------------

class TestSplitIntoSections:
    def test_no_section_markers(self, formatter: TransparencyFormatter):
        sections = formatter._split_into_sections("Plain text without markers.")
        assert len(sections) == 1
        assert sections[0].source_type == SourceType.DOCUMENT

    def test_with_document_marker(self, formatter: TransparencyFormatter):
        content = "[문서 기반] First part\nSecond line\n[분석] Analysis here"
        sections = formatter._split_into_sections(content)
        assert len(sections) == 2
        assert sections[0].source_type == SourceType.DOCUMENT
        assert sections[1].source_type == SourceType.INFERENCE

    def test_with_general_marker(self, formatter: TransparencyFormatter):
        content = "[문서 기반] Doc content\n[권장 사항] Recommendation"
        sections = formatter._split_into_sections(content)
        assert len(sections) == 2
        assert sections[1].source_type == SourceType.GENERAL

    def test_multiple_same_type_markers(self, formatter: TransparencyFormatter):
        content = "[문서 기반] First\nMore text\nEven more"
        sections = formatter._split_into_sections(content)
        # All same type, so single section
        assert len(sections) == 1


# ---------------------------------------------------------------------------
# _format_sections
# ---------------------------------------------------------------------------

class TestFormatSections:
    def test_single_section(self, formatter: TransparencyFormatter):
        sections = [FormattedSection("Content", SourceType.DOCUMENT, [])]
        result = formatter._format_sections(sections, "document")
        assert "문서 기반" in result
        assert "Content" in result

    def test_multiple_sections(self, formatter: TransparencyFormatter):
        sections = [
            FormattedSection("Doc content", SourceType.DOCUMENT, []),
            FormattedSection("Analysis", SourceType.INFERENCE, []),
        ]
        result = formatter._format_sections(sections, "document")
        assert "문서 기반" in result
        assert "추론" in result
        assert "Doc content" in result
        assert "Analysis" in result


# ---------------------------------------------------------------------------
# _format_citations
# ---------------------------------------------------------------------------

class TestFormatCitations:
    def test_no_citations(self, formatter: TransparencyFormatter):
        result = formatter._format_citations([])
        assert result is None

    def test_with_citations(self, formatter: TransparencyFormatter):
        citations = [
            {
                "ref": "1",
                "document_name": "Guide",
                "source_uri": "http://x.com",
                "relevance_score": 0.9,
            },
        ]
        result = formatter._format_citations(citations)
        assert result is not None
        assert "Guide" in result

    def test_with_freshness_warnings(self, formatter: TransparencyFormatter):
        citations = [
            {
                "ref": "1",
                "document_name": "Old Doc",
                "source_uri": "http://x.com",
                "relevance_score": 0.7,
                "freshness_warning": "이 문서는 180일 전에 업데이트되었습니다",
            },
        ]
        result = formatter._format_citations(citations)
        assert result is not None
        assert "최신성" in result
        assert "180일" in result


# ---------------------------------------------------------------------------
# _get_confidence_level
# ---------------------------------------------------------------------------

class TestGetConfidenceLevel:
    def test_high(self, formatter: TransparencyFormatter):
        assert formatter._get_confidence_level(0.95) == "high"

    def test_medium(self, formatter: TransparencyFormatter):
        assert formatter._get_confidence_level(0.75) == "medium"

    def test_low(self, formatter: TransparencyFormatter):
        assert formatter._get_confidence_level(0.55) == "low"

    def test_uncertain(self, formatter: TransparencyFormatter):
        assert formatter._get_confidence_level(0.1) == "uncertain"


# ---------------------------------------------------------------------------
# _generate_summary_label
# ---------------------------------------------------------------------------

class TestGenerateSummaryLabel:
    def test_factual_document(self, formatter: TransparencyFormatter):
        response = _make_response(query_type=QueryType.FACTUAL, source_type="document")
        label = formatter._generate_summary_label(response)
        assert "사실 확인" in label
        assert "문서 기반" in label

    def test_analytical_inference(self, formatter: TransparencyFormatter):
        response = _make_response(query_type=QueryType.ANALYTICAL, source_type="inference")
        label = formatter._generate_summary_label(response)
        assert "분석" in label

    def test_advisory_general(self, formatter: TransparencyFormatter):
        response = _make_response(query_type=QueryType.ADVISORY, source_type="general")
        label = formatter._generate_summary_label(response)
        assert "조언" in label


# ---------------------------------------------------------------------------
# NoOpTransparencyFormatter
# ---------------------------------------------------------------------------

class TestNoOpTransparencyFormatter:
    def test_format(self):
        formatter = NoOpTransparencyFormatter()
        response = _make_response()
        result = formatter.format(response)
        assert isinstance(result, TransparentResponse)
        assert "[NoOp]" in result.formatted_content
        assert result.sections == []
        assert result.summary_label == "[NoOp]"
        assert result.disclaimer == "[NoOp] 테스트 모드"

    def test_format_simple(self):
        formatter = NoOpTransparencyFormatter()
        response = _make_response()
        result = formatter.format_simple(response)
        assert "[NoOp]" in result
        assert "Test response content" in result
