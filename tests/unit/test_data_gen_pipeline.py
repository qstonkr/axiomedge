"""Tests for src/distill/pipeline/ — Stage Protocol + Pipeline (PR10)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.distill.pipeline.stages import DataGenContext, DataGenPipeline, make_context


# ---------------------------------------------------------------------------
# make_context
# ---------------------------------------------------------------------------


class TestMakeContext:
    def test_auto_generates_batch_id(self):
        profile = MagicMock()
        ctx = make_context("pbu", profile, ["kb1"], "PBU")
        assert len(ctx.batch_id) == 36  # uuid4 format

    def test_explicit_batch_id(self):
        profile = MagicMock()
        ctx = make_context("pbu", profile, ["kb1"], "PBU", batch_id="test-123")
        assert ctx.batch_id == "test-123"

    def test_empty_rows(self):
        profile = MagicMock()
        ctx = make_context("pbu", profile, [], "PBU")
        assert ctx.rows == []
        assert ctx.reformatted_rows == []
        assert ctx.augmented_rows == []


# ---------------------------------------------------------------------------
# DataGenPipeline
# ---------------------------------------------------------------------------


class _FakeStage:
    """Test double — rows 에 marker 추가."""

    def __init__(self, name: str, marker: str):
        self.name = name
        self._marker = marker

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        ctx.rows.append({"marker": self._marker})
        return ctx


class _FailingStage:
    """Test double — process 에서 exception raise."""

    name = "failing"

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        raise RuntimeError("intentional failure")


class TestDataGenPipeline:
    def _make_ctx(self) -> DataGenContext:
        return DataGenContext(
            profile_name="test",
            profile=MagicMock(),
            batch_id="batch-1",
            kb_ids=["kb1"],
            search_group="TEST",
        )

    @pytest.mark.asyncio
    async def test_runs_stages_in_order(self):
        ctx = self._make_ctx()
        pipeline = (
            DataGenPipeline(ctx)
            .add(_FakeStage("s1", "first"))
            .add(_FakeStage("s2", "second"))
            .add(_FakeStage("s3", "third"))
        )
        result = await pipeline.run()
        markers = [r["marker"] for r in result.rows]
        assert markers == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_failing_stage_continues(self):
        """실패한 stage 는 skip + 로그, 나머지 stage 는 계속 진행 (fail-open)."""
        ctx = self._make_ctx()
        pipeline = (
            DataGenPipeline(ctx)
            .add(_FakeStage("s1", "ok1"))
            .add(_FailingStage())
            .add(_FakeStage("s3", "ok3"))
        )
        result = await pipeline.run()
        markers = [r["marker"] for r in result.rows]
        assert markers == ["ok1", "ok3"]
        assert "error" in result.stage_logs.get("failing", {})

    @pytest.mark.asyncio
    async def test_empty_pipeline_returns_ctx(self):
        ctx = self._make_ctx()
        result = await DataGenPipeline(ctx).run()
        assert result is ctx
        assert result.rows == []

    @pytest.mark.asyncio
    async def test_chaining_add(self):
        ctx = self._make_ctx()
        pipeline = DataGenPipeline(ctx)
        returned = pipeline.add(_FakeStage("s1", "x"))
        assert returned is pipeline  # builder chaining


# ---------------------------------------------------------------------------
# IDAssignStage
# ---------------------------------------------------------------------------


class TestIDAssignStage:
    @pytest.mark.asyncio
    async def test_assigns_fields(self):
        from src.distill.pipeline.data_gen_stages import IDAssignStage

        ctx = DataGenContext(
            profile_name="pbu",
            profile=MagicMock(),
            batch_id="batch-42",
            kb_ids=["kb1"],
            search_group="PBU",
            rows=[{"question": "Q", "answer": "A"}],
        )
        result = await IDAssignStage().process(ctx)
        row = result.rows[0]
        assert row["profile_name"] == "pbu"
        assert row["status"] == "pending"
        assert row["generation_batch_id"] == "batch-42"
        assert len(row["id"]) == 36  # uuid4

    @pytest.mark.asyncio
    async def test_preserves_existing_id(self):
        from src.distill.pipeline.data_gen_stages import IDAssignStage

        ctx = DataGenContext(
            profile_name="pbu",
            profile=MagicMock(),
            batch_id="batch-42",
            kb_ids=[],
            search_group="PBU",
            rows=[{"id": "keep-me", "question": "Q", "answer": "A"}],
        )
        result = await IDAssignStage().process(ctx)
        assert result.rows[0]["id"] == "keep-me"


# ---------------------------------------------------------------------------
# GeneralityStage
# ---------------------------------------------------------------------------


class TestGeneralityStage:
    @pytest.mark.asyncio
    async def test_delegates_to_filter(self):
        from unittest.mock import AsyncMock

        from src.distill.pipeline.data_gen_stages import GeneralityStage

        mock_filter = MagicMock()
        mock_filter.batch_score = AsyncMock(return_value=[{"q": "Q", "generality_score": 0.9}])

        ctx = DataGenContext(
            profile_name="p",
            profile=MagicMock(),
            batch_id="b",
            kb_ids=[],
            search_group="S",
            rows=[{"q": "Q"}],
        )
        result = await GeneralityStage(mock_filter).process(ctx)
        mock_filter.batch_score.assert_awaited_once()
        assert result.rows[0]["generality_score"] == 0.9
