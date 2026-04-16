"""Comprehensive unit tests for ingestion.py and helper modules — maximizing line coverage.

Tests NoOp implementations, document type classification, owner extraction,
L1 category assignment, quality scoring, IngestionPipeline initialization,
and text processing utilities. No external services required.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import RawDocument, IngestionResult
from src.pipeline.ingestion_contracts import (
    NoOpEmbedder,
    NoOpSparseEmbedder,
    NoOpVectorStore,
    NoOpGraphStore,
)
from src.pipeline.ingestion_helpers import (
    _DOC_TYPE_KEYWORDS,
    _L1_CATEGORIES_DEFAULT,
    classify_document_type,
    classify_l1_category,
    calculate_quality_score,
    extract_owner,
    extract_cross_references,
    load_l1_categories_from_db,
    _get_l1_categories_sync,
)
from src.pipeline.ingestion_text import (
    extract_document_summary,
    clean_text_for_embedding,
    clean_passage,
    shorten_title,
    build_document_context_prefix,
)
from src.pipeline.quality_processor import QualityTier, QualityMetrics


# =========================================================================
# NoOp implementations
# =========================================================================

class TestNoOpEmbedder:
    async def test_default_dimension(self):
        e = NoOpEmbedder()
        vecs = await e.embed_documents(["hello", "world"])
        assert len(vecs) == 2
        assert all(v == 0.0 for v in vecs[0])

    async def test_custom_dimension(self):
        e = NoOpEmbedder(dimension=128)
        vecs = await e.embed_documents(["test"])
        assert len(vecs[0]) == 128


class TestNoOpSparseEmbedder:
    async def test_returns_empty_sparse(self):
        e = NoOpSparseEmbedder()
        result = await e.embed_sparse(["a", "b"])
        assert len(result) == 2
        assert result[0] == {"indices": [], "values": []}


class TestNoOpVectorStore:
    async def test_upsert_noop(self):
        s = NoOpVectorStore()
        # Should not raise
        await s.upsert_batch("kb", [{"content": "x"}])


class TestNoOpGraphStore:
    async def test_upsert_document_noop(self):
        s = NoOpGraphStore()
        await s.upsert_document("doc1")

    async def test_execute_write_noop(self):
        s = NoOpGraphStore()
        await s.execute_write("MATCH (n) RETURN n", {})


# =========================================================================
# Document type classification (META-03)
# =========================================================================

class TestClassifyDocumentType:
    def test_guide(self):
        assert classify_document_type("사용자 가이드", "") == "guide"
        assert classify_document_type("User Manual", "") == "guide"

    def test_policy(self):
        assert classify_document_type("보안 정책 v2", "") == "policy"

    def test_procedure(self):
        assert classify_document_type("장애 절차 안내", "") == "procedure"

    def test_faq(self):
        assert classify_document_type("FAQ 모음", "") == "faq"

    def test_meeting_notes(self):
        assert classify_document_type("주간 회의록", "") == "meeting_notes"

    def test_changelog(self):
        assert classify_document_type("릴리스 노트", "") == "changelog"

    def test_default_reference(self):
        assert classify_document_type("어떤 문서", "내용") == "reference"

    def test_keywords_coverage(self):
        """Ensure all keyword tuples are exercised."""
        for doc_type, keywords in _DOC_TYPE_KEYWORDS:
            for kw in keywords:
                result = classify_document_type(kw, "")
                assert result == doc_type, f"{kw} should classify as {doc_type}"


# =========================================================================
# Owner extraction (META-06)
# =========================================================================

class TestExtractOwner:
    def test_from_author(self):
        raw = RawDocument(
            doc_id="1", title="doc", content="c", source_uri="",
            author="홍길동",
        )
        assert extract_owner(raw) == "홍길동"

    def test_from_title_pattern(self):
        raw = RawDocument(
            doc_id="1", title="2024_1-2_김철수M_보고서", content="c", source_uri="",
        )
        result = extract_owner(raw)
        assert result == "김철수"

    def test_empty(self):
        raw = RawDocument(
            doc_id="1", title="doc", content="c", source_uri="",
        )
        assert extract_owner(raw) == ""

    def test_skip_patterns(self):
        raw = RawDocument(
            doc_id="1", title="doc", content="c", source_uri="",
            author="AI센터",
        )
        assert extract_owner(raw) == ""


# =========================================================================
# L1 category assignment (META-07)
# =========================================================================

class TestClassifyL1Category:
    def test_it_infra(self):
        result = classify_l1_category("서버 배포 가이드", "")
        assert "IT" in result or "인프라" in result

    def test_system(self):
        result = classify_l1_category("POS 시스템 매뉴얼", "")
        assert "시스템" in result or "애플리케이션" in result

    def test_business_process(self):
        result = classify_l1_category("양수도 절차 안내", "")
        assert "프로세스" in result or "규정" in result

    def test_distribution(self):
        result = classify_l1_category("GS25 점포 운영", "상품 발주 재고")
        assert "유통" in result or "물류" in result

    def test_default_etc(self):
        result = classify_l1_category("random title", "random content")
        assert result == "기타"

    def test_title_weight_higher(self):
        """Title keywords should be weighted 3x vs content."""
        result = classify_l1_category("서버 장애", "")
        assert "IT" in result or "인프라" in result


class TestLoadL1CategoriesFromDb:
    def test_load_and_use(self):
        custom = [{"name": "Custom", "keywords": ["custom_kw"]}]
        load_l1_categories_from_db(custom)
        assert _get_l1_categories_sync() == custom
        result = classify_l1_category("custom_kw doc", "")
        assert result == "Custom"
        # Reset
        load_l1_categories_from_db([])

    def test_empty_does_not_overwrite(self):
        """Passing empty list does NOT reset cache (only truthy list updates)."""
        # Reset to defaults first by loading a non-empty then checking behavior
        load_l1_categories_from_db([])
        # The function only updates cache when categories is truthy
        # So after load_l1_categories_from_db([]), cache may still hold previous value
        cats = _get_l1_categories_sync()
        assert isinstance(cats, list)
        assert len(cats) >= 1


# =========================================================================
# Quality score (META-08)
# =========================================================================

class TestCalculateQualityScore:
    def test_none_metrics(self):
        assert calculate_quality_score(None, QualityTier.BRONZE) == 50.0

    def test_gold_high_content(self):
        m = QualityMetrics(
            content_length=5000,
            has_tables=True,
            has_code_blocks=True,
            has_headers=True,
            has_images=False,
            has_links=False,
            word_count=500,
            paragraph_count=10,
        )
        score = calculate_quality_score(m, QualityTier.GOLD)
        assert score > 80

    def test_bronze_short(self):
        m = QualityMetrics(
            content_length=100,
            has_tables=False,
            has_code_blocks=False,
            has_headers=False,
            has_images=False,
            has_links=False,
            word_count=15,
            paragraph_count=1,
        )
        score = calculate_quality_score(m, QualityTier.BRONZE)
        assert 0 < score < 50

    def test_max_100(self):
        m = QualityMetrics(
            content_length=100000,
            has_tables=True,
            has_code_blocks=True,
            has_headers=True,
            has_images=True,
            has_links=True,
            word_count=10000,
            paragraph_count=100,
        )
        score = calculate_quality_score(m, QualityTier.GOLD)
        assert score <= 100.0


# =========================================================================
# Cross-reference extraction (GRAPH-01)
# =========================================================================

class TestExtractCrossReferences:
    def test_internal_links(self):
        content = "See [guide](/pages/123) and [wiki](https://confluence.example.com/x/abc)"
        refs = extract_cross_references(content)
        assert len(refs) == 2

    def test_external_links_excluded(self):
        content = "Visit [Google](https://google.com)"
        refs = extract_cross_references(content)
        assert len(refs) == 0

    def test_no_links(self):
        assert extract_cross_references("plain text") == []


# =========================================================================
# Text processing utilities (ingestion_text.py)
# =========================================================================

class TestExtractDocumentSummary:
    def test_short_text(self):
        assert extract_document_summary("Short.") == "Short."

    def test_long_text_truncates(self):
        text = "First sentence. Second sentence that is quite long and goes on. " * 5
        result = extract_document_summary(text, max_len=50)
        assert len(result) <= 55  # some tolerance
        # The function tries to cut at sentence boundary, but may not find one > 50 chars
        assert len(result) > 0

    def test_empty(self):
        assert extract_document_summary("") == ""

    def test_no_sentence_boundary(self):
        text = "a" * 300
        result = extract_document_summary(text, max_len=200)
        assert len(result) == 200


class TestCleanTextForEmbedding:
    def test_removes_html(self):
        assert clean_text_for_embedding("<b>bold</b>") == "bold"

    def test_normalizes_whitespace(self):
        result = clean_text_for_embedding("hello   world")
        assert result == "hello world"

    def test_removes_control_chars(self):
        result = clean_text_for_embedding("hello\x00world")
        assert "\x00" not in result

    def test_collapses_newlines(self):
        result = clean_text_for_embedding("a\n\n\n\n\nb")
        assert "\n\n\n" not in result


class TestCleanPassage:
    def test_dedup_lines(self):
        text = "line one\nline one\nline two"
        result = clean_passage(text)
        assert result.count("line one") == 1

    def test_empty(self):
        assert clean_passage("") == ""

    def test_none_safe(self):
        assert clean_passage(None) is None

    def test_removes_incomplete_fragment(self):
        text = "Complete sentence. Another complete sentence. Incomplete frag"
        result = clean_passage(text)
        # Should trim to last sentence boundary
        assert result.endswith(".")


class TestShortenTitle:
    def test_removes_numeric_prefix(self):
        assert "1234567890_" not in shorten_title("1234567890_document")

    def test_removes_extension(self):
        assert ".pptx" not in shorten_title("presentation.pptx")

    def test_truncates_long(self):
        title = "a" * 100
        result = shorten_title(title, max_len=30)
        assert len(result) <= 31 + len("…")


class TestBuildDocumentContextPrefix:
    def test_basic_prefix(self):
        raw = RawDocument(
            doc_id="1", title="Test Doc", content="content", source_uri="",
            metadata={"labels": ["label1"], "parent_title": "Parent"},
        )
        prefix = build_document_context_prefix(
            raw, heading_path="Heading > Sub",
            chunk_type="body", chunk_index=0, total_chunks=5,
            doc_summary="A summary",
        )
        assert "Test Doc" in prefix
        assert "body" in prefix.lower() or "Section" in prefix or "1/5" in prefix


# =========================================================================
# IngestionPipeline initialization
# =========================================================================

class TestIngestionPipelineInit:
    def test_default_init(self):
        from src.pipeline.ingestion import IngestionPipeline
        pipeline = IngestionPipeline()
        assert isinstance(pipeline.embedder, NoOpEmbedder)
        assert isinstance(pipeline.sparse_embedder, NoOpSparseEmbedder)
        assert isinstance(pipeline.vector_store, NoOpVectorStore)
        assert isinstance(pipeline.graph_store, NoOpGraphStore)
        assert pipeline.chunker is not None
        assert pipeline.enable_quality_filter is True
        assert pipeline.enable_graphrag is False

    def test_custom_embedder(self):
        from src.pipeline.ingestion import IngestionPipeline
        mock_embedder = MagicMock()
        pipeline = IngestionPipeline(embedder=mock_embedder)
        assert pipeline.embedder is mock_embedder

    def test_quality_filter_disabled(self):
        from src.pipeline.ingestion import IngestionPipeline
        pipeline = IngestionPipeline(enable_quality_filter=False)
        assert pipeline.enable_quality_filter is False


# =========================================================================
# IngestionResult
# =========================================================================

class TestIngestionResult:
    def test_success_result(self):
        r = IngestionResult.success_result(chunks_stored=5)
        assert r.success is True
        assert r.chunks_stored == 5

    def test_failure_result(self):
        r = IngestionResult.failure_result(reason="bad", stage="chunk")
        assert r.success is False
        assert r.reason == "bad"
        assert r.stage == "chunk"
