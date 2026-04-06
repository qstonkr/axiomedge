"""QA 학습 데이터 생성.

검색 그룹의 KB 청크에서 Teacher(EXAONE)로 QA 쌍을 생성.
Self-consistency 필터, 질문 augmentation, answer-only 변환 포함.

Usage:
    from src.distill.data_generator import DistillDataGenerator
    generator = DistillDataGenerator(llm_client, embedder, profile)
    qa_pairs = await generator.generate_from_chunks(kb_ids)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from rapidfuzz import fuzz

from src.distill.config import (
    ESTIMATED_CHARS_PER_TOKEN,
    MIN_CHUNK_LENGTH,
    DistillProfile,
)

logger = logging.getLogger(__name__)


class DistillDataGenerator:
    """RAG 데이터에서 Small LM 학습용 QA 쌍 생성."""

    def __init__(
        self,
        llm_client,
        embedder,
        profile: DistillProfile,
        qdrant_url: str = "http://localhost:6333",
    ):
        self.llm = llm_client
        self.embedder = embedder
        self.profile = profile
        self.qdrant_url = qdrant_url

        # LLM 동시 호출 제한
        from src.config import get_settings
        concurrency = get_settings().distill.llm_concurrency
        self._llm_semaphore = asyncio.Semaphore(concurrency)
        self._llm_timeout = get_settings().distill.llm_timeout_sec

    # =====================================================================
    # Chunk 기반 QA 생성
    # =====================================================================

    async def generate_from_chunks(
        self,
        kb_ids: list[str],
        max_chunks_per_kb: int = 200,
    ) -> list[dict[str, Any]]:
        """KB 청크에서 QA 쌍 생성."""
        all_qa: list[dict[str, Any]] = []

        for kb_id in kb_ids:
            logger.info("Generating QA from KB: %s", kb_id)
            chunks = await self._scroll_chunks(kb_id, limit=max_chunks_per_kb)
            logger.info("  Fetched %d chunks from %s", len(chunks), kb_id)

            for i, chunk in enumerate(chunks):
                content = chunk.get("content", "")
                if len(content) < MIN_CHUNK_LENGTH:
                    continue

                try:
                    qa_pairs = await self._generate_qa_from_chunk(content, kb_id)
                    all_qa.extend(qa_pairs)
                except Exception as e:
                    logger.warning("  Chunk %d failed: %s", i, e)

                if (i + 1) % 50 == 0:
                    logger.info("  Progress: %d/%d chunks, %d QA pairs", i + 1, len(chunks), len(all_qa))

        logger.info("Total QA pairs from chunks: %d", len(all_qa))
        return all_qa

    async def _generate_qa_from_chunk(
        self, content: str, kb_id: str,
    ) -> list[dict[str, Any]]:
        """단일 청크에서 QA 쌍 생성 (1~3개)."""
        prompt = (
            "다음 정보를 바탕으로 편의점 직원이 물어볼 법한 질문과 답변을 1~3개 만들어주세요.\n"
            "각 QA는 JSON 형식으로 작성하세요.\n\n"
            f"[정보]\n{content[:2000]}\n\n"
            '[출력 형식]\n[{"question": "...", "answer": "..."}]'
        )

        response = await self._call_llm(prompt, temperature=0.7)
        qa_pairs = self._parse_qa_json(response)

        results = []
        for qa in qa_pairs:
            question = qa.get("question", "").strip()
            answer = qa.get("answer", "").strip()
            if not question or not answer:
                continue

            # Self-consistency 필터
            if self.profile.data_quality.enable_self_consistency:
                filtered = await self._self_consistency_filter(question, content)
                if filtered is None:
                    continue
                answer, consistency_score = filtered
            else:
                consistency_score = 1.0

            # Answer-only 변환
            if self.profile.qa_style.answer_only_ratio > 0:
                if random.random() < self.profile.qa_style.answer_only_ratio:
                    answer = await self._convert_to_answer_only(question, answer)

            # 답변 길이 정규화
            answer = await self._normalize_answer_length(answer)

            results.append({
                "question": question,
                "answer": answer,
                "source_type": "chunk_qa",
                "kb_id": kb_id,
                "consistency_score": consistency_score,
            })

        return results

    # =====================================================================
    # Usage Log 기반 QA 추출
    # =====================================================================

    async def generate_from_usage_logs(
        self,
        session_factory,
        kb_ids: list[str],
        group_name: str,
    ) -> list[dict[str, Any]]:
        """usage_log에서 answer+chunks 포함 로그를 QA로 변환."""
        from sqlalchemy import text

        qa_pairs: list[dict[str, Any]] = []

        async with session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT knowledge_id, context
                    FROM knowledge_usage_logs
                    WHERE usage_type = 'hub_search'
                    AND context LIKE :group_filter
                    AND context LIKE '%"answer"%'
                    ORDER BY created_at DESC
                    LIMIT 5000
                """),
                {"group_filter": f'%"group_name": "{group_name}"%'},
            )
            rows = result.fetchall()

        logger.info("Found %d usage logs with answers for group %s", len(rows), group_name)

        for row in rows:
            query = row[0]
            try:
                ctx = json.loads(row[1]) if isinstance(row[1], str) else {}
            except (json.JSONDecodeError, TypeError):
                continue

            answer = ctx.get("answer", "")
            if not answer or not query:
                continue

            qa_pairs.append({
                "question": query,
                "answer": answer,
                "source_type": "usage_log",
                "kb_id": ",".join(kb_ids[:3]),
            })

        logger.info("Extracted %d QA pairs from usage logs", len(qa_pairs))
        return qa_pairs

    # =====================================================================
    # Self-Consistency 필터
    # =====================================================================

    async def _self_consistency_filter(
        self,
        question: str,
        chunk: str,
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
            answer = await self._call_llm(prompt, temperature=0.7)
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

    # =====================================================================
    # Answer-Only 변환
    # =====================================================================

    async def _convert_to_answer_only(self, question: str, full_answer: str) -> str:
        """Teacher LLM으로 추론 과정 제거, 핵심 답변만 남기기."""
        prompt = (
            "다음 답변에서 추론 과정('~이므로', '~를 확인해보면' 등)을 제거하고 "
            "핵심 답변만 간결하게 남겨주세요. 번호가 있는 절차형이면 그대로 유지하세요.\n\n"
            f"질문: {question}\n"
            f"원본 답변: {full_answer}\n\n"
            "간결한 답변:"
        )
        result = await self._call_llm(prompt, temperature=0.1)
        return result if result else full_answer

    # =====================================================================
    # 질문 Augmentation
    # =====================================================================

    async def augment_questions(
        self, qa_pairs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """질문 paraphrase로 학습 데이터 증강."""
        n = self.profile.data_quality.augmentation_count
        if n <= 0:
            return qa_pairs

        augmented = list(qa_pairs)  # 원본 유지

        for qa in qa_pairs:
            question = qa["question"]
            prompt = (
                f"다음 질문을 {n}가지 다른 표현으로 바꿔주세요. "
                "의미는 같게, 편의점 직원이 실제로 물어볼 법한 구어체로.\n\n"
                f"원본: {question}\n\n"
                "다른 표현 (한 줄에 하나씩):"
            )
            try:
                result = await self._call_llm(prompt, temperature=0.8)
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

    # =====================================================================
    # 답변 길이 정규화
    # =====================================================================

    async def _normalize_answer_length(self, answer: str) -> str:
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
        result = await self._call_llm(prompt, temperature=0.1)
        return result if result else answer[:max_tokens * 3]

    # =====================================================================
    # 병합 + 중복 제거 + 밸런싱
    # =====================================================================

    async def merge_and_deduplicate(
        self, *data_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """여러 소스의 QA 데이터를 병합하고 중복 제거."""
        merged = []
        for source in data_sources:
            merged.extend(source)

        if not merged:
            return merged

        # 질문 기준 중복 제거 (fuzz ratio 0.85 이상이면 중복)
        unique: list[dict[str, Any]] = []
        seen_questions: list[str] = []

        for qa in merged:
            q = qa["question"]
            is_dup = False
            for seen in seen_questions:
                if fuzz.token_sort_ratio(q, seen) > 85:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(qa)
                seen_questions.append(q)

        logger.info("Deduplicated: %d → %d QA pairs", len(merged), len(unique))
        return unique

    def balance_dataset(
        self, data: list[dict[str, Any]], max_per_type: int = 500,
    ) -> list[dict[str, Any]]:
        """source_type별 균형 맞추기."""
        by_type: dict[str, list] = defaultdict(list)
        for item in data:
            by_type[item.get("source_type", "unknown")].append(item)

        balanced = []
        for src_type, items in by_type.items():
            if len(items) > max_per_type:
                items = random.sample(items, max_per_type)
            balanced.extend(items)
            logger.info("  %s: %d items", src_type, len(items))

        return balanced

    # =====================================================================
    # JSONL 출력
    # =====================================================================

    def export_jsonl(
        self, data: list[dict[str, Any]], output_path: str,
    ) -> int:
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

    # =====================================================================
    # Qdrant 청크 스크롤
    # =====================================================================

    async def _scroll_chunks(
        self, kb_id: str, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Qdrant에서 KB의 청크를 스크롤."""
        import httpx

        chunks: list[dict[str, Any]] = []
        offset = None

        while len(chunks) < limit:
            body: dict[str, Any] = {
                "limit": min(100, limit - len(chunks)),
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self.qdrant_url}/collections/{kb_id}/points/scroll",
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json().get("result", {})
            except Exception as e:
                logger.warning("Qdrant scroll failed for %s: %s", kb_id, e)
                break

            points = data.get("points", [])
            for point in points:
                payload = point.get("payload", {})
                chunks.append({
                    "content": payload.get("content", ""),
                    "document_name": payload.get("document_name", ""),
                    "source_uri": payload.get("source_uri", ""),
                })

            offset = data.get("next_page_offset")
            if offset is None or not points:
                break

        return chunks

    # =====================================================================
    # LLM 호출 헬퍼
    # =====================================================================

    async def _call_llm(self, prompt: str, temperature: float = 0.7) -> str:
        """Teacher LLM 호출 (세마포어 + 타임아웃)."""
        async with self._llm_semaphore:
            try:
                coro = None
                if hasattr(self.llm, "generate"):
                    coro = self.llm.generate(prompt, temperature=temperature)
                elif hasattr(self.llm, "generate_response"):
                    coro = self.llm.generate_response(
                        query=prompt, context=[], system_prompt="",
                    )
                if coro is None:
                    return ""
                result = await asyncio.wait_for(coro, timeout=self._llm_timeout)
                return result if isinstance(result, str) else str(result)
            except asyncio.TimeoutError:
                logger.warning("LLM call timed out after %ds", self._llm_timeout)
                return ""
            except Exception as e:
                logger.warning("LLM call failed: %s", e)
            return ""

    @staticmethod
    def _parse_qa_json(response: str) -> list[dict[str, Any]]:
        """LLM 응답에서 QA JSON 파싱."""
        try:
            # JSON 배열 추출
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                from json_repair import repair_json
                repaired = repair_json(response[start:end])
                parsed = json.loads(repaired)
                if isinstance(parsed, list):
                    return parsed
        except Exception:
            pass

        # fallback: 줄 단위 파싱
        try:
            results = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    results.append(json.loads(line))
            return results
        except Exception:
            pass

        return []
