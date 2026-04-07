"""QA 품질 필터 — self-consistency, answer-only 변환, 길이 정규화."""

from __future__ import annotations

import asyncio
import logging

import numpy as np
from rapidfuzz import fuzz

from src.distill.config import ESTIMATED_CHARS_PER_TOKEN, DistillProfile
from src.distill.data_gen.llm_helper import LLMHelper

logger = logging.getLogger(__name__)


class QualityFilter:
    """QA 쌍 품질 필터링 및 변환."""

    def __init__(self, llm_helper: LLMHelper, embedder, profile: DistillProfile):
        self.llm = llm_helper
        self.embedder = embedder
        self.profile = profile

    async def compute_similarity(self, text_a: str, text_b: str) -> float:
        """두 텍스트 간 cosine similarity (임베딩 기반, fallback: fuzz)."""
        try:
            result = await asyncio.to_thread(
                self.embedder.encode, [text_a, text_b], return_dense=True,
                return_sparse=False, return_colbert_vecs=False,
            )
            vecs = np.array(result["dense_vecs"])
            return float(
                np.dot(vecs[0], vecs[1])
                / (np.linalg.norm(vecs[0]) * np.linalg.norm(vecs[1]) + 1e-8)
            )
        except Exception as e:
            logger.warning("Embedding similarity failed, falling back to fuzz: %s", e)
            return fuzz.token_sort_ratio(text_a, text_b) / 100.0

    async def self_consistency_filter(
        self, question: str, chunk: str,
    ) -> tuple[str, float] | None:
        """Teacher N회 응답 → 임베딩 cosine similarity로 일관성 검증."""
        n = self.profile.data_quality.self_consistency_samples
        threshold = self.profile.data_quality.self_consistency_threshold

        prompt = (
            f"다음 정보를 바탕으로 답변하세요.\n\n"
            f"[정보]\n{chunk[:1500]}\n\n"
            f"[질문]\n{question}"
        )

        results = await asyncio.gather(
            *[self.llm.call(prompt, temperature=0.7) for _ in range(n)]
        )
        answers = [a for a in results if a]

        if len(answers) < 2:
            return None

        # 임베딩 cosine similarity (compute_similarity 재사용)
        similarities = []
        for i in range(len(answers)):
            for j in range(i + 1, len(answers)):
                sim = await self.compute_similarity(answers[i], answers[j])
                similarities.append(sim)

        avg_sim = float(np.mean(similarities)) if similarities else 0

        if avg_sim < threshold:
            return None

        # Centroid에 가장 가까운 답변 선택
        try:
            result = await asyncio.to_thread(
                self.embedder.encode, answers, return_dense=True,
                return_sparse=False, return_colbert_vecs=False,
            )
            vecs = np.array(result["dense_vecs"])
            centroid = np.mean(vecs, axis=0)
            best_idx = int(np.argmax([
                np.dot(v, centroid) / (np.linalg.norm(v) * np.linalg.norm(centroid) + 1e-8)
                for v in vecs
            ]))
        except Exception as e:
            logger.warning("Centroid selection failed, using first answer: %s", e)
            best_idx = 0

        return answers[best_idx], avg_sim

    async def convert_to_answer_only(self, question: str, full_answer: str) -> str:
        """Teacher LLM으로 추론 과정 제거."""
        prompt = (
            "다음 답변에서 추론 과정('~이므로', '~를 확인해보면' 등)을 제거하고 "
            "핵심 답변만 간결하게 남겨주세요. 번호가 있는 절차형이면 그대로 유지하세요.\n\n"
            f"질문: {question}\n"
            f"원본 답변: {full_answer}\n\n"
            "간결한 답변:"
        )
        result = await self.llm.call(prompt, temperature=0.1)
        return result if result else full_answer

    async def normalize_answer_length(self, answer: str) -> str:
        """max_answer_tokens 초과 시 요약."""
        max_tokens = self.profile.qa_style.max_answer_tokens
        estimated_tokens = int(len(answer) / ESTIMATED_CHARS_PER_TOKEN)
        if estimated_tokens <= max_tokens:
            return answer

        prompt = (
            f"다음 답변을 {max_tokens}토큰(약 {max_tokens * 2}자) 이내로 "
            "핵심만 간결하게 요약하세요.\n\n"
            f"{answer}"
        )
        result = await self.llm.call(prompt, temperature=0.1)
        return result if result else answer[:max_tokens * 3]
