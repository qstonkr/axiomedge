"""Extended unit tests for src/pipeline/ingestion.py — 279 uncovered lines."""

from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import RawDocument
from src.pipeline.document_parser import ParseResult
from src.pipeline.ingestion import (
    IngestionPipeline,
    NoOpEmbedder,
    NoOpGraphStore,
    NoOpSparseEmbedder,
    NoOpVectorStore,
)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_raw(doc_id="doc1", title="Test Doc", content="Some content here for testing", **kwargs):
    return RawDocument(
        doc_id=doc_id,
        title=title,
        content=content,
        source_uri="http://example.com/doc",
        author="tester",
        metadata=kwargs.get("metadata", {}),
        **{k: v for k, v in kwargs.items() if k != "metadata"},
    )


# ---------------------------------------------------------------------------
# NoOp classes
# ---------------------------------------------------------------------------

class TestNoOps:
    def test_noop_embedder(self):
        e = NoOpEmbedder()
        result = _run(e.embed_documents(["hello"]))
        assert result == [[0.0] * 1024]

    def test_noop_sparse_embedder(self):
        s = NoOpSparseEmbedder()
        result = _run(s.embed_sparse(["hello"]))
        assert len(result) == 1
        assert isinstance(result[0], dict)

    def test_noop_vector_store(self):
        v = NoOpVectorStore()
        _run(v.upsert_batch("coll", []))

    def test_noop_graph_store(self):
        g = NoOpGraphStore()
        # NoOp returns None for all ops
        _run(g.upsert_document(doc_id="d1", title="t"))
        _run(g.execute_write("query", {}))


# ---------------------------------------------------------------------------
# IngestionPipeline init
# ---------------------------------------------------------------------------

class TestPipelineInit:
    def test_default_init(self):
        p = IngestionPipeline()
        assert isinstance(p.embedder, NoOpEmbedder)
        assert isinstance(p.sparse_embedder, NoOpSparseEmbedder)
        assert isinstance(p.vector_store, NoOpVectorStore)
        assert isinstance(p.graph_store, NoOpGraphStore)

    def test_custom_services(self):
        embedder = MagicMock()
        store = MagicMock()
        p = IngestionPipeline(embedder=embedder, vector_store=store)
        assert p.embedder is embedder
        assert p.vector_store is store


# ---------------------------------------------------------------------------
# _embed_dense with retry
# ---------------------------------------------------------------------------

class TestEmbedDense:
    def test_embed_dense_via_encode(self):
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"dense_vecs": [[0.1] * 1024]})
        p = IngestionPipeline(embedder=embedder)

        result = _run(p._embed_dense(["hello"]))
        assert len(result) == 1
        assert len(result[0]) == 1024

    def test_embed_dense_via_embed_documents(self):
        embedder = AsyncMock()
        embedder.encode = None  # No encode method
        del embedder.encode  # Force AttributeError on getattr
        embedder.embed_documents = AsyncMock(return_value=[[0.2] * 1024])
        p = IngestionPipeline(embedder=embedder)

        result = _run(p._embed_dense(["hello"]))
        assert len(result) == 1

    def test_embed_dense_retry(self):
        embedder = MagicMock()
        call_count = 0

        def _fail_then_succeed(texts, return_dense=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("timeout")
            return {"dense_vecs": [[0.1] * 1024]}

        embedder.encode = _fail_then_succeed
        p = IngestionPipeline(embedder=embedder)
        p._EMBED_RETRY_DELAY = 0  # no delay in tests

        result = _run(p._embed_dense(["hello"]))
        assert len(result) == 1
        assert call_count == 2


# ---------------------------------------------------------------------------
# Full ingest flow with mocks
# ---------------------------------------------------------------------------

class TestIngestFlow:
    def _make_pipeline(self):
        embedder = MagicMock()
        # Return vectors matching the number of input texts
        def _encode(texts, return_dense=True, **kwargs):
            return {"dense_vecs": [[0.1] * 1024] * len(texts)}
        embedder.encode = MagicMock(side_effect=_encode)

        sparse_embedder = AsyncMock()
        async def _sparse(texts):
            return [{"indices": [1, 2], "values": [0.5, 0.3]}] * len(texts)
        sparse_embedder.embed_sparse = AsyncMock(side_effect=_sparse)

        vector_store = AsyncMock()
        vector_store.upsert_batch = AsyncMock()

        graph_store = AsyncMock()
        graph_store.upsert_document = AsyncMock(return_value=None)
        graph_store.execute_write = AsyncMock(return_value=None)

        return IngestionPipeline(
            embedder=embedder,
            sparse_embedder=sparse_embedder,
            vector_store=vector_store,
            graph_store=graph_store,
            enable_quality_filter=False,
        )

    def test_ingest_success(self):
        p = self._make_pipeline()
        raw = _make_raw(content="This is a test document with some content that needs to be long enough to produce chunks.")
        result = _run(p.ingest(raw, collection_name="test-kb"))

        assert result.success is True
        assert result.chunks_stored > 0
        p.vector_store.upsert_batch.assert_called_once()
        p.graph_store.upsert_document.assert_called_once()

    def test_ingest_with_parse_result(self):
        p = self._make_pipeline()
        parse_result = ParseResult(
            text="Pre-parsed body text with enough content to chunk",
            tables=[[["A", "B"], ["1", "2"]]],
            ocr_text="[Page 1 OCR] Extracted OCR text content",
        )
        raw = _make_raw(content="Some content")
        result = _run(p.ingest(raw, collection_name="test-kb", parse_result=parse_result))
        assert result.success is True

    def test_ingest_empty_content(self):
        p = self._make_pipeline()
        raw = _make_raw(content="")
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is False

    def test_ingest_quality_filter_blocks(self):
        """Pipeline with quality filter that blocks low quality."""
        p = self._make_pipeline()
        p.enable_quality_filter = True
        from src.pipeline.quality_processor import QualityTier
        p.min_quality_tier = QualityTier.GOLD
        raw = _make_raw(content="tiny")
        result = _run(p.ingest(raw, collection_name="test-kb"))
        # Either fails due to quality or chunk — both are valid
        assert result.success is False

    def test_ingest_with_author(self):
        p = self._make_pipeline()
        raw = _make_raw(content="Long enough content to pass chunking stage and produce output")
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is True
        # Author graph edge should be created
        assert p.graph_store.execute_write.called

    def test_ingest_with_metadata(self):
        p = self._make_pipeline()
        raw = _make_raw(
            content="Long enough content to pass chunking stage and produce output",
            metadata={"parent_id": "parent1", "space_key": "SPACE", "labels": ["L1", "L2"]},
        )
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is True

    def test_ingest_dedup_cache_hit(self):
        p = self._make_pipeline()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(return_value=True)

        raw = _make_raw(content="duplicate content")
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is False
        assert "dedup" in result.reason.lower() or "duplicate" in result.reason.lower()

    def test_ingest_dedup_cache_miss(self):
        p = self._make_pipeline()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(return_value=False)
        p.dedup_cache.add = AsyncMock()

        raw = _make_raw(content="Long enough unique content to pass chunking stage and produce output")
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is True

    def test_ingest_force_rebuild_skips_dedup(self):
        p = self._make_pipeline()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(return_value=True)
        p.dedup_cache.add = AsyncMock()

        raw = _make_raw(
            content="Long enough content to pass chunking stage and produce output",
            metadata={"force_rebuild": True},
        )
        result = _run(p.ingest(raw, collection_name="test-kb"))
        assert result.success is True
        p.dedup_cache.exists.assert_not_called()


# ---------------------------------------------------------------------------
# _create_graph_edges
# ---------------------------------------------------------------------------

class TestCreateGraphEdges:
    def test_graph_edges_with_all_fields(self):
        p = IngestionPipeline()
        p.graph_store = AsyncMock()
        p.graph_store.execute_write = AsyncMock(return_value={})

        raw = _make_raw(
            content="content with [link](http://example.com/ref) cross-reference",
            metadata={"parent_id": "parent1", "space_key": "SPACE", "space_name": "My Space"},
        )

        _run(p._create_graph_edges(raw, "test-kb", owner="alice", l1_category="IT운영"))
        assert p.graph_store.execute_write.call_count >= 2  # author + at least one more

    def test_graph_edges_failure_logged(self):
        p = IngestionPipeline()
        p.graph_store = AsyncMock()
        p.graph_store.execute_write = AsyncMock(side_effect=Exception("neo4j down"))

        raw = _make_raw(content="content", metadata={"parent_id": "p1"})
        # Should not raise
        _run(p._create_graph_edges(raw, "test-kb"))
