"""QA 학습 데이터 생성 — facade.

실제 구현은 ``data_gen/`` 패키지로 분리.
기존 import 호환: ``from src.distill.data_generator import DistillDataGenerator``
"""

from __future__ import annotations

import logging
from typing import Any

from src.distill.config import DistillProfile
from src.distill.data_gen.dataset_builder import DatasetBuilder
from src.distill.data_gen.llm_helper import LLMHelper
from src.distill.data_gen.qa_generator import QAGenerator
from src.distill.data_gen.quality_filter import QualityFilter

logger = logging.getLogger(__name__)


class DistillDataGenerator:
    """QA 데이터 생성 facade — 하위 모듈 조합.

    Usage:
        generator = DistillDataGenerator(llm_client, embedder, profile)
        qa = await generator.generate_from_chunks(kb_ids)
        qa = await generator.augment_questions(qa)
        qa = await generator.merge_and_deduplicate(qa, log_qa)
        count = generator.export_jsonl(qa, "train.jsonl")
    """

    def __init__(
        self,
        llm_client,
        embedder,
        profile: DistillProfile,
        qdrant_url: str = "http://localhost:6333",
    ):
        from src.config import get_settings
        settings = get_settings().distill

        self._llm_helper = LLMHelper(
            llm_client, qdrant_url, settings.llm_concurrency, settings.llm_timeout_sec,
        )
        self._quality = QualityFilter(self._llm_helper, embedder, profile)
        self._qa_gen = QAGenerator(self._llm_helper, self._quality, profile)
        self._builder = DatasetBuilder(self._llm_helper, profile)

    # Phase 1.5 reformatter/augmenter 가 같은 LLMHelper 를 재사용할 수 있도록
    # 공개. 새 인스턴스를 만들면 concurrency 카운트가 분리돼서 SageMaker 동시
    # 호출 한도를 초과할 수 있다.
    @property
    def llm_helper(self) -> LLMHelper:
        return self._llm_helper

    @property
    def quality_filter(self) -> QualityFilter:
        return self._quality

    @property
    def dataset_builder(self) -> DatasetBuilder:
        return self._builder

    # QA 생성 (delegate)
    async def generate_from_chunks(
        self, kb_ids: list[str], max_chunks_per_kb: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._qa_gen.generate_from_chunks(kb_ids, max_chunks_per_kb)

    async def generate_from_usage_logs(
        self, session_factory, kb_ids: list[str], group_name: str,
    ) -> list[dict[str, Any]]:
        return await self._qa_gen.generate_from_usage_logs(session_factory, kb_ids, group_name)

    # 데이터셋 빌드 (delegate)
    async def merge_and_deduplicate(
        self, *data_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await self._builder.merge_and_deduplicate(*data_sources)

    async def augment_questions(
        self, qa_pairs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await self._builder.augment_questions(qa_pairs)

    def balance_dataset(
        self, data: list[dict[str, Any]], max_per_type: int = 500,
    ) -> list[dict[str, Any]]:
        return self._builder.balance_dataset(data, max_per_type)

    def export_jsonl(self, data: list[dict[str, Any]], output_path: str) -> int:
        return self._builder.export_jsonl(data, output_path)
