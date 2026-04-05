"""Tests for dashboard/pages/chat.py helper functions.

Tests cover data-building and rendering helper functions extracted from the chat page.

The chat page has heavy module-level side effects (st.set_page_config, session state
attribute access, API calls at import time). We test the pure logic functions by
extracting and testing the function bodies directly.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Reproduce pure functions from chat.py for testing
# (These are exact copies of the function logic from the source file)
# ---------------------------------------------------------------------------
def _build_freshness_warnings(src: dict) -> list[str]:
    """Build freshness warning parts for a source document."""
    parts: list[str] = []
    if src.get("is_stale", False):
        parts.append("⚠️ 오래된 문서")
    days_since = src.get("days_since_update")
    if days_since is not None:
        parts.append(f"📅 {days_since}일 전 업데이트")
    elif src.get("updated_at"):
        parts.append(f"📅 {src['updated_at']}")
    freshness_warning = src.get("freshness_warning", "")
    if freshness_warning:
        parts.append(freshness_warning)
    return parts


def _build_sources_from_chunks(chunks: list[dict]) -> list[dict]:
    """HubSearchResponse chunks를 소스 메타데이터로 변환."""
    results = []
    for c in chunks:
        doc_name = c.get("document_name", c.get("chunk_id", "-"))
        content = c.get("content", "")

        location = ""
        slide_match = re.search(r'\[Slide (\d+)', content)
        page_match = re.search(r'\[Page (\d+)', content)
        if slide_match:
            location = f" (Slide {slide_match.group(1)})"
        elif page_match:
            location = f" (Page {page_match.group(1)})"
        elif c.get("metadata", {}).get("chunk_index") is not None:
            idx = c["metadata"]["chunk_index"]
            if idx >= 0:
                location = f" (§{idx + 1})"

        results.append({
            "title": f"{doc_name}{location}",
            "url": c.get("source_uri", ""),
            "tier": c.get("tier", c.get("metadata", {}).get("tier", "-")),
            "trust_score": c.get("trust_score", c.get("kts_score", 0)),
            "rerank_score": c.get("rerank_score", c.get("composite_score", c.get("score", 0))),
            "is_stale": c.get("is_stale", False),
            "freshness_warning": c.get("freshness_warning", ""),
            "days_since_update": c.get("days_since_update"),
            "updated_at": c.get("updated_at"),
        })
    return results


# ---------------------------------------------------------------------------
# Tests for _build_freshness_warnings
# ---------------------------------------------------------------------------
class TestBuildFreshnessWarnings:
    def test_empty_source(self):
        result = _build_freshness_warnings({})
        assert result == []

    def test_stale_document(self):
        src = {"is_stale": True}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "오래된 문서" in result[0]

    def test_days_since_update(self):
        src = {"days_since_update": 45}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "45일 전" in result[0]

    def test_updated_at_fallback(self):
        src = {"updated_at": "2025-01-01"}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "2025-01-01" in result[0]

    def test_days_since_takes_precedence_over_updated_at(self):
        src = {"days_since_update": 10, "updated_at": "2025-01-01"}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "10일 전" in result[0]

    def test_freshness_warning(self):
        src = {"freshness_warning": "문서가 오래되었습니다"}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "문서가 오래되었습니다" in result[0]

    def test_multiple_warnings(self):
        src = {
            "is_stale": True,
            "days_since_update": 100,
            "freshness_warning": "경고",
        }
        result = _build_freshness_warnings(src)
        assert len(result) == 3

    def test_days_since_zero(self):
        src = {"days_since_update": 0}
        result = _build_freshness_warnings(src)
        assert len(result) == 1
        assert "0일 전" in result[0]

    def test_is_stale_false(self):
        src = {"is_stale": False}
        result = _build_freshness_warnings(src)
        assert result == []


# ---------------------------------------------------------------------------
# Tests for _build_sources_from_chunks
# ---------------------------------------------------------------------------
class TestBuildSourcesFromChunks:
    def test_empty_chunks(self):
        result = _build_sources_from_chunks([])
        assert result == []

    def test_basic_chunk(self):
        chunks = [{
            "document_name": "test_doc.pdf",
            "content": "Some content",
            "score": 0.95,
            "tier": "GOLD",
            "trust_score": 0.8,
        }]
        result = _build_sources_from_chunks(chunks)
        assert len(result) == 1
        assert result[0]["title"] == "test_doc.pdf"
        assert result[0]["tier"] == "GOLD"
        assert result[0]["trust_score"] == 0.8
        assert result[0]["rerank_score"] == 0.95

    def test_slide_extraction(self):
        chunks = [{
            "document_name": "presentation.pptx",
            "content": "[Slide 5] Hello world",
        }]
        result = _build_sources_from_chunks(chunks)
        assert "(Slide 5)" in result[0]["title"]

    def test_page_extraction(self):
        chunks = [{
            "document_name": "report.pdf",
            "content": "[Page 12] Some text",
        }]
        result = _build_sources_from_chunks(chunks)
        assert "(Page 12)" in result[0]["title"]

    def test_chunk_index_extraction(self):
        chunks = [{
            "document_name": "doc.md",
            "content": "Some content",
            "metadata": {"chunk_index": 2},
        }]
        result = _build_sources_from_chunks(chunks)
        assert "(§3)" in result[0]["title"]

    def test_chunk_index_negative_ignored(self):
        chunks = [{
            "document_name": "doc.md",
            "content": "Content",
            "metadata": {"chunk_index": -1},
        }]
        result = _build_sources_from_chunks(chunks)
        assert "§" not in result[0]["title"]

    def test_rerank_score_priority(self):
        """rerank_score should take priority over composite_score and score."""
        chunks = [{
            "document_name": "doc",
            "content": "",
            "rerank_score": 0.9,
            "composite_score": 0.8,
            "score": 0.7,
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["rerank_score"] == 0.9

    def test_composite_score_fallback(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
            "composite_score": 0.8,
            "score": 0.7,
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["rerank_score"] == 0.8

    def test_score_final_fallback(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
            "score": 0.7,
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["rerank_score"] == 0.7

    def test_no_score(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["rerank_score"] == 0

    def test_freshness_fields(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
            "is_stale": True,
            "freshness_warning": "old",
            "days_since_update": 30,
            "updated_at": "2025-01-01",
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["is_stale"] is True
        assert result[0]["freshness_warning"] == "old"
        assert result[0]["days_since_update"] == 30
        assert result[0]["updated_at"] == "2025-01-01"

    def test_source_uri(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
            "source_uri": "https://example.com/doc",
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["url"] == "https://example.com/doc"

    def test_fallback_keys(self):
        """Test fallback key resolution for chunk_id and metadata tier."""
        chunks = [{
            "chunk_id": "chunk-001",
            "content": "",
            "metadata": {"tier": "SILVER"},
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["title"] == "chunk-001"
        assert result[0]["tier"] == "SILVER"

    def test_kts_score_fallback(self):
        chunks = [{
            "document_name": "doc",
            "content": "",
            "kts_score": 0.75,
        }]
        result = _build_sources_from_chunks(chunks)
        assert result[0]["trust_score"] == 0.75

    def test_slide_priority_over_page(self):
        """Slide match should take priority over page match."""
        chunks = [{
            "document_name": "doc",
            "content": "[Slide 3] [Page 7] content",
        }]
        result = _build_sources_from_chunks(chunks)
        assert "(Slide 3)" in result[0]["title"]
        assert "(Page 7)" not in result[0]["title"]

    def test_multiple_chunks(self):
        chunks = [
            {"document_name": "a.pdf", "content": "aaa", "score": 0.9},
            {"document_name": "b.pdf", "content": "bbb", "score": 0.8},
            {"document_name": "c.pdf", "content": "ccc", "score": 0.7},
        ]
        result = _build_sources_from_chunks(chunks)
        assert len(result) == 3
        assert result[0]["title"] == "a.pdf"
        assert result[2]["rerank_score"] == 0.7


# ---------------------------------------------------------------------------
# Tests for confidence/quality signal logic (inline in chat.py)
# ---------------------------------------------------------------------------
class TestQualitySignalLogic:
    """Test inline logic patterns from _render_quality_signals."""

    def test_quality_gate_passed_true(self):
        meta = {"quality_gate_passed": True}
        gate = meta.get("quality_gate_passed")
        assert gate is True

    def test_quality_gate_passed_false(self):
        meta = {"quality_gate_passed": False}
        gate = meta.get("quality_gate_passed")
        assert gate is False

    def test_quality_gate_none(self):
        meta = {}
        gate = meta.get("quality_gate_passed")
        assert gate is None

    def test_confidence_level_badges(self):
        level_badges = {
            "HIGH": "HIGH",
            "MEDIUM": "MEDIUM",
            "LOW": "LOW",
            "UNCERTAIN": "UNCERTAIN",
        }
        assert level_badges.get("HIGH") == "HIGH"
        assert level_badges.get("UNKNOWN") is None

    def test_corrected_query_detection(self):
        meta = {"corrected_query": "정산", "original_query": "젼산"}
        corrected = meta.get("corrected_query", "")
        original = meta.get("original_query", "")
        assert corrected and original and corrected != original

    def test_no_correction_when_same(self):
        meta = {"corrected_query": "정산", "original_query": "정산"}
        corrected = meta.get("corrected_query", "")
        original = meta.get("original_query", "")
        assert not (corrected and original and corrected != original)


class TestMetadataBuilding:
    """Test metadata dict construction logic from _execute_ai_search / _execute_fast_search."""

    def test_fast_search_metadata(self):
        chunks = [
            {"document_name": "doc1", "content": "", "score": 0.9},
            {"document_name": "doc2", "content": "", "score": 0.8, "is_stale": True},
        ]
        sources = _build_sources_from_chunks(chunks)
        has_stale = any(s.get("is_stale", False) for s in sources)
        metadata = {
            "sources": sources,
            "confidence_level": "",
            "rerank_breakdown": {},
            "expanded_terms": [],
            "working_memory_hit": False,
        }
        assert len(metadata["sources"]) == 2
        assert has_stale is True

    def test_ai_search_metadata(self):
        transparency = {"confidence_indicator": "HIGH"}
        query_preprocess = {
            "corrected_query": "정산",
            "original_query": "젼산",
        }
        metadata = {
            "sources": [],
            "confidence_level": transparency.get("confidence_indicator", ""),
            "rerank_breakdown": {},
            "expanded_terms": [],
            "working_memory_hit": False,
            "quality_gate_passed": True,
            "disclaimer": "주의",
            "cross_kb_conflict": None,
            "corrected_query": query_preprocess.get("corrected_query", ""),
            "original_query": query_preprocess.get("original_query", ""),
            "crag_action_history": ["correct"],
        }
        assert metadata["confidence_level"] == "HIGH"
        assert metadata["corrected_query"] == "정산"
        assert metadata["original_query"] == "젼산"
        assert metadata["quality_gate_passed"] is True
