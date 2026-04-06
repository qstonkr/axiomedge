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

        answers = []
        for _ in range(n):
            answer = await self.llm.call(prompt, temperature=0.7)
            if answer:
                answers.append(answer)

        if len(answers) < 2:
            return None

        # 임베딩 cosine similarity
        try:
            result = await asyncio.to_thread(
                self.embedder.encode, answers, return_dense=True,
                return_sparse=False, return_colbert_vecs=False,
            )
            vecs = np.array(result["dense_vecs"])

            similarities = []
            for i in range(len(vecs)):
                for j in range(i + 1, len(vecs)):
                    cos_sim = float(
                        np.dot(vecs[i], vecs[j])
                        / (np.linalg.norm(vecs[i]) * np.linalg.norm(vecs[j]) + 1e-8)
                    )
                    similarities.append(cos_sim)

            avg_sim = float(np.mean(similarities))
        except Exception as e:
            logger.warning("Embedding similarity failed, falling back to fuzz: %s", e)
            similarities = []
            for i in range(len(answers)):
                for j in range(i + 1, len(answers)):
                    similarities.append(fuzz.token_sort_ratio(answers[i], answers[j]) / 100.0)
            avg_sim = sum(similarities) / len(similarities) if similarities else 0

        if avg_sim < threshold:
            return None

        # Centroid에 가장 가까운 답변 선택
        try:
            centroid = np.mean(vecs, axis=0)
            best_idx = int(np.argmax([
                np.dot(v, centroid) / (np.linalg.norm(v) * np.linalg.norm(centroid) + 1e-8)
                for v in vecs
            ]))
        except Exception:
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
