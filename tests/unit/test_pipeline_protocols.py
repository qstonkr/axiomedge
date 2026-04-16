"""Tests for Search/Ingestion pipeline stage protocols.

Verifies Protocol, Context, Pipeline runner, builder pattern, early-exit.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.search.pipeline.protocol import SearchContext, SearchPipeline, SearchStage
from src.pipeline.stages.protocol import (
    IngestionStageContext,
    IngestionPipelineRunner,
    IngestionStage,
)
from src.core.models import RawDocument, IngestionResult


# ==========================================================================
# Search pipeline
# ==========================================================================


class _IncrementStage:
    """Test stage that appends to chunks."""
    name = "increment"

    async def process(self, ctx: SearchContext) -> SearchContext:
        ctx.all_chunks.append(f"chunk_{len(ctx.all_chunks)}")
        return ctx


class _FailStage:
    """Test stage that raises."""
    name = "fail"

    async def process(self, ctx: SearchContext) -> SearchContext:
        raise RuntimeError("stage error")


class TestSearchContext:
    def test_defaults(self) -> None:
        ctx = SearchContext(raw_query="test", top_k=5, state={})
        assert ctx.raw_query == "test"
        assert ctx.all_chunks == []
        assert ctx.elapsed_ms >= 0

    def test_mutable(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=3, state={})
        ctx.corrected_query = "corrected"
        assert ctx.corrected_query == "corrected"


class TestSearchPipeline:
    async def test_empty_pipeline(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        pipeline = SearchPipeline(ctx)
        result = await pipeline.run()
        assert result.all_chunks == []

    async def test_single_stage(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        result = await SearchPipeline(ctx).add(_IncrementStage()).run()
        assert len(result.all_chunks) == 1

    async def test_multi_stage(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        result = await (
            SearchPipeline(ctx)
            .add(_IncrementStage())
            .add(_IncrementStage())
            .add(_IncrementStage())
            .run()
        )
        assert len(result.all_chunks) == 3

    async def test_stage_failure_continues(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        result = await (
            SearchPipeline(ctx)
            .add(_IncrementStage())
            .add(_FailStage())
            .add(_IncrementStage())
            .run()
        )
        # fail stage logs error but continues
        assert "fail" in result.stage_logs
        assert len(result.all_chunks) == 2

    async def test_builder_chaining(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        pipeline = SearchPipeline(ctx)
        returned = pipeline.add(_IncrementStage())
        assert returned is pipeline  # chaining

    def test_stage_count(self) -> None:
        ctx = SearchContext(raw_query="q", top_k=5, state={})
        pipeline = SearchPipeline(ctx).add(_IncrementStage()).add(_IncrementStage())
        assert pipeline.stage_count == 2

    def test_protocol_check(self) -> None:
        assert isinstance(_IncrementStage(), SearchStage)


# ==========================================================================
# Ingestion pipeline
# ==========================================================================


def _make_raw() -> RawDocument:
    return RawDocument(
        doc_id="d1", title="Test", content="Content " * 50,
        source_uri="test.txt",
    )


class _ChunkStage:
    name = "chunk"

    async def process(self, ctx: IngestionStageContext) -> IngestionStageContext:
        ctx.typed_chunks = [("chunk text", "body", "")]
        return ctx


class _EarlyExitStage:
    name = "dedup"

    async def process(self, ctx: IngestionStageContext) -> IngestionStageContext:
        ctx.result = IngestionResult(success=False, blocked=True, reason="duplicate")
        return ctx


class _IngestionFailStage:
    name = "fail"

    async def process(self, ctx: IngestionStageContext) -> IngestionStageContext:
        raise ValueError("embedding failed")


class TestIngestionContext:
    def test_defaults(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        assert ctx.should_stop is False
        assert ctx.typed_chunks == []

    def test_should_stop_after_result(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        ctx.result = IngestionResult(success=False, reason="blocked")
        assert ctx.should_stop is True


class TestIngestionPipelineRunner:
    async def test_empty_pipeline(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        result = await IngestionPipelineRunner(ctx).run()
        assert result.result is None

    async def test_single_stage(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        result = await IngestionPipelineRunner(ctx).add(_ChunkStage()).run()
        assert len(result.typed_chunks) == 1

    async def test_early_exit(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        result = await (
            IngestionPipelineRunner(ctx)
            .add(_EarlyExitStage())  # sets result → should_stop
            .add(_ChunkStage())     # should be skipped
            .run()
        )
        assert result.should_stop is True
        assert result.typed_chunks == []  # ChunkStage was skipped

    async def test_failure_stops_pipeline(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        result = await (
            IngestionPipelineRunner(ctx)
            .add(_IngestionFailStage())
            .add(_ChunkStage())
            .run()
        )
        assert result.should_stop is True
        assert "fail" in result.stage_logs

    async def test_builder_chaining(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        runner = IngestionPipelineRunner(ctx)
        returned = runner.add(_ChunkStage())
        assert returned is runner

    def test_stage_count(self) -> None:
        ctx = IngestionStageContext(raw=_make_raw(), collection_name="kb_test")
        runner = (
            IngestionPipelineRunner(ctx)
            .add(_ChunkStage())
            .add(_EarlyExitStage())
        )
        assert runner.stage_count == 2

    def test_protocol_check(self) -> None:
        assert isinstance(_ChunkStage(), IngestionStage)
