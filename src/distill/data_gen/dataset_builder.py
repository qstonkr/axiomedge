"""데이터셋 빌드 — 중복 제거, augmentation, 밸런싱, JSONL export."""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.distill.config import DistillProfile
from src.distill.data_gen.dedup import GlobalDeduper
from src.distill.data_gen.llm_helper import LLMHelper

SOURCE_TYPE_AUG_SUFFIX = "_aug"  # augmented 소스 타입 접미사

logger = logging.getLogger(__name__)


class DatasetBuilder:
    """QA 데이터 후처리: 중복 제거 + augmentation + 밸런싱 + export."""

    def __init__(self, llm_helper: LLMHelper, profile: DistillProfile) -> None:
        self.llm = llm_helper
        self.profile = profile

    async def merge_and_deduplicate(
        self, *data_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """여러 소스의 QA 데이터를 병합하고 전역 dedup (MinHash LSH).

        이전: ``seen_questions.values()[-200:]`` 슬라이싱으로 마지막 200개만
        비교 — 멀리 떨어진 paraphrase 가 train/eval 양쪽에 들어가서 데이터
        누수 발생. 새 GlobalDeduper 는 전 데이터 LSH 검사.
        """
        merged: list[dict[str, Any]] = []
        for source in data_sources:
            merged.extend(source)

        if not merged:
            return merged

        deduper = GlobalDeduper(threshold=0.85)
        unique: list[dict[str, Any]] = []
        for qa in merged:
            if deduper.add({
                "question": qa.get("question", ""),
                "answer": qa.get("answer", ""),
            }):
                unique.append(qa)

        logger.info("Deduplicated (LSH): %d → %d QA pairs", len(merged), len(unique))
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
            from src.nlp.llm.prompt_safety import safe_user_input
            prompt = (
                f"다음 질문을 {n}가지 다른 표현으로 바꿔주세요. "
                "의미는 같게, 편의점 직원이 실제로 물어볼 법한 구어체로.\n\n"
                f"{safe_user_input('원본 질문', question)}\n\n"
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
                        "source_type": qa.get("source_type", "chunk_qa") + SOURCE_TYPE_AUG_SUFFIX,
                        "augmented_from": qa.get("id", ""),
                    })
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("Augmentation failed for '%s': %s", question[:30], e)

        logger.info("Augmented: %d → %d QA pairs", len(qa_pairs), len(augmented))
        return augmented

    async def verify_augmented_questions(
        self,
        qa_pairs: list[dict[str, Any]],
        quality_filter,
        threshold: float = 0.75,
        max_concurrency: int = 10,
    ) -> list[dict[str, Any]]:
        """변형 질문의 답변 일관성 검증 (병렬 처리).

        변형 질문을 Teacher에게 다시 질의 → 원본 답변과 cosine sim 비교.
        threshold 미달 시 탈락.
        """
        import asyncio

        originals = [qa for qa in qa_pairs if not qa.get("augmented_from")]
        augmented = [qa for qa in qa_pairs if qa.get("augmented_from")]

        sem = asyncio.Semaphore(max_concurrency)

        async def _verify_one(qa: dict) -> dict | None:
            async with sem:
                try:
                    from src.nlp.llm.prompt_safety import safe_user_input
                    teacher_answer = await self.llm.call(
                        f"다음 질문에 답변하세요:\n{safe_user_input('질문', qa['question'])}",
                        temperature=0.1,
                    )
                    if not teacher_answer:
                        return None
                    sim = await quality_filter.compute_similarity(
                        teacher_answer, qa["answer"],
                    )
                    qa["augmentation_verified"] = sim >= threshold
                    return qa if qa["augmentation_verified"] else None
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.warning("Augmentation verify failed: %s", e)
                    return None

        results = await asyncio.gather(*[_verify_one(qa) for qa in augmented])
        verified_aug = [r for r in results if r is not None]

        logger.info(
            "Augmentation verification: %d passed, %d dropped (threshold=%.2f)",
            len(verified_aug), len(augmented) - len(verified_aug), threshold,
        )
        return originals + verified_aug

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
