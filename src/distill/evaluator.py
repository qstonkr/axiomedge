# pyright: reportMissingImports=false
"""모델 평가 — Teacher judge + 임베딩 유사도.

학습된 모델의 품질을 평가하고 배포 게이트로 사용.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import numpy as np

from src.distill.config import EvalThreshold

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    passed: bool
    faithfulness: float
    relevancy: float
    avg_similarity: float
    sample_count: int
    details: list[dict] = field(default_factory=list)


class DistillEvaluator:
    """학습된 모델을 eval set으로 평가."""

    def __init__(self, teacher_llm, embedder) -> None:
        self.teacher = teacher_llm
        self.embedder = embedder

    async def evaluate(
        self,
        model_path: str,
        eval_data: list[dict],
        threshold: EvalThreshold,
    ) -> EvalResult:
        """학습 모델 평가: Teacher judge + 임베딩 유사도."""
        from llama_cpp import Llama

        logger.info("Loading student model for evaluation: %s", model_path)
        # 결정적 평가 — temperature=0.0 + seed 고정으로 같은 빌드 재평가 시 동일 점수
        student = Llama(
            model_path=model_path, n_ctx=512, n_threads=4, verbose=False, seed=42,
        )

        results = []
        for i, item in enumerate(eval_data):
            question = item["question"]
            expected = item["answer"]

            # Student 답변 생성 (temperature=0.0 결정성)
            try:
                output = student.create_chat_completion(
                    messages=[{"role": "user", "content": question}],
                    max_tokens=256,
                    temperature=0.0,
                )
                student_answer = output["choices"][0]["message"]["content"].strip()
            except (RuntimeError, OSError) as e:
                logger.warning("Student inference failed for Q%d: %s", i, e)
                student_answer = ""

            # Teacher Judge 평가
            judge_score = await self._teacher_judge(question, student_answer, expected)

            # 임베딩 유사도
            emb_sim = self._embedding_similarity(student_answer, expected)

            results.append({
                "question": question,
                "expected": expected[:200],
                "actual": student_answer[:200],
                "faithfulness": judge_score.get("faithfulness", 0),
                "relevancy": judge_score.get("relevancy", 0),
                "similarity": emb_sim,
            })

            if (i + 1) % 20 == 0:
                logger.info("Eval progress: %d/%d", i + 1, len(eval_data))

        del student

        # 집계
        if not results:
            return EvalResult(passed=False, faithfulness=0, relevancy=0,
                              avg_similarity=0, sample_count=0)

        avg_faith = sum(r["faithfulness"] for r in results) / len(results)
        avg_relev = sum(r["relevancy"] for r in results) / len(results)
        avg_sim = sum(r["similarity"] for r in results) / len(results)

        passed = avg_faith >= threshold.faithfulness and avg_relev >= threshold.relevancy

        logger.info(
            "Evaluation: faith=%.3f (≥%.2f), relev=%.3f (≥%.2f), sim=%.3f → %s",
            avg_faith, threshold.faithfulness,
            avg_relev, threshold.relevancy,
            avg_sim, "PASS" if passed else "FAIL",
        )

        return EvalResult(
            passed=passed,
            faithfulness=round(avg_faith, 4),
            relevancy=round(avg_relev, 4),
            avg_similarity=round(avg_sim, 4),
            sample_count=len(results),
            details=results,
        )

    async def _teacher_judge(
        self, question: str, student_answer: str, expected_answer: str,
    ) -> dict[str, float]:
        """Teacher LLM이 judge로서 학생 답변을 평가."""
        if not student_answer:
            return {"faithfulness": 0, "relevancy": 0}

        prompt = (
            "학생의 답변을 평가하세요. 0.0~1.0 점수로.\n\n"
            f"질문: {question}\n"
            f"기대 답변: {expected_answer[:500]}\n"
            f"학생 답변: {student_answer[:500]}\n\n"
            "평가 기준:\n"
            "- faithfulness: 학생 답변이 기대 답변과 사실적으로 일치하는가?\n"
            "- relevancy: 학생 답변이 질문에 적절히 답하는가?\n\n"
            'JSON으로 답하세요: {"faithfulness": 0.X, "relevancy": 0.X}'
        )

        try:
            if hasattr(self.teacher, "generate"):
                response = await self.teacher.generate(prompt, temperature=0.1)
            else:
                response = await self.teacher.generate_response(
                    query=prompt, context=[], system_prompt="",
                )

            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                scores = json.loads(response[start:end])
                return {
                    "faithfulness": float(scores.get("faithfulness", 0)),
                    "relevancy": float(scores.get("relevancy", 0)),
                }
        except (RuntimeError, json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as e:
            # teacher 호출 실패 / JSON 파싱 실패 / 점수 cast 실패 — fail-soft 0.5.
            # import json 을 try 안에 두면 teacher.generate 가 raise 시 except 의
            # json.JSONDecodeError 가 UnboundLocalError 로 폭주 (latent bug fix).
            logger.warning("Teacher judge failed: %s", e)

        return {"faithfulness": 0.5, "relevancy": 0.5}

    def _embedding_similarity(self, text1: str, text2: str) -> float:
        """임베딩 cosine similarity.

        Embedder 실패 시 0.0 반환 + warning. 평가 점수에 영향을 주므로
        silent 로 묻지 않고 호출자가 로그로 추적 가능해야 한다.
        """
        if not text1 or not text2:
            return 0.0
        try:
            result = self.embedder.encode(
                [text1, text2], return_dense=True,
                return_sparse=False, return_colbert_vecs=False,
            )
            vecs = result["dense_vecs"]
            if len(vecs) < 2:
                return 0.0
            v1, v2 = np.array(vecs[0]), np.array(vecs[1])
            return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8))
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "Embedding similarity failed for texts (%s / %s): %s",
                text1[:30], text2[:30], e,
            )
            return 0.0
