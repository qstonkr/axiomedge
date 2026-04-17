"""Coverage backfill — data_gen_stages.py concrete stage implementations.

Tests QAGenerationStage, LegacyAugmentStage, ReformatStage, AugmentStage.
IDAssignStage and GeneralityStage already covered in test_data_gen_pipeline.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.distill.pipeline.data_gen_stages import (
    AugmentStage,
    LegacyAugmentStage,
    QAGenerationStage,
    ReformatStage,
)
from src.distill.pipeline.stages import DataGenContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(
    rows: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> DataGenContext:
    defaults: dict[str, Any] = {
        "profile_name": "test-profile",
        "profile": _make_profile(),
        "batch_id": "batch-99",
        "kb_ids": ["kb1"],
        "search_group": "TEST",
        "rows": rows or [],
    }
    defaults.update(overrides)
    return DataGenContext(**defaults)


@dataclass
class _FakeDataQuality:
    augmentation_count: int = 0
    question_augmenter_count: int = 0
    reformat_enabled: bool = False


def _make_profile(
    augmentation_count: int = 0,
    question_augmenter_count: int = 0,
    reformat_enabled: bool = False,
) -> MagicMock:
    profile = MagicMock()
    profile.data_quality = _FakeDataQuality(
        augmentation_count=augmentation_count,
        question_augmenter_count=question_augmenter_count,
        reformat_enabled=reformat_enabled,
    )
    return profile


# ---------------------------------------------------------------------------
# QAGenerationStage
# ---------------------------------------------------------------------------

class TestQAGenerationStage:
    @pytest.mark.asyncio
    async def test_usage_log_only_when_enough_samples(self) -> None:
        """log_qa >= min_samples -> skip chunk generation."""
        gen = MagicMock()
        gen.generate_from_usage_logs = AsyncMock(
            return_value=[{"q": "Q1", "a": "A1"}] * 10,
        )
        gen.generate_from_chunks = AsyncMock(return_value=[])
        gen.merge_and_deduplicate = AsyncMock(
            return_value=[{"q": "Q1", "a": "A1"}] * 10,
        )

        stage = QAGenerationStage(
            gen, session_factory=MagicMock(), min_training_samples=5,
        )
        ctx = _make_ctx()
        result = await stage.process(ctx)

        gen.generate_from_usage_logs.assert_awaited_once()
        gen.generate_from_chunks.assert_not_awaited()
        assert len(result.rows) == 10
        assert result.stage_logs["qa_generation"]["usage_log"] == 10
        assert result.stage_logs["qa_generation"]["chunk_qa"] == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_chunks(self) -> None:
        """log_qa < min_samples -> also call generate_from_chunks."""
        gen = MagicMock()
        gen.generate_from_usage_logs = AsyncMock(
            return_value=[{"q": "Q1", "a": "A1"}],
        )
        gen.generate_from_chunks = AsyncMock(
            return_value=[{"q": "Q2", "a": "A2"}] * 5,
        )
        gen.merge_and_deduplicate = AsyncMock(
            return_value=[{"q": "Q1", "a": "A1"}] * 6,
        )

        stage = QAGenerationStage(
            gen, session_factory=MagicMock(), min_training_samples=5,
        )
        ctx = _make_ctx()
        result = await stage.process(ctx)

        gen.generate_from_chunks.assert_awaited_once()
        assert result.stage_logs["qa_generation"]["chunk_qa"] == 5
        assert result.stage_logs["qa_generation"]["merged"] == 6

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        gen = MagicMock()
        gen.generate_from_usage_logs = AsyncMock(return_value=[])
        gen.generate_from_chunks = AsyncMock(return_value=[])
        gen.merge_and_deduplicate = AsyncMock(return_value=[])

        stage = QAGenerationStage(
            gen, session_factory=MagicMock(), min_training_samples=5,
        )
        ctx = _make_ctx()
        result = await stage.process(ctx)

        assert result.rows == []
        assert result.stage_logs["qa_generation"]["merged"] == 0


# ---------------------------------------------------------------------------
# LegacyAugmentStage
# ---------------------------------------------------------------------------

class TestLegacyAugmentStage:
    @pytest.mark.asyncio
    async def test_runs_when_legacy_conditions_met(self) -> None:
        """augmentation_count > 0 AND question_augmenter_count == 0."""
        gen = MagicMock()
        gen.augment_questions = AsyncMock(
            return_value=[{"q": "Q", "a": "A", "aug": True}],
        )
        gen.dataset_builder.verify_augmented_questions = AsyncMock(
            return_value=[{"q": "Q", "a": "A", "aug": True}],
        )

        profile = _make_profile(
            augmentation_count=3, question_augmenter_count=0,
        )
        ctx = _make_ctx(
            rows=[{"q": "Q", "a": "A"}],
            profile=profile,
        )

        stage = LegacyAugmentStage(gen)
        result = await stage.process(ctx)

        gen.augment_questions.assert_awaited_once()
        gen.dataset_builder.verify_augmented_questions.assert_awaited_once()
        assert result.stage_logs["legacy_augment"]["augmented"] is True

    @pytest.mark.asyncio
    async def test_skips_when_augmentation_zero(self) -> None:
        gen = MagicMock()
        profile = _make_profile(
            augmentation_count=0, question_augmenter_count=0,
        )
        ctx = _make_ctx(profile=profile)

        result = await LegacyAugmentStage(gen).process(ctx)
        assert result.stage_logs["legacy_augment"]["skipped"] is True

    @pytest.mark.asyncio
    async def test_skips_when_new_augmenter_active(self) -> None:
        """question_augmenter_count > 0 -> skip legacy."""
        gen = MagicMock()
        profile = _make_profile(
            augmentation_count=3, question_augmenter_count=2,
        )
        ctx = _make_ctx(profile=profile)

        result = await LegacyAugmentStage(gen).process(ctx)
        assert result.stage_logs["legacy_augment"]["skipped"] is True


# ---------------------------------------------------------------------------
# ReformatStage
# ---------------------------------------------------------------------------

class TestReformatStage:
    @pytest.mark.asyncio
    async def test_skips_when_disabled(self) -> None:
        profile = _make_profile(reformat_enabled=False)
        ctx = _make_ctx(profile=profile)

        stage = ReformatStage(llm_helper=MagicMock())
        result = await stage.process(ctx)

        assert result.stage_logs["reformat"]["skipped"] is True
        assert result.reformatted_rows == []

    @pytest.mark.asyncio
    async def test_reformats_rows(self) -> None:
        profile = _make_profile(reformat_enabled=True)
        rows = [
            {"id": "row-1", "question": "Q1", "answer": "A1"},
            {"id": "row-2", "question": "Q2", "answer": "A2"},
        ]
        ctx = _make_ctx(rows=rows, profile=profile)

        # Mock AnswerReformatter
        mock_summary = MagicMock()
        mock_summary.success = 2
        mock_summary.total = 2
        mock_summary.avg_answer_len = 50.0
        mock_summary.failure_reasons = {}

        mock_result_1 = MagicMock()
        mock_result_1.success = True
        mock_result_1.reformatted_answer = "Reformatted A1"
        mock_result_1.source_id = "row-1"

        mock_result_2 = MagicMock()
        mock_result_2.success = False
        mock_result_2.reformatted_answer = None
        mock_result_2.source_id = "row-2"

        mock_reformatter = MagicMock()
        mock_reformatter.reformat_batch = AsyncMock(
            return_value=(mock_summary, [mock_result_1, mock_result_2]),
        )

        mock_build_row = MagicMock(
            return_value={"id": "ref-1", "question": "Q1", "answer": "Reformatted A1"},
        )

        with patch(
            "src.distill.data_gen.reformatter.AnswerReformatter",
            return_value=mock_reformatter,
        ), patch(
            "src.distill.data_gen.reformatter.build_reformatted_row",
            mock_build_row,
        ):
            stage = ReformatStage(llm_helper=MagicMock(), concurrency=2)
            result = await stage.process(ctx)

        assert len(result.reformatted_rows) == 1
        assert result.reformatted_rows[0]["answer"] == "Reformatted A1"
        assert result.stage_logs["reformat"]["success"] == 2
        assert result.stage_logs["reformat"]["total"] == 2

    @pytest.mark.asyncio
    async def test_skips_unknown_source_id(self) -> None:
        """Result with source_id not in rows is silently skipped."""
        profile = _make_profile(reformat_enabled=True)
        rows = [{"id": "row-1", "question": "Q1", "answer": "A1"}]
        ctx = _make_ctx(rows=rows, profile=profile)

        mock_summary = MagicMock(
            success=1, total=1, avg_answer_len=40.0, failure_reasons={},
        )
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.reformatted_answer = "New"
        mock_result.source_id = "nonexistent-id"

        mock_reformatter = MagicMock()
        mock_reformatter.reformat_batch = AsyncMock(
            return_value=(mock_summary, [mock_result]),
        )

        with patch(
            "src.distill.data_gen.reformatter.AnswerReformatter",
            return_value=mock_reformatter,
        ), patch(
            "src.distill.data_gen.reformatter.build_reformatted_row",
        ):
            stage = ReformatStage(llm_helper=MagicMock())
            result = await stage.process(ctx)

        assert len(result.reformatted_rows) == 0


# ---------------------------------------------------------------------------
# AugmentStage
# ---------------------------------------------------------------------------

class TestAugmentStage:
    @pytest.mark.asyncio
    async def test_skips_when_count_zero(self) -> None:
        profile = _make_profile(question_augmenter_count=0)
        ctx = _make_ctx(profile=profile)

        stage = AugmentStage(
            llm_helper=MagicMock(), n_variations=3,
        )
        result = await stage.process(ctx)

        assert result.stage_logs["augment"]["skipped"] is True
        assert result.augmented_rows == []

    @pytest.mark.asyncio
    async def test_augments_from_rows(self) -> None:
        """No reformatted_rows -> uses ctx.rows as parents."""
        profile = _make_profile(question_augmenter_count=2)
        rows = [{"id": "r1", "question": "Q1", "answer": "A1"}]
        ctx = _make_ctx(rows=rows, profile=profile)

        mock_summary = MagicMock()
        mock_summary.success = 1
        mock_summary.total = 1
        mock_summary.total_variations_generated = 2
        mock_summary.total_variations_verified = 2
        mock_summary.total_variations_rejected = 0
        mock_summary.failure_reasons = {}

        mock_result = MagicMock()
        mock_result.source_id = "r1"
        mock_result.variations = ["Q1-var1", "Q1-var2"]

        mock_augmenter = MagicMock()
        mock_augmenter.augment_batch = AsyncMock(
            return_value=(mock_summary, [mock_result]),
        )

        mock_build_row = MagicMock(
            side_effect=lambda p, q, pn, bi: {
                "id": f"aug-{q}",
                "question": q,
                "answer": p["answer"],
            },
        )

        with patch(
            "src.distill.data_gen.question_augmenter.QuestionAugmenter",
            return_value=mock_augmenter,
        ), patch(
            "src.distill.data_gen.question_augmenter.build_augmented_row",
            mock_build_row,
        ):
            stage = AugmentStage(
                llm_helper=MagicMock(),
                n_variations=2,
                concurrency=1,
                verify=True,
            )
            result = await stage.process(ctx)

        assert len(result.augmented_rows) == 2
        assert result.stage_logs["augment"]["variations"] == 2

    @pytest.mark.asyncio
    async def test_prefers_reformatted_as_parents(self) -> None:
        """When reformatted_rows exist, use them as parents."""
        profile = _make_profile(question_augmenter_count=1)
        rows = [{"id": "r1", "question": "Q1", "answer": "A1"}]
        reformatted = [
            {"id": "ref1", "question": "Q1", "answer": "Ref-A1"},
        ]
        ctx = _make_ctx(rows=rows, profile=profile)
        ctx.reformatted_rows = reformatted

        mock_summary = MagicMock(
            success=1, total=1,
            total_variations_generated=1,
            total_variations_verified=1,
            total_variations_rejected=0,
            failure_reasons={},
        )
        mock_result = MagicMock()
        mock_result.source_id = "ref1"
        mock_result.variations = ["Q1-v1"]

        mock_augmenter = MagicMock()
        mock_augmenter.augment_batch = AsyncMock(
            return_value=(mock_summary, [mock_result]),
        )

        mock_build_row = MagicMock(
            side_effect=lambda p, q, pn, bi: {
                "id": f"aug-{q}", "question": q, "answer": p["answer"],
            },
        )

        with patch(
            "src.distill.data_gen.question_augmenter.QuestionAugmenter",
            return_value=mock_augmenter,
        ), patch(
            "src.distill.data_gen.question_augmenter.build_augmented_row",
            mock_build_row,
        ):
            stage = AugmentStage(
                llm_helper=MagicMock(), n_variations=1,
            )
            result = await stage.process(ctx)

        # augment_batch should receive reformatted, not original rows
        call_args = mock_augmenter.augment_batch.call_args[0]
        assert call_args[0] is reformatted
        assert len(result.augmented_rows) == 1
        assert result.augmented_rows[0]["answer"] == "Ref-A1"

    @pytest.mark.asyncio
    async def test_skips_unknown_parent(self) -> None:
        """Result with unmatched source_id produces no augmented rows."""
        profile = _make_profile(question_augmenter_count=1)
        rows = [{"id": "r1", "question": "Q", "answer": "A"}]
        ctx = _make_ctx(rows=rows, profile=profile)

        mock_summary = MagicMock(
            success=1, total=1,
            total_variations_generated=1,
            total_variations_verified=1,
            total_variations_rejected=0,
            failure_reasons={},
        )
        mock_result = MagicMock()
        mock_result.source_id = "unknown-id"
        mock_result.variations = ["Q-v1"]

        mock_augmenter = MagicMock()
        mock_augmenter.augment_batch = AsyncMock(
            return_value=(mock_summary, [mock_result]),
        )

        with patch(
            "src.distill.data_gen.question_augmenter.QuestionAugmenter",
            return_value=mock_augmenter,
        ), patch(
            "src.distill.data_gen.question_augmenter.build_augmented_row",
        ):
            stage = AugmentStage(
                llm_helper=MagicMock(), n_variations=1,
            )
            result = await stage.process(ctx)

        assert len(result.augmented_rows) == 0
