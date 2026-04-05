"""Unit tests for src/search/citation_formatter.py."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from src.search.citation_formatter import (
    CitationEntry,
    CitationFormatter,
    _build_citation_entry,
    _safe_float,
    _safe_int,
)


# ---------------------------------------------------------------------------
# _safe_float / _safe_int helpers
# ---------------------------------------------------------------------------


class TestSafeConversions:
    def test_safe_float_valid(self):
        assert _safe_float(3.14) == 3.14

    def test_safe_float_int(self):
        assert _safe_float(5) == 5.0

    def test_safe_float_string(self):
        assert _safe_float("0.75") == 0.75

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_invalid(self):
        assert _safe_float("abc") is None

    def test_safe_int_valid(self):
        assert _safe_int(42) == 42

    def test_safe_int_float(self):
        assert _safe_int(3.7) == 3

    def test_safe_int_none(self):
        assert _safe_int(None) is None

    def test_safe_int_invalid(self):
        assert _safe_int("xyz") is None


# ---------------------------------------------------------------------------
# CitationEntry dataclass
# ---------------------------------------------------------------------------


class TestCitationEntry:
    def test_frozen(self):
        entry = CitationEntry(index=1, ref="1", document_name="Doc A")
        with pytest.raises(AttributeError):
            entry.index = 2  # type: ignore[misc]

    def test_defaults(self):
        entry = CitationEntry(index=1, ref="1", document_name="Doc A")
        assert entry.kb_name is None
        assert entry.source_uri is None
        assert entry.relevance_score is None
        assert entry.is_stale is False
        assert entry.freshness_warning is None

    def test_all_fields(self):
        entry = CitationEntry(
            index=2,
            ref="2",
            document_name="Guide",
            kb_name="itops",
            source_uri="https://example.com",
            relevance_score=0.95,
            is_stale=True,
            freshness_warning="90일 이상 경과",
            days_since_update=120,
            updated_at="2025-01-01",
        )
        assert entry.is_stale is True
        assert entry.days_since_update == 120


# ---------------------------------------------------------------------------
# _build_citation_entry
# ---------------------------------------------------------------------------


class TestBuildCitationEntry:
    def test_basic_citation(self):
        citation = {"ref": "A", "document_name": "운영 가이드", "score": 0.88}
        entry = _build_citation_entry(1, citation)
        assert entry.ref == "A"
        assert entry.document_name == "운영 가이드"
        assert entry.relevance_score == 0.88

    def test_missing_ref_falls_back_to_index(self):
        entry = _build_citation_entry(3, {})
        assert entry.ref == "3"

    def test_missing_document_name_falls_back(self):
        entry = _build_citation_entry(5, {})
        assert entry.document_name == "문서 5"

    def test_relevance_score_from_relevance_score_key(self):
        citation = {"relevance_score": 0.77}
        entry = _build_citation_entry(1, citation)
        assert entry.relevance_score == 0.77

    def test_url_used_as_source_uri(self):
        citation = {"url": "https://wiki.example.com/page/1"}
        entry = _build_citation_entry(1, citation)
        assert entry.source_uri == "https://wiki.example.com/page/1"

    def test_stale_flag(self):
        citation = {"is_stale": True, "freshness_warning": "오래됨"}
        entry = _build_citation_entry(1, citation)
        assert entry.is_stale is True
        assert entry.freshness_warning == "오래됨"


# ---------------------------------------------------------------------------
# CitationFormatter.from_response_citations
# ---------------------------------------------------------------------------


class TestFromResponseCitations:
    def test_empty_list(self):
        entries = CitationFormatter.from_response_citations([])
        assert entries == []

    def test_indexes_start_at_one(self):
        citations = [{"document_name": "A"}, {"document_name": "B"}]
        entries = CitationFormatter.from_response_citations(citations)
        assert entries[0].index == 1
        assert entries[1].index == 2

    def test_preserves_fields(self):
        citations = [
            {"ref": "X", "document_name": "Guide", "score": 0.9, "kb_name": "kb1"}
        ]
        entries = CitationFormatter.from_response_citations(citations)
        assert entries[0].ref == "X"
        assert entries[0].kb_name == "kb1"


# ---------------------------------------------------------------------------
# CitationFormatter.from_sources (dict sources)
# ---------------------------------------------------------------------------


class TestFromSourcesDict:
    def test_dict_source(self):
        sources = [
            {
                "document_name": "K8s 가이드",
                "relevance_score": 0.92,
                "source_uri": "https://example.com/k8s",
            }
        ]
        entries = CitationFormatter.from_sources(sources)
        assert len(entries) == 1
        assert entries[0].document_name == "K8s 가이드"
        assert entries[0].relevance_score == 0.92
        assert entries[0].source_uri == "https://example.com/k8s"

    def test_dict_source_title_fallback(self):
        sources = [{"title": "Backup Guide"}]
        entries = CitationFormatter.from_sources(sources)
        assert entries[0].document_name == "Backup Guide"

    def test_dict_source_url_fallback(self):
        sources = [{"url": "https://example.com"}]
        entries = CitationFormatter.from_sources(sources)
        assert entries[0].source_uri == "https://example.com"


# ---------------------------------------------------------------------------
# CitationFormatter.from_sources (object sources)
# ---------------------------------------------------------------------------


class TestFromSourcesObject:
    def test_object_source(self):
        src = SimpleNamespace(
            document_name="Server Guide",
            relevance_score=0.88,
            source_uri="https://example.com/server",
            ref="S1",
            kb_name=None,
            is_stale=False,
            freshness_warning=None,
            days_since_update=None,
            updated_at=None,
            score=None,
            relevance=None,
            title=None,
            source_id=None,
            url=None,
        )
        entries = CitationFormatter.from_sources([src])
        assert entries[0].document_name == "Server Guide"
        assert entries[0].relevance_score == 0.88

    def test_object_with_score_fallback(self):
        src = SimpleNamespace(
            document_name=None,
            relevance_score=None,
            score=0.65,
            relevance=None,
            source_uri=None,
            url=None,
            ref=None,
            kb_name=None,
            is_stale=False,
            freshness_warning=None,
            days_since_update=None,
            updated_at=None,
            title=None,
            source_id=None,
        )
        entries = CitationFormatter.from_sources([src])
        assert entries[0].relevance_score == 0.65


# ---------------------------------------------------------------------------
# CitationFormatter.format_markdown
# ---------------------------------------------------------------------------


class TestFormatMarkdown:
    def test_empty_entries_returns_none(self):
        assert CitationFormatter.format_markdown([]) is None

    def test_single_entry_with_uri(self):
        entry = CitationEntry(
            index=1, ref="1", document_name="Guide", source_uri="https://example.com"
        )
        md = CitationFormatter.format_markdown([entry])
        assert md is not None
        assert "[1] [Guide](https://example.com)" in md

    def test_single_entry_without_uri(self):
        entry = CitationEntry(index=1, ref="1", document_name="Guide")
        md = CitationFormatter.format_markdown([entry])
        assert md is not None
        assert "[1] Guide" in md

    def test_relevance_score_displayed(self):
        entry = CitationEntry(
            index=1, ref="1", document_name="Doc", relevance_score=0.92
        )
        md = CitationFormatter.format_markdown([entry])
        assert "(score=0.92)" in md

    def test_no_heading(self):
        entry = CitationEntry(index=1, ref="1", document_name="Doc")
        md = CitationFormatter.format_markdown([entry], include_heading=False)
        assert CitationFormatter.DEFAULT_HEADING not in md

    def test_custom_heading(self):
        entry = CitationEntry(index=1, ref="1", document_name="Doc")
        md = CitationFormatter.format_markdown([entry], heading="## References")
        assert "## References" in md

    def test_multiple_entries(self):
        entries = [
            CitationEntry(index=1, ref="1", document_name="A"),
            CitationEntry(index=2, ref="2", document_name="B"),
        ]
        md = CitationFormatter.format_markdown(entries)
        assert "[1] A" in md
        assert "[2] B" in md

    def test_default_heading_present(self):
        entry = CitationEntry(index=1, ref="1", document_name="Doc")
        md = CitationFormatter.format_markdown([entry])
        assert CitationFormatter.DEFAULT_HEADING in md
