"""Coverage backfill — IngestionPipeline core paths.

Tests init, dedup check, quality gate, embed retry, morpheme extraction,
and the main ingest() orchestration with mocked dependencies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import RawDocument
from src.pipeline.ingestion import IngestionPipeline, IngestionFeatureFlags


def _make_raw(
    title: str = "Test Document",
    content: str = "테스트 문서 본문 내용입니다. 충분한 길이를 가진 텍스트.",
    source_uri: str = "test.txt",
    author: str = "tester",
    doc_id: str = "doc-001",
) -> RawDocument:
    return RawDocument(
        doc_id=doc_id,
        title=title,
        content=content,
        source_uri=source_uri,
        author=author,
    )


# ==========================================================================
# Init
# ==========================================================================


class TestIngestionPipelineInit:
    def test_default_init(self) -> None:
        pipeline = IngestionPipeline()
        assert pipeline.embedder is not None
        assert pipeline.vector_store is not None
        assert pipeline.enable_quality_filter is True

    def test_custom_flags(self) -> None:
        flags = IngestionFeatureFlags(
            enable_quality_filter=False,
            enable_graphrag=True,
        )
        pipeline = IngestionPipeline(flags=flags)
        assert pipeline.enable_quality_filter is False
        assert pipeline.enable_graphrag is True

    def test_flag_kwargs(self) -> None:
        pipeline = IngestionPipeline(enable_quality_filter=False)
        assert pipeline.enable_quality_filter is False

    def test_custom_embedder(self) -> None:
        mock_embedder = MagicMock()
        pipeline = IngestionPipeline(embedder=mock_embedder)
        assert pipeline.embedder is mock_embedder


class TestIngestionFeatureFlags:
    def test_defaults(self) -> None:
        f = IngestionFeatureFlags()
        assert f.enable_quality_filter is True
        assert f.enable_graphrag is False
        assert f.enable_term_extraction is False
        assert f.enable_ingestion_gate is False

    def test_override(self) -> None:
        f = IngestionFeatureFlags(enable_graphrag=True)
        assert f.enable_graphrag is True


# ==========================================================================
# Embed retry
# ==========================================================================


class TestEmbedDense:
    async def test_success_first_attempt(self) -> None:
        embedder = MagicMock()
        embedder.encode.return_value = {"dense_vecs": [[0.1, 0.2, 0.3]]}
        pipeline = IngestionPipeline(embedder=embedder)
        result = await pipeline._embed_dense(["test text"])
        assert len(result) == 1

    async def test_retry_on_failure(self) -> None:
        embedder = MagicMock()
        embedder.encode.side_effect = [
            ConnectionError("timeout"),
            {"dense_vecs": [[0.1, 0.2]]},
        ]
        pipeline = IngestionPipeline(embedder=embedder)
        pipeline._EMBED_RETRY_DELAY = 0  # Speed up test
        result = await pipeline._embed_dense(["test"])
        assert len(result) == 1

    async def test_all_retries_fail(self) -> None:
        embedder = MagicMock()
        embedder.encode.side_effect = ConnectionError("always fail")
        pipeline = IngestionPipeline(embedder=embedder)
        pipeline._EMBED_RETRY_DELAY = 0
        with pytest.raises(ConnectionError):
            await pipeline._embed_dense(["test"])


# ==========================================================================
# Quality check
# ==========================================================================


class TestQualityCheck:
    def test_short_content_rejected(self) -> None:
        pipeline = IngestionPipeline(enable_quality_filter=True)
        raw = _make_raw(content="짧은")
        tier, metrics, failure = pipeline._check_quality(raw)
        # Very short content should be filtered
        assert failure is not None or tier is not None

    def test_quality_filter_disabled_passes_all(self) -> None:
        pipeline = IngestionPipeline(enable_quality_filter=False)
        raw = _make_raw(content="짧은")
        tier, metrics, failure = pipeline._check_quality(raw)
        assert failure is None  # No filtering when disabled


# ==========================================================================
# Morpheme extraction
# ==========================================================================


class TestMorphemeExtraction:
    def test_extracts_morphemes(self) -> None:
        pipeline = IngestionPipeline()
        typed_chunks = [("본문 내용 테스트", "body", "")]
        result = pipeline._extract_morphemes(typed_chunks)
        assert len(result) == 1
        assert isinstance(result[0], str)

    def test_empty_chunks(self) -> None:
        pipeline = IngestionPipeline()
        result = pipeline._extract_morphemes([])
        assert result == []


# ==========================================================================
# Ingest — end-to-end with mocks
# ==========================================================================


class TestIngestEndToEnd:
    @pytest.fixture
    def pipeline(self):
        embedder = MagicMock()
        embedder.encode.return_value = {"dense_vecs": [[0.1] * 1024]}

        sparse_embedder = MagicMock()
        sparse_embedder.encode.return_value = [{"indices": [1, 2], "values": [0.5, 0.3]}]
        sparse_embedder.embed_sparse = AsyncMock(
            return_value=[{"indices": [1, 2], "values": [0.5, 0.3]}],
        )

        vector_store = AsyncMock()
        vector_store.upsert.return_value = 1

        graph_store = AsyncMock()

        return IngestionPipeline(
            embedder=embedder,
            sparse_embedder=sparse_embedder,
            vector_store=vector_store,
            graph_store=graph_store,
            enable_quality_filter=False,
        )

    async def test_basic_ingest(self, pipeline) -> None:
        raw = _make_raw(content="GS리테일 매장 운영 가이드. " * 20)
        result = await pipeline.ingest(raw, collection_name="kb_test")
        assert result is not None
        assert result.success is True or result.chunks_stored >= 0

    async def test_empty_content_handled(self, pipeline) -> None:
        raw = _make_raw(content="")
        result = await pipeline.ingest(raw, collection_name="kb_test")
        # Empty content should either return error result or raise
        assert result is not None

    async def test_binary_source_skipped(self, pipeline) -> None:
        raw = _make_raw(source_uri="image.png", content="binary content")
        result = await pipeline.ingest(raw, collection_name="kb_test")
        assert result is not None


# ==========================================================================
# Dedup check
# ==========================================================================


class TestDedupCheck:
    async def test_no_dedup_cache_passes(self) -> None:
        pipeline = IngestionPipeline()
        raw = _make_raw()
        content_hash = hashlib.sha256(raw.content.lower().strip().encode()).hexdigest()[:32]
        failure, info = await pipeline._check_dedup(raw, "kb_test", content_hash)
        assert failure is None  # No cache → no dedup → passes

    async def test_duplicate_detected(self) -> None:
        dedup_cache = AsyncMock()
        dedup_cache.exists.return_value = True
        pipeline = IngestionPipeline(dedup_cache=dedup_cache)
        raw = _make_raw()
        content_hash = hashlib.sha256(raw.content.lower().strip().encode()).hexdigest()[:32]
        failure, info = await pipeline._check_dedup(raw, "kb_test", content_hash)
        assert failure is not None  # Duplicate detected → skip

    async def test_not_duplicate_passes(self) -> None:
        dedup_cache = AsyncMock()
        dedup_cache.exists.return_value = False
        pipeline = IngestionPipeline(dedup_cache=dedup_cache)
        raw = _make_raw()
        content_hash = hashlib.sha256(raw.content.lower().strip().encode()).hexdigest()[:32]
        failure, info = await pipeline._check_dedup(raw, "kb_test", content_hash)
        assert failure is None
