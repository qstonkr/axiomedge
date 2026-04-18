"""Data generation pipeline stages — concrete implementations.

각 stage 는 ``DataGenStage`` Protocol 을 구현한다. 기존 ``service.py::
generate_data_for_review()`` 의 6 단계를 1:1 로 wrap.

### Stage 목록 (실행 순서)

1. ``QAGenerationStage`` — usage_log + chunk 기반 QA pair 생성
2. ``GeneralityStage`` — 범용성 점수 부여
3. ``LegacyAugmentStage`` — 기존 augmentation (question_augmenter_count==0 일 때만)
4. ``IDAssignStage`` — 모든 row 에 id/profile_name/status/batch_id 부여
5. ``ReformatStage`` — Phase 1.5 answer reformatter
6. ``AugmentStage`` — Phase 1.5 question augmenter

### 의존성

- ``QAGenerationStage`` 는 ``DistillDataGenerator`` 를 필요로 함
- ``GeneralityStage`` 는 ``GeneralityFilter`` 를 필요로 함
- ``ReformatStage`` / ``AugmentStage`` 는 ``LLMHelper`` 를 필요로 함

이 의존성들은 각 stage 생성자에서 주입. Pipeline 조립은 ``service.py`` 에서.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.distill.pipeline.stages import DataGenContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. QA Generation
# ---------------------------------------------------------------------------


class QAGenerationStage:
    """Usage log + chunk 기반 QA pair 생성."""

    name = "qa_generation"

    def __init__(self, generator, session_factory, min_training_samples: int) -> None:
        self._gen = generator
        self._session_factory = session_factory
        self._min_samples = min_training_samples

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        log_qa = await self._gen.generate_from_usage_logs(
            self._session_factory, ctx.kb_ids, ctx.search_group,
        )
        chunk_qa: list[dict[str, Any]] = []
        if len(log_qa) < self._min_samples:
            chunk_qa = await self._gen.generate_from_chunks(
                ctx.kb_ids, max_chunks_per_kb=50,
            )
        ctx.rows = await self._gen.merge_and_deduplicate(log_qa, chunk_qa)
        ctx.stage_logs[self.name] = {
            "usage_log": len(log_qa),
            "chunk_qa": len(chunk_qa),
            "merged": len(ctx.rows),
        }
        return ctx


# ---------------------------------------------------------------------------
# 2. Generality
# ---------------------------------------------------------------------------


class GeneralityStage:
    """범용성 점수 부여."""

    name = "generality"

    def __init__(self, generality_filter) -> None:
        self._filter = generality_filter

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        ctx.rows = await self._filter.batch_score(ctx.rows)
        return ctx


# ---------------------------------------------------------------------------
# 3. Legacy Augmentation (optional)
# ---------------------------------------------------------------------------


class LegacyAugmentStage:
    """기존 augmentation 경로 (Phase 1.5 augmenter 비활성 시만 동작).

    ``profile.data_quality.augmentation_count > 0`` AND
    ``profile.data_quality.question_augmenter_count == 0`` 일 때만 실행.
    """

    name = "legacy_augment"

    def __init__(self, generator) -> None:
        self._gen = generator

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        p = ctx.profile.data_quality
        if p.augmentation_count > 0 and p.question_augmenter_count == 0:
            ctx.rows = await self._gen.augment_questions(ctx.rows)
            ctx.rows = await self._gen.dataset_builder.verify_augmented_questions(
                ctx.rows, self._gen.quality_filter,
            )
            ctx.stage_logs[self.name] = {"augmented": True}
        else:
            ctx.stage_logs[self.name] = {"skipped": True}
        return ctx


# ---------------------------------------------------------------------------
# 4. ID Assignment
# ---------------------------------------------------------------------------


class IDAssignStage:
    """모든 row 에 id / profile_name / status / batch_id 부여.

    Phase 1.5 reformatter 가 ``augmented_from`` 으로 원본 id 를 참조하므로
    reformat 호출 전에 반드시 실행돼야 함.
    """

    name = "id_assign"

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        for qa in ctx.rows:
            qa.setdefault("id", str(uuid.uuid4()))
            qa["profile_name"] = ctx.profile_name
            qa["status"] = "pending"
            qa["generation_batch_id"] = ctx.batch_id
        return ctx


# ---------------------------------------------------------------------------
# 5. Answer Reformatter (Phase 1.5)
# ---------------------------------------------------------------------------


class ReformatStage:
    """원본 답변을 1B 친화 2문단 포맷으로 재작성.

    ``profile.data_quality.reformat_enabled`` 가 True 일 때만 실행.
    원본 row 는 건드리지 않고 ``ctx.reformatted_rows`` 에 새 행 추가.
    """

    name = "reformat"

    def __init__(self, llm_helper, concurrency: int = 4) -> None:
        self._llm = llm_helper
        self._concurrency = concurrency

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        if not ctx.profile.data_quality.reformat_enabled:
            ctx.stage_logs[self.name] = {"skipped": True}
            return ctx

        from src.distill.data_gen.reformatter import (
            AnswerReformatter,
            build_reformatted_row,
        )
        reformatter = AnswerReformatter(self._llm, concurrency=self._concurrency)
        summary, results = await reformatter.reformat_batch(ctx.rows)

        id_to_qa = {qa["id"]: qa for qa in ctx.rows}
        for r in results:
            if r.success and r.reformatted_answer:
                parent = id_to_qa.get(r.source_id)
                if parent is None:
                    continue
                ctx.reformatted_rows.append(
                    build_reformatted_row(
                        parent, r.reformatted_answer, ctx.profile_name, ctx.batch_id,
                    ),
                )

        ctx.stage_logs[self.name] = {
            "success": summary.success,
            "total": summary.total,
            "avg_len": summary.avg_answer_len,
            "failures": summary.failure_reasons,
        }
        return ctx


# ---------------------------------------------------------------------------
# 6. Question Augmenter (Phase 1.5)
# ---------------------------------------------------------------------------


class AugmentStage:
    """질문 paraphrase 생성.

    ``profile.data_quality.question_augmenter_count > 0`` 일 때만 실행.
    reformatted_rows 가 있으면 그것을 parent 로, 없으면 원본 rows 를 parent 로.
    """

    name = "augment"

    def __init__(self, llm_helper, n_variations: int, concurrency: int = 4, verify: bool = True) -> None:
        self._llm = llm_helper
        self._n = n_variations
        self._concurrency = concurrency
        self._verify = verify

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        if ctx.profile.data_quality.question_augmenter_count <= 0:
            ctx.stage_logs[self.name] = {"skipped": True}
            return ctx

        from src.distill.data_gen.question_augmenter import (
            QuestionAugmenter,
            build_augmented_row,
        )
        parents = ctx.reformatted_rows if ctx.reformatted_rows else ctx.rows
        augmenter = QuestionAugmenter(
            self._llm,
            n_variations=self._n,
            concurrency=self._concurrency,
            verify=self._verify,
        )
        aug_summary, aug_results = await augmenter.augment_batch(parents)

        id_to_parent = {p["id"]: p for p in parents}
        for r in aug_results:
            parent = id_to_parent.get(r.source_id)
            if parent is None:
                continue
            for new_q in r.variations:
                ctx.augmented_rows.append(
                    build_augmented_row(parent, new_q, ctx.profile_name, ctx.batch_id),
                )

        ctx.stage_logs[self.name] = {
            "success": aug_summary.success,
            "total": aug_summary.total,
            "variations": aug_summary.total_variations_generated,
            "verified": aug_summary.total_variations_verified,
            "rejected": aug_summary.total_variations_rejected,
            "failures": aug_summary.failure_reasons,
        }
        return ctx
