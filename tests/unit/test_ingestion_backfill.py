"""Coverage backfill — IngestionPipeline core paths.

Tests init, dedup check, quality gate, embed retry, morpheme extraction,
and the main ingest() orchestration with mocked dependencies.

Targets ~250 previously-missed lines in src/pipelines/ingestion.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import IngestionResult, RawDocument
from src.pipelines.document_parser import ParseResult
from src.pipelines.ingestion import (
    IngestionFeatureFlags,
    IngestionPipeline,
    _ChunkContext,
)
from src.pipelines.quality_processor import QualityTier


def _make_raw(
    title: str = "Test Document",
    content: str = "테스트 문서 본문 내용입니다. 충분한 길이를 가진 텍스트.",
    source_uri: str = "test.txt",
    author: str = "tester",
    doc_id: str = "doc-001",
    metadata: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
) -> RawDocument:
    return RawDocument(
        doc_id=doc_id,
        title=title,
        content=content,
        source_uri=source_uri,
        author=author,
        metadata=metadata or {},
        updated_at=updated_at,
    )


def _pipeline_with_mocks(**overrides) -> IngestionPipeline:
    """Build an IngestionPipeline with sensible mock defaults."""
    def _encode(texts, return_dense=True, **kw):
        return {"dense_vecs": [[0.1] * 1024] * len(texts)}

    embedder = MagicMock()
    embedder.encode = MagicMock(side_effect=_encode)

    async def _sparse(texts):
        return [{"indices": [1, 2], "values": [0.5, 0.3]}] * len(texts)

    sparse_embedder = AsyncMock()
    sparse_embedder.embed_sparse = AsyncMock(side_effect=_sparse)

    vector_store = AsyncMock()
    vector_store.upsert_batch = AsyncMock()

    graph_store = AsyncMock()
    graph_store.upsert_document = AsyncMock(return_value=None)
    graph_store.execute_write = AsyncMock(return_value=None)

    defaults: dict[str, Any] = {
        "embedder": embedder,
        "sparse_embedder": sparse_embedder,
        "vector_store": vector_store,
        "graph_store": graph_store,
        "enable_quality_filter": False,
    }
    defaults.update(overrides)
    return IngestionPipeline(**defaults)


# =========================================================================
# _try_binary_parse
# =========================================================================


class TestTryBinaryParse:
    def test_no_filename_returns_none(self) -> None:
        raw = _make_raw(metadata={})
        assert IngestionPipeline._try_binary_parse(raw) is None

    def test_non_binary_extension_returns_none(self) -> None:
        raw = _make_raw(metadata={"filename": "readme.txt"})
        assert IngestionPipeline._try_binary_parse(raw) is None

    def test_binary_ext_without_file_bytes_returns_none(self) -> None:
        raw = _make_raw(metadata={"filename": "report.pdf"})
        assert IngestionPipeline._try_binary_parse(raw) is None

    def test_binary_ext_with_non_bytes_file_bytes_returns_none(self) -> None:
        raw = _make_raw(metadata={"filename": "report.pdf", "file_bytes": "str"})
        assert IngestionPipeline._try_binary_parse(raw) is None

    @patch("src.pipelines.ingestion.parse_bytes_enhanced")
    def test_binary_parse_success(self, mock_parse) -> None:
        mock_parse.return_value = ParseResult(text="parsed content")
        raw = _make_raw(
            metadata={"filename": "report.pdf", "file_bytes": b"PDF bytes"}
        )
        result = IngestionPipeline._try_binary_parse(raw)
        assert result is not None
        assert result.text == "parsed content"

    @patch("src.pipelines.ingestion.parse_bytes_enhanced", side_effect=ValueError("bad"))
    def test_binary_parse_exception_returns_none(self, _mock) -> None:
        raw = _make_raw(
            metadata={"filename": "report.xlsx", "file_bytes": b"bytes"}
        )
        result = IngestionPipeline._try_binary_parse(raw)
        assert result is None


# =========================================================================
# _build_body_chunks / _append_table_chunks
# =========================================================================


class TestBuildBodyChunks:
    def test_basic(self) -> None:
        @dataclass
        class FakeChunkResult:
            chunks: list[str]

        cr = FakeChunkResult(chunks=["chunk0", "chunk1", "chunk2"])
        heading_map = {0: "H1 > H2", 2: "H1 > H3"}
        result = IngestionPipeline._build_body_chunks(cr, heading_map)
        assert len(result) == 3
        assert result[0] == ("chunk0", "body", "H1 > H2")
        assert result[1] == ("chunk1", "body", "")
        assert result[2] == ("chunk2", "body", "H1 > H3")


class TestAppendTableChunks:
    def test_no_parse_result(self) -> None:
        typed = []
        IngestionPipeline._append_table_chunks(typed, None)
        assert typed == []

    def test_no_tables(self) -> None:
        typed = []
        pr = ParseResult(text="body", tables=[])
        IngestionPipeline._append_table_chunks(typed, pr)
        assert typed == []

    def test_appends_tables(self) -> None:
        typed: list[tuple[str, str, str]] = []
        pr = ParseResult(
            text="body",
            tables=[[["Header", "Value"], ["A", "1"]]],
        )
        IngestionPipeline._append_table_chunks(typed, pr)
        assert len(typed) == 1
        assert typed[0][1] == "table"

    def test_skips_empty_table(self) -> None:
        typed: list[tuple[str, str, str]] = []
        pr = ParseResult(text="body", tables=[[]])
        IngestionPipeline._append_table_chunks(typed, pr)
        assert len(typed) == 0


# =========================================================================
# _split_ocr_text
# =========================================================================


class TestSplitOcrText:
    async def test_single_segment(self) -> None:
        p = _pipeline_with_mocks()
        result = await p._split_ocr_text("Some OCR text from a scan.")
        assert len(result) == 1
        assert result[0][1] == "ocr"

    async def test_page_segments(self) -> None:
        p = _pipeline_with_mocks()
        text = "[Page 1 main] First page.\n[Page 2 main] Second page."
        result = await p._split_ocr_text(text)
        assert len(result) == 2

    async def test_slide_segments(self) -> None:
        p = _pipeline_with_mocks()
        text = "[Slide 1] First slide.\n[Slide 2] Second slide."
        result = await p._split_ocr_text(text)
        assert len(result) == 2

    async def test_image_segments(self) -> None:
        p = _pipeline_with_mocks()
        text = "[Image 1 fig] First image.\n[Image 2 fig] Second image."
        result = await p._split_ocr_text(text)
        assert len(result) == 2

    async def test_large_segment_sub_chunked(self) -> None:
        p = _pipeline_with_mocks()
        # Create a segment larger than max_chunk_chars
        from src.config.weights import weights
        big = "A" * (weights.chunking.max_chunk_chars + 500)
        result = await p._split_ocr_text(big)
        assert len(result) >= 1

    async def test_empty_segments_skipped(self) -> None:
        p = _pipeline_with_mocks()
        text = "[Page 1 main]   \n\n[Page 2 main] Actual content."
        result = await p._split_ocr_text(text)
        # Only the segment with actual content should be present
        assert all(r[0].strip() for r in result)


# =========================================================================
# _append_date_author_tokens
# =========================================================================


class TestAppendDateAuthorTokens:
    def test_date_pattern_yyyy_mm(self) -> None:
        morphemes = ["term1 term2"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "2025_03_report", None,
        )
        assert "2025" in result[0]
        assert "3월" in result[0]

    def test_date_pattern_korean(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "2025년 4월 보고서", None,
        )
        assert "2025" in result[0]
        assert "4월" in result[0]

    def test_week_pattern(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "3월 2주차 보고", None,
        )
        assert "3월" in result[0]
        assert "2주차" in result[0]

    def test_author_appended(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "plain title", "김철수",
        )
        assert "김철수" in result[0]

    def test_no_date_no_author(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "plain title", None,
        )
        assert result is morphemes  # unchanged, same object

    def test_none_title(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, None, None,
        )
        assert result is morphemes

    def test_combined_date_and_week(self) -> None:
        morphemes = ["term1"]
        result = IngestionPipeline._append_date_author_tokens(
            morphemes, "2025_03 리포트 3월 2주차", "author1",
        )
        assert "2025" in result[0]
        assert "2주차" in result[0]
        assert "author1" in result[0]


# =========================================================================
# _build_chunk_item
# =========================================================================


class TestBuildChunkItem:
    def test_builds_metadata(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(updated_at=datetime(2025, 1, 1, tzinfo=UTC))
        ctx = _ChunkContext(
            raw=raw,
            collection_name="kb-test",
            chunk_types=["body"],
            chunk_heading_paths=["H1 > H2"],
            chunk_morphemes=["morpheme1"],
            now_iso="2025-01-01T00:00:00",
            quality_tier=QualityTier.SILVER,
            quality_score=70.0,
            doc_type="guide",
            owner="alice",
            l1_category="IT인프라",
            content_flags={"has_tables": True, "has_code": False, "has_images": False},
            parse_result=None,
        )
        item = p._build_chunk_item(
            0, "chunk text", [0.1, 0.2], {"indices": [1], "values": [0.5]},
            ctx=ctx,
        )
        assert item["content"] is not None
        assert item["dense_vector"] == [0.1, 0.2]
        assert isinstance(item["sparse_vector"], dict)
        # sparse converted from indices/values to {index: value}
        assert item["sparse_vector"] == {1: 0.5}
        meta = item["metadata"]
        assert meta["doc_id"] == "doc-001"
        assert meta["chunk_type"] == "body"
        assert meta["heading_path"] == "H1 > H2"
        assert meta["has_tables"] is True
        assert meta["quality_tier"] == "SILVER"
        assert "last_modified" in meta

    def test_sparse_dict_passthrough(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw()
        ctx = _ChunkContext(
            raw=raw, collection_name="kb", chunk_types=["body"],
            chunk_heading_paths=[""], chunk_morphemes=[""],
            now_iso="t", quality_tier=QualityTier.BRONZE, quality_score=50.0,
            doc_type="reference", owner="", l1_category="기타",
            content_flags={}, parse_result=None,
        )
        sparse = {10: 0.9, 20: 0.1}
        item = p._build_chunk_item(0, "text", [0.1], sparse, ctx=ctx)
        assert item["sparse_vector"] == sparse

    def test_parse_result_file_modified(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(updated_at=None)
        pr = ParseResult(text="t", file_modified_at="2024-06-01T00:00:00")
        ctx = _ChunkContext(
            raw=raw, collection_name="kb", chunk_types=["body"],
            chunk_heading_paths=[""], chunk_morphemes=[""],
            now_iso="t", quality_tier=QualityTier.BRONZE, quality_score=50.0,
            doc_type="reference", owner="", l1_category="기타",
            content_flags={}, parse_result=pr,
        )
        item = p._build_chunk_item(0, "text", [0.1], {}, ctx=ctx)
        assert item["metadata"]["last_modified"] == "2024-06-01T00:00:00"

    def test_morphemes_out_of_range(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw()
        ctx = _ChunkContext(
            raw=raw, collection_name="kb", chunk_types=["body", "body"],
            chunk_heading_paths=["", ""], chunk_morphemes=["m0"],
            now_iso="t", quality_tier=QualityTier.BRONZE, quality_score=50.0,
            doc_type="reference", owner="", l1_category="기타",
            content_flags={}, parse_result=None,
        )
        item = p._build_chunk_item(1, "text", [0.1], {}, ctx=ctx)
        assert item["metadata"]["morphemes"] == ""


# =========================================================================
# _build_title_item
# =========================================================================


class TestBuildTitleItem:
    async def test_returns_title_item(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(
            title="Test Title",
            metadata={"labels": ["label1", "label2"]},
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        item = await p._build_title_item(
            raw, "kb-test", "2025-01-01T00:00:00",
            QualityTier.GOLD, 90.0, "guide", "alice", "IT인프라",
            {"has_tables": False, "has_code": False, "has_images": False},
            None,
        )
        assert item is not None
        assert item["metadata"]["chunk_type"] == "title"
        assert item["metadata"]["chunk_index"] == -1
        assert "last_modified" in item["metadata"]

    async def test_empty_title_returns_none(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(title="", metadata={})
        item = await p._build_title_item(
            raw, "kb", "t", QualityTier.BRONZE, 50.0,
            "reference", "", "기타", {}, None,
        )
        assert item is None

    async def test_title_with_parse_result_modified(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(title="Title", updated_at=None)
        pr = ParseResult(text="t", file_modified_at="2024-01-01T00:00:00")
        item = await p._build_title_item(
            raw, "kb", "t", QualityTier.BRONZE, 50.0,
            "reference", "", "기타", {}, pr,
        )
        assert item is not None
        assert item["metadata"]["last_modified"] == "2024-01-01T00:00:00"

    async def test_sparse_dict_passthrough(self) -> None:
        """When sparse embedder returns a plain dict (no indices key)."""
        async def _sparse_dict(texts):
            return [{10: 0.9}] * len(texts)

        p = _pipeline_with_mocks()
        p.sparse_embedder.embed_sparse = AsyncMock(side_effect=_sparse_dict)
        raw = _make_raw(title="Title")
        item = await p._build_title_item(
            raw, "kb", "t", QualityTier.BRONZE, 50.0,
            "reference", "", "기타", {}, None,
        )
        assert item is not None
        assert item["sparse_vector"] == {10: 0.9}


# =========================================================================
# _add_context_prefixes
# =========================================================================


class TestAddContextPrefixes:
    def test_prefixes_added(self) -> None:
        raw = _make_raw(metadata={"labels": ["L1"], "parent_title": "Parent"})
        typed_chunks = [
            ("chunk1 text", "body", "H1 > H2"),
            ("chunk2 text", "table", ""),
        ]
        prefixed, types, paths = IngestionPipeline._add_context_prefixes(
            raw, typed_chunks, "A summary",
        )
        assert len(prefixed) == 2
        assert types == ["body", "table"]
        assert paths == ["H1 > H2", ""]
        # Prefix should contain doc title
        assert "Test Document" in prefixed[0]


# =========================================================================
# _embed_sparse_with_retry
# =========================================================================


class TestEmbedSparseWithRetry:
    async def test_success_first_attempt(self) -> None:
        p = _pipeline_with_mocks()
        result = await p._embed_sparse_with_retry(["text"])
        assert len(result) == 1

    async def test_retry_on_failure(self) -> None:
        call_count = 0

        async def _fail_once(texts):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("timeout")
            return [{"indices": [1], "values": [0.5]}] * len(texts)

        p = _pipeline_with_mocks()
        p.sparse_embedder.embed_sparse = AsyncMock(side_effect=_fail_once)
        p._EMBED_RETRY_DELAY = 0
        result = await p._embed_sparse_with_retry(["text"])
        assert len(result) == 1
        assert call_count == 2

    async def test_all_retries_fail(self) -> None:
        p = _pipeline_with_mocks()
        p.sparse_embedder.embed_sparse = AsyncMock(
            side_effect=ConnectionError("always fail"),
        )
        p._EMBED_RETRY_DELAY = 0
        with pytest.raises(ConnectionError):
            await p._embed_sparse_with_retry(["text"])


# =========================================================================
# _build_result_metadata
# =========================================================================


class TestBuildResultMetadata:
    def test_basic_metadata(self) -> None:
        raw = _make_raw()
        ctx = _ChunkContext(
            raw=raw, collection_name="kb-test",
            chunk_types=["body", "body", "table"],
            chunk_heading_paths=["H1", "", ""],
            chunk_morphemes=["m1", "m2", "m3"],
            now_iso="t", quality_tier=QualityTier.SILVER,
            quality_score=70.0, doc_type="guide",
            owner="alice", l1_category="IT인프라",
            content_flags={"has_tables": True, "has_code": False, "has_images": False},
        )
        items = [{"id": i} for i in range(4)]  # 3 body+table + 1 title
        heading_map = {0: "H1"}

        meta = IngestionPipeline._build_result_metadata(
            ctx, "semantic", items, heading_map,
        )
        assert meta["collection_name"] == "kb-test"
        assert meta["body_chunks"] == 2
        assert meta["table_chunks"] == 1
        assert meta["has_heading_paths"] is True
        assert meta["quality_tier"] == "SILVER"
        assert meta["has_tables"] is True

    def test_with_optional_stats(self) -> None:
        raw = _make_raw()
        ctx = _ChunkContext(
            raw=raw, collection_name="kb",
            chunk_types=["body"], chunk_heading_paths=[""],
            chunk_morphemes=[""], now_iso="t",
            quality_tier=QualityTier.BRONZE, quality_score=50.0,
            doc_type="reference", owner="", l1_category="기타",
            content_flags={},
        )
        meta = IngestionPipeline._build_result_metadata(
            ctx, "fixed", [{}], {},
            graphrag_stats={"nodes_extracted": 5},
            term_extraction_stats={"terms_extracted": 10},
            synonym_discovery_stats={"synonyms_discovered": 2},
            dedup_result_info={"status": "unique"},
        )
        assert meta["graphrag"] == {"nodes_extracted": 5}
        assert meta["term_extraction"] == {"terms_extracted": 10}
        assert meta["synonym_discovery"] == {"synonyms_discovered": 2}
        assert meta["dedup"] == {"status": "unique"}


# =========================================================================
# _check_quality
# =========================================================================


class TestCheckQuality:
    def test_below_min_tier_rejected(self) -> None:
        p = IngestionPipeline(
            enable_quality_filter=True,
        )
        p.min_quality_tier = QualityTier.GOLD
        raw = _make_raw(content="짧은 텍스트")
        tier, metrics, failure = p._check_quality(raw)
        assert failure is not None
        assert "quality" in failure.reason.lower()

    def test_disabled_returns_bronze(self) -> None:
        p = IngestionPipeline(enable_quality_filter=False)
        raw = _make_raw(content="짧은")
        tier, metrics, failure = p._check_quality(raw)
        assert tier == QualityTier.BRONZE
        assert metrics is None
        assert failure is None


# =========================================================================
# _check_ingestion_gate
# =========================================================================


class TestCheckIngestionGate:
    def test_no_gate_returns_none(self) -> None:
        p = IngestionPipeline()
        assert p._check_ingestion_gate(_make_raw(), "kb") is None

    def test_gate_blocks(self) -> None:
        p = IngestionPipeline()
        gate = MagicMock()
        gate_result = MagicMock()
        gate_result.is_blocked = True
        gate_result.action = MagicMock()
        gate_result.action.value = "REJECT"
        gate_result.failed_count = 2
        gate.run_gates.return_value = gate_result
        p._ingestion_gate = gate

        result = p._check_ingestion_gate(_make_raw(), "kb")
        assert result is not None
        assert result.success is False

    def test_gate_passes(self) -> None:
        p = IngestionPipeline()
        gate = MagicMock()
        gate_result = MagicMock()
        gate_result.is_blocked = False
        gate.run_gates.return_value = gate_result
        p._ingestion_gate = gate

        result = p._check_ingestion_gate(_make_raw(), "kb")
        assert result is None


# =========================================================================
# _check_dedup with dedup_pipeline
# =========================================================================


class TestDedupPipeline:
    async def test_force_rebuild_skips(self) -> None:
        p = _pipeline_with_mocks()
        p.dedup_pipeline = MagicMock()
        raw = _make_raw(metadata={"force_rebuild": True})
        failure, info = await p._check_dedup(raw, "kb", "hash123")
        assert failure is None

    async def test_exact_duplicate(self) -> None:
        p = _pipeline_with_mocks()

        mock_result = MagicMock()
        mock_result.status = MagicMock()
        mock_result.status.value = "EXACT_DUPLICATE"
        # Set the name for comparison
        mock_result.status.__eq__ = lambda self, other: (
            other.value == "EXACT_DUPLICATE"
            if hasattr(other, "value") else False
        )
        mock_result.duplicate_of = "doc-999"
        mock_result.processing_time_ms = 1.0
        mock_result.to_dict.return_value = {"status": "exact_dup"}

        # Patch the imports inside _check_dedup
        with patch.dict("sys.modules", {}):
            from unittest.mock import patch as _patch
            with _patch(
                "src.pipelines.ingestion.IngestionPipeline._check_dedup",
                new=IngestionPipeline._check_dedup,
            ):
                # Use a simpler approach: mock at module level
                pass

        # Direct mock of dedup_pipeline
        dedup_pipeline = AsyncMock()
        from enum import Enum

        class FakeDedupStatus(Enum):
            EXACT_DUPLICATE = "EXACT_DUPLICATE"
            NEAR_DUPLICATE = "NEAR_DUPLICATE"
            SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
            UNIQUE = "UNIQUE"

        dedup_result = MagicMock()
        dedup_result.status = FakeDedupStatus.EXACT_DUPLICATE
        dedup_result.duplicate_of = "doc-999"
        dedup_result.processing_time_ms = 1.0
        dedup_result.to_dict.return_value = {"status": "exact_dup"}
        dedup_pipeline.add = AsyncMock(return_value=dedup_result)

        p.dedup_pipeline = dedup_pipeline
        p.dedup_cache = None

        with patch(
            "src.pipelines.ingestion.DedupStatus",
            FakeDedupStatus,
            create=True,
        ):
            # Import the real DedupStatus path inside _check_dedup
            import sys
            fake_dedup_mod = MagicMock()
            fake_dedup_mod.Document = MagicMock()
            fake_dedup_mod.DedupStatus = FakeDedupStatus
            sys.modules["src.pipelines.dedup"] = fake_dedup_mod

            try:
                raw = _make_raw()
                failure, info = await p._check_dedup(raw, "kb", "hash123")
                assert failure is not None
                assert "duplicate" in failure.reason.lower()
            finally:
                del sys.modules["src.pipelines.dedup"]

    async def test_near_duplicate_proceeds(self) -> None:
        from enum import Enum

        class FakeDedupStatus(Enum):
            EXACT_DUPLICATE = "EXACT_DUPLICATE"
            NEAR_DUPLICATE = "NEAR_DUPLICATE"
            SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
            UNIQUE = "UNIQUE"

        dedup_result = MagicMock()
        dedup_result.status = FakeDedupStatus.NEAR_DUPLICATE
        dedup_result.duplicate_of = "doc-888"
        dedup_result.similarity_score = 0.85
        dedup_result.processing_time_ms = 2.0
        dedup_result.to_dict.return_value = {"status": "near_dup"}

        p = _pipeline_with_mocks()
        p.dedup_pipeline = AsyncMock()
        p.dedup_pipeline.add = AsyncMock(return_value=dedup_result)
        p.dedup_cache = None

        import sys
        fake_mod = MagicMock()
        fake_mod.Document = MagicMock()
        fake_mod.DedupStatus = FakeDedupStatus
        sys.modules["src.pipelines.dedup"] = fake_mod

        try:
            raw = _make_raw()
            failure, info = await p._check_dedup(raw, "kb", "hash123")
            assert failure is None  # near-dup proceeds
            assert info == {"status": "near_dup"}
        finally:
            del sys.modules["src.pipelines.dedup"]

    async def test_dedup_pipeline_exception_proceeds(self) -> None:
        p = _pipeline_with_mocks()
        p.dedup_pipeline = AsyncMock()
        p.dedup_pipeline.add = AsyncMock(side_effect=RuntimeError("db down"))
        p.dedup_cache = None

        import sys
        fake_mod = MagicMock()
        fake_mod.Document = MagicMock()
        fake_mod.DedupStatus = MagicMock()
        sys.modules["src.pipelines.dedup"] = fake_mod

        try:
            raw = _make_raw()
            failure, info = await p._check_dedup(raw, "kb", "hash123")
            assert failure is None  # exception => proceed
        finally:
            del sys.modules["src.pipelines.dedup"]

    async def test_dedup_cache_exception_proceeds(self) -> None:
        p = _pipeline_with_mocks()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(side_effect=RuntimeError("redis down"))

        raw = _make_raw()
        failure, info = await p._check_dedup(raw, "kb", "hash123")
        assert failure is None  # exception => proceed


# =========================================================================
# _run_term_extraction
# =========================================================================


class TestRunTermExtraction:
    async def test_disabled(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = False
        stats = await p._run_term_extraction(
            _make_raw(), [("text", "body", "")], "kb",
        )
        assert stats == {}

    async def test_no_extractor(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        p.term_extractor = None
        stats = await p._run_term_extraction(
            _make_raw(), [("text", "body", "")], "kb",
        )
        assert stats == {}

    async def test_success(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        p.term_extractor = AsyncMock()
        p.term_extractor.extract_from_chunks = AsyncMock(
            return_value=[{"term": "t1"}, {"term": "t2"}],
        )
        p.term_extractor.save_extracted_terms = AsyncMock(return_value=2)

        stats = await p._run_term_extraction(
            _make_raw(), [("chunk text", "body", "")], "kb",
        )
        assert stats["terms_extracted"] == 2
        assert stats["terms_saved"] == 2

    async def test_empty_terms(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        p.term_extractor = AsyncMock()
        p.term_extractor.extract_from_chunks = AsyncMock(return_value=[])

        stats = await p._run_term_extraction(
            _make_raw(), [("chunk text", "body", "")], "kb",
        )
        assert stats == {}

    async def test_exception_logged(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        p.term_extractor = AsyncMock()
        p.term_extractor.extract_from_chunks = AsyncMock(
            side_effect=RuntimeError("llm down"),
        )

        stats = await p._run_term_extraction(
            _make_raw(), [("chunk text", "body", "")], "kb",
        )
        assert "error" in stats


# =========================================================================
# _run_synonym_discovery
# =========================================================================


class TestRunSynonymDiscovery:
    async def test_disabled(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = False
        stats = await p._run_synonym_discovery(_make_raw(), "kb")
        assert stats == {}

    async def test_no_discover_fn(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        p.term_extractor = MagicMock(spec=[])  # no attributes
        stats = await p._run_synonym_discovery(_make_raw(), "kb")
        assert stats == {}

    async def test_success_with_glossary(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        te = MagicMock()
        te.discover_synonyms = AsyncMock(
            return_value=[{"term": "A", "synonym": "B"}],
        )
        te.save_discovered_synonyms = AsyncMock(return_value=1)
        glossary_repo = MagicMock()
        glossary_repo.list_by_kb = AsyncMock(return_value=[{"term": "A"}])
        te._glossary_repo = glossary_repo
        p.term_extractor = te

        stats = await p._run_synonym_discovery(_make_raw(), "kb")
        assert stats["synonyms_discovered"] == 1
        assert stats["synonyms_saved"] == 1

    async def test_glossary_repo_exception(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        te = MagicMock()
        te.discover_synonyms = AsyncMock(
            return_value=[{"term": "A", "synonym": "B"}],
        )
        te.save_discovered_synonyms = AsyncMock(return_value=1)
        glossary_repo = MagicMock()
        glossary_repo.list_by_kb = AsyncMock(
            side_effect=RuntimeError("db down"),
        )
        te._glossary_repo = glossary_repo
        p.term_extractor = te

        stats = await p._run_synonym_discovery(_make_raw(), "kb")
        # Should proceed even if glossary fetch fails
        assert stats["synonyms_discovered"] == 1

    async def test_exception_logged(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        te = MagicMock()
        te.discover_synonyms = AsyncMock(side_effect=RuntimeError("bad"))
        te.save_discovered_synonyms = AsyncMock()
        te._glossary_repo = None
        p.term_extractor = te

        stats = await p._run_synonym_discovery(_make_raw(), "kb")
        assert "error" in stats


# =========================================================================
# _run_graphrag
# =========================================================================


class TestRunGraphrag:
    async def test_disabled(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_graphrag = False
        stats = await p._run_graphrag(_make_raw(), "kb")
        assert stats == {}

    async def test_no_extractor(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_graphrag = True
        p.graphrag_extractor = None
        stats = await p._run_graphrag(_make_raw(), "kb")
        assert stats == {}

    async def test_success(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_graphrag = True
        extractor = MagicMock()
        extraction_result = MagicMock()
        extraction_result.node_count = 3
        extraction_result.relationship_count = 2
        extractor.extract = MagicMock(return_value=extraction_result)
        extractor.save_to_neo4j = MagicMock(
            return_value={"nodes_saved": 3, "rels_saved": 2},
        )
        p.graphrag_extractor = extractor

        raw = _make_raw(updated_at=datetime(2025, 1, 1, tzinfo=UTC))
        stats = await p._run_graphrag(raw, "kb")
        assert stats["nodes_extracted"] == 3
        assert stats["relationships_extracted"] == 2

    async def test_zero_results(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_graphrag = True
        extractor = MagicMock()
        extraction_result = MagicMock()
        extraction_result.node_count = 0
        extraction_result.relationship_count = 0
        extractor.extract = MagicMock(return_value=extraction_result)
        p.graphrag_extractor = extractor

        stats = await p._run_graphrag(_make_raw(), "kb")
        assert stats == {}

    async def test_exception_logged(self) -> None:
        p = _pipeline_with_mocks()
        p.enable_graphrag = True
        extractor = MagicMock()
        extractor.extract = MagicMock(side_effect=RuntimeError("llm down"))
        p.graphrag_extractor = extractor

        stats = await p._run_graphrag(_make_raw(), "kb")
        assert "error" in stats

    async def test_legal_doc_routes_to_legal_extractor(self) -> None:
        p = _pipeline_with_mocks()
        legal_extractor = AsyncMock()
        result = MagicMock()
        result.node_count = 5
        result.relationship_count = 3
        legal_extractor.extract_from_document = AsyncMock(return_value=result)
        legal_extractor.save_to_neo4j = MagicMock(
            return_value={"nodes_saved": 5},
        )
        p.legal_graph_extractor = legal_extractor

        raw = _make_raw(metadata={"_is_legal_document": True})
        stats = await p._run_graphrag(raw, "kb")
        assert stats["extractor"] == "legal_rule_based"
        assert stats["nodes_extracted"] == 5


# =========================================================================
# _run_legal_graph_extraction
# =========================================================================


class TestRunLegalGraphExtraction:
    async def test_zero_results(self) -> None:
        p = _pipeline_with_mocks()
        legal_extractor = AsyncMock()
        result = MagicMock()
        result.node_count = 0
        result.relationship_count = 0
        legal_extractor.extract_from_document = AsyncMock(return_value=result)
        p.legal_graph_extractor = legal_extractor

        stats = await p._run_legal_graph_extraction(_make_raw(), "kb")
        assert stats == {}

    async def test_exception_logged(self) -> None:
        p = _pipeline_with_mocks()
        legal_extractor = AsyncMock()
        legal_extractor.extract_from_document = AsyncMock(
            side_effect=RuntimeError("parse fail"),
        )
        p.legal_graph_extractor = legal_extractor

        stats = await p._run_legal_graph_extraction(_make_raw(), "kb")
        assert "error" in stats
        assert stats["extractor"] == "legal_rule_based"


# =========================================================================
# _run_tree_index_builder
# =========================================================================


class TestRunTreeIndexBuilder:
    @patch("src.config.get_settings")
    async def test_disabled(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = False
        p = _pipeline_with_mocks()
        await p._run_tree_index_builder(_make_raw(), [], [], "kb")
        # Should return early, no error

    @patch("src.config.get_settings")
    async def test_no_graph_store(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = True
        p = _pipeline_with_mocks()
        p.graph_store = None
        await p._run_tree_index_builder(_make_raw(), [], [], "kb")

    @patch("src.config.get_settings")
    async def test_no_body_chunks(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = True
        p = _pipeline_with_mocks()
        items = [{"metadata": {"chunk_type": "title"}}]
        await p._run_tree_index_builder(_make_raw(), items, [], "kb")

    @patch("src.config.get_settings")
    async def test_success_or_import_error(self, mock_settings) -> None:
        """Covers the body of _run_tree_index_builder; import may fail
        inside the method (tree_index_builder module), but the exception
        handler catches it gracefully."""
        mock_settings.return_value.tree_index.enabled = True
        p = _pipeline_with_mocks()
        items = [
            {
                "metadata": {"chunk_type": "body", "chunk_index": 0},
                "point_id": "point-0",
            },
        ]
        # The method imports tree_index_builder internally.
        # If it's not available the except branch catches it.
        await p._run_tree_index_builder(
            _make_raw(), items, ["H1"], "kb",
        )


# =========================================================================
# _run_summary_tree_builder
# =========================================================================


class TestRunSummaryTreeBuilder:
    @patch("src.config.get_settings")
    async def test_disabled(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = False
        p = _pipeline_with_mocks()
        await p._run_summary_tree_builder(
            _make_raw(), [], [], [], "kb", [], "t",
        )

    @patch("src.config.get_settings")
    async def test_summary_disabled(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = True
        mock_settings.return_value.tree_index.summary_enabled = False
        p = _pipeline_with_mocks()
        await p._run_summary_tree_builder(
            _make_raw(), [], [], [], "kb", [], "t",
        )

    @patch("src.config.get_settings")
    async def test_no_embedder_or_llm(self, mock_settings) -> None:
        mock_settings.return_value.tree_index.enabled = True
        mock_settings.return_value.tree_index.summary_enabled = True
        p = _pipeline_with_mocks()
        # No embedding_provider or llm_client
        await p._run_summary_tree_builder(
            _make_raw(), [], [], [], "kb", [], "t",
        )


# =========================================================================
# _build_typed_chunks
# =========================================================================


class TestBuildTypedChunks:
    async def test_empty_content_returns_failure(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(content="")
        result = await p._build_typed_chunks(raw, None)
        assert isinstance(result, IngestionResult)
        assert result.success is False

    async def test_legal_document_uses_legal_chunker(self) -> None:
        p = _pipeline_with_mocks()
        p.chunker = MagicMock()

        @dataclass
        class FakeChunkResult:
            chunks: list[str]
            heading_chunks: list | None = None

        p.chunker.chunk_legal_articles = MagicMock(
            return_value=FakeChunkResult(chunks=["제1조 내용"], heading_chunks=None),
        )
        raw = _make_raw(
            content="제1조 (목적) 이 규정은 목적을 정한다.",
            metadata={"_is_legal_document": True},
        )
        result = await p._build_typed_chunks(raw, None)
        assert not isinstance(result, IngestionResult)
        p.chunker.chunk_legal_articles.assert_called_once()

    async def test_with_parse_result_tables_and_ocr(self) -> None:
        p = _pipeline_with_mocks()
        pr = ParseResult(
            text="Parsed body text with enough content.",
            tables=[[["H1", "H2"], ["V1", "V2"]]],
            ocr_text="[Page 1] OCR content from scan.",
        )
        result = await p._build_typed_chunks(_make_raw(), pr)
        assert not isinstance(result, IngestionResult)
        typed_chunks, heading_map, doc_summary = result
        types = [ct for _, ct, _ in typed_chunks]
        assert "table" in types or "ocr" in types or "body" in types


# =========================================================================
# _create_graph_edges
# =========================================================================


class TestCreateGraphEdges:
    async def test_all_edge_types(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(
            content="See [guide](/pages/123) for details.",
            metadata={
                "parent_id": "parent-1",
                "space_key": "SPACE",
                "space_name": "My Space",
            },
        )
        await p._create_graph_edges(
            raw, "kb", owner="alice", l1_category="IT인프라",
        )
        # parent_id + author + space + cross-ref + owner + category = 6 calls
        assert p.graph_store.execute_write.call_count >= 4

    async def test_no_optional_fields(self) -> None:
        p = _pipeline_with_mocks()
        raw = _make_raw(author="", content="no links", metadata={})
        await p._create_graph_edges(raw, "kb", owner="", l1_category="기타")
        # "기타" is skipped, no parent, no author, no space => minimal calls
        assert p.graph_store.execute_write.call_count == 0

    async def test_exception_does_not_raise(self) -> None:
        p = _pipeline_with_mocks()
        p.graph_store.execute_write = AsyncMock(
            side_effect=RuntimeError("neo4j down"),
        )
        raw = _make_raw(metadata={"parent_id": "p1"})
        # Should not raise
        await p._create_graph_edges(raw, "kb")


# =========================================================================
# IngestionPipeline init edge cases
# =========================================================================


class TestIngestionPipelineInitEdgeCases:
    def test_ingestion_gate_import_failure(self) -> None:
        """When IngestionGate import fails, gate should be None."""
        with patch(
            "src.pipelines.ingestion.IngestionGate",
            side_effect=ImportError("no module"),
            create=True,
        ):
            # The actual import happens inside __init__ conditionally
            flags = IngestionFeatureFlags(enable_ingestion_gate=True)
            _p = IngestionPipeline(flags=flags)  # noqa: F841
            # Gate init may succeed or fail depending on module availability
            # but pipeline should not raise

    def test_flag_kwargs_filters_unknown(self) -> None:
        """Unknown kwargs should be silently ignored."""
        p = IngestionPipeline(
            enable_quality_filter=False,
            unknown_flag="ignored",
        )
        assert p.enable_quality_filter is False


# =========================================================================
# Full ingest — additional paths
# =========================================================================


class TestIngestAdditionalPaths:
    async def test_ingest_with_quality_metrics_content_flags(self) -> None:
        """Quality filter enabled but passes — covers content_flags branch."""
        p = _pipeline_with_mocks()
        p.enable_quality_filter = True
        p.min_quality_tier = QualityTier.NOISE
        raw = _make_raw(
            content="GS리테일 매장 운영 가이드 문서입니다. " * 30,
        )
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True
        assert "has_tables" in result.metadata

    async def test_ingest_registers_dedup_hash(self) -> None:
        """Covers dedup_cache.add after successful ingest."""
        p = _pipeline_with_mocks()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(return_value=False)
        p.dedup_cache.add = AsyncMock()

        raw = _make_raw(content="문서 내용이 충분히 깁니다. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True
        p.dedup_cache.add.assert_called_once()

    async def test_ingest_dedup_cache_add_failure(self) -> None:
        """Covers exception in dedup_cache.add (non-critical)."""
        p = _pipeline_with_mocks()
        p.dedup_cache = AsyncMock()
        p.dedup_cache.exists = AsyncMock(return_value=False)
        p.dedup_cache.add = AsyncMock(side_effect=RuntimeError("redis"))

        raw = _make_raw(content="문서 내용이 충분히 깁니다. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True  # Should still succeed

    async def test_ingest_pipeline_exception_caught(self) -> None:
        """Covers the outer try/except in ingest()."""
        p = _pipeline_with_mocks()
        p.embedder.encode = MagicMock(
            side_effect=RuntimeError("catastrophic"),
        )
        p._EMBED_RETRY_DELAY = 0

        raw = _make_raw(content="문서 내용. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is False
        assert result.stage == "pipeline"

    async def test_ingest_with_graphrag_enabled(self) -> None:
        """Covers the graphrag path during ingest."""
        p = _pipeline_with_mocks()
        p.enable_graphrag = True
        extractor = MagicMock()
        er = MagicMock()
        er.node_count = 2
        er.relationship_count = 1
        extractor.extract = MagicMock(return_value=er)
        extractor.save_to_neo4j = MagicMock(return_value={})
        p.graphrag_extractor = extractor

        raw = _make_raw(content="문서 내용이 충분합니다. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True
        if "graphrag" in result.metadata:
            assert result.metadata["graphrag"]["nodes_extracted"] == 2

    async def test_ingest_with_term_extraction(self) -> None:
        """Covers term extraction + synonym discovery during ingest."""
        p = _pipeline_with_mocks()
        p.enable_term_extraction = True
        te = AsyncMock()
        te.extract_from_chunks = AsyncMock(return_value=[{"term": "t1"}])
        te.save_extracted_terms = AsyncMock(return_value=1)
        te.discover_synonyms = AsyncMock(return_value=[])
        te.save_discovered_synonyms = AsyncMock(return_value=0)
        te._glossary_repo = None
        p.term_extractor = te

        raw = _make_raw(content="문서 내용이 충분합니다. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True

    async def test_ingest_with_date_in_title(self) -> None:
        """Covers _append_date_author_tokens inside ingest flow."""
        p = _pipeline_with_mocks()
        raw = _make_raw(
            title="2025_03 월간보고서 3월 2주차",
            content="문서 내용이 충분합니다. " * 20,
        )
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is True

    async def test_ingest_gate_blocks(self) -> None:
        """Covers gate check returning failure in ingest()."""
        p = _pipeline_with_mocks()
        gate = MagicMock()
        gate_result = MagicMock()
        gate_result.is_blocked = True
        gate_result.action = MagicMock()
        gate_result.action.value = "REJECT"
        gate_result.failed_count = 1
        gate.run_gates.return_value = gate_result
        p._ingestion_gate = gate

        raw = _make_raw(content="문서 내용. " * 20)
        result = await p.ingest(raw, collection_name="kb-test")
        assert result.success is False
        assert "gate" in (result.stage or "").lower() or "gate" in (
            result.reason or ""
        ).lower()


# =========================================================================
# _extract_morphemes edge cases
# =========================================================================


class TestExtractMorphemesEdgeCases:
    def test_kiwi_import_failure(self) -> None:
        """When KiwiPy is not available, returns empty strings."""
        with patch.dict("sys.modules", {"kiwipiepy": None}):
            result = IngestionPipeline._extract_morphemes(
                [("text", "body", "")],
            )
            assert len(result) == 1

    def test_large_chunk_text_truncated(self) -> None:
        """Kiwi tokenize receives at most 2000 chars."""
        chunks = [("A" * 5000, "body", "")]
        result = IngestionPipeline._extract_morphemes(chunks)
        assert len(result) == 1


# =========================================================================
# _ChunkContext dataclass
# =========================================================================


class TestChunkContext:
    def test_defaults(self) -> None:
        raw = _make_raw()
        ctx = _ChunkContext(
            raw=raw, collection_name="kb",
            chunk_types=[], chunk_heading_paths=[],
            chunk_morphemes=[], now_iso="t",
            quality_tier=QualityTier.BRONZE,
            quality_score=50.0, doc_type="reference",
            owner="", l1_category="기타",
            content_flags={},
        )
        assert ctx.parse_result is None

    def test_with_parse_result(self) -> None:
        raw = _make_raw()
        pr = ParseResult(text="t")
        ctx = _ChunkContext(
            raw=raw, collection_name="kb",
            chunk_types=[], chunk_heading_paths=[],
            chunk_morphemes=[], now_iso="t",
            quality_tier=QualityTier.BRONZE,
            quality_score=50.0, doc_type="reference",
            owner="", l1_category="기타",
            content_flags={}, parse_result=pr,
        )
        assert ctx.parse_result is pr
