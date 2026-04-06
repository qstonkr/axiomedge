"""데이터셋 빌드 — 중복 제거, augmentation, 밸런싱, JSONL export."""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from src.distill.data_gen.llm_helper import LLMHelper
from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


class DatasetBuilder:
    """QA 데이터 후처리: 중복 제거 + augmentation + 밸런싱 + export."""

    def __init__(self, llm_helper: LLMHelper, profile: DistillProfile):
        self.llm = llm_helper
        self.profile = profile

    async def merge_and_deduplicate(
        self, *data_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """여러 소스의 QA 데이터를 병합하고 중복 제거."""
        merged: list[dict[str, Any]] = []
        for source in data_sources:
            merged.extend(source)

        if not merged:
            return merged

        unique: list[dict[str, Any]] = []
        seen_questions: list[str] = []

        for qa in merged:
            q = qa["question"]
            is_dup = any(fuzz.token_sort_ratio(q, seen) > 85 for seen in seen_questions)
            if not is_dup:
                unique.append(qa)
                seen_questions.append(q)

        logger.info("Deduplicated: %d → %d QA pairs", len(merged), len(unique))
        return unique

    async def augment_questions(
        self, qa_pairs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """질문 paraphrase로 학습 데이터 증강."""
        n = self.profile.data_quality.augmentation_count
        if n <= 0:
            return qa_pairs

        augmented = list(qa_pairs)

        for qa in qa_pairs:
            question = qa["question"]
            prompt = (
                f"다음 질문을 {n}가지 다른 표현으로 바꿔주세요. "
                "의미는 같게, 편의점 직원이 실제로 물어볼 법한 구어체로.\n\n"
                f"원본: {question}\n\n"
                "다른 표현 (한 줄에 하나씩):"
            )
            try:
                result = await self.llm.call(prompt, temperature=0.8)
                variants = [
                    line.strip().lstrip("0123456789.-) ")
                    for line in result.split("\n")
                    if line.strip() and len(line.strip()) > 5
                ]
                for variant in variants[:n]:
                    augmented.append({
                        **qa,
                        "question": variant,
                        "source_type": qa.get("source_type", "chunk_qa") + "_aug",
                    })
            except Exception as e:
                logger.warning("Augmentation failed for '%s': %s", question[:30], e)

        logger.info("Augmented: %d → %d QA pairs", len(qa_pairs), len(augmented))
        return augmented

    @staticmethod
    def balance_dataset(
        data: list[dict[str, Any]], max_per_type: int = 500,
    ) -> list[dict[str, Any]]:
        """source_type별 균형 맞추기."""
        by_type: dict[str, list] = defaultdict(list)
        for item in data:
            by_type[item.get("source_type", "unknown")].append(item)

        balanced: list[dict[str, Any]] = []
        for src_type, items in by_type.items():
            if len(items) > max_per_type:
                items = random.sample(items, max_per_type)
            balanced.extend(items)
            logger.info("  %s: %d items", src_type, len(items))

        return balanced

    @staticmethod
    def export_jsonl(data: list[dict[str, Any]], output_path: str) -> int:
        """QA 데이터를 chat format JSONL로 저장."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for qa in data:
                entry = {
                    "messages": [
                        {"role": "user", "content": qa["question"]},
                        {"role": "assistant", "content": qa["answer"]},
                    ],
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                count += 1

        logger.info("Exported %d entries to %s", count, output_path)
        return count
