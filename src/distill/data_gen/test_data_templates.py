"""테스트 데이터 생성 — PBU 도메인 질문 템플릿 + KB 청크 기반 QA 생성.

SageMaker EXAONE을 Teacher LLM으로 사용하여 타당한 QA 쌍을 생성.
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# PBU 도메인 질문 템플릿 (카테고리별)
TEST_QUESTION_TEMPLATES: dict[str, list[str]] = {
    "상품관리": [
        "유통기한 지난 상품 폐기 절차 알려줘",
        "신상품 입고 시 처리 절차가 어떻게 되나요?",
        "1+1 행사 상품 POS 등록 방법",
        "상품 진열 기준과 원칙",
        "불량 상품 반품 처리 방법",
        "온도 관리가 필요한 상품 종류와 기준",
        "상품 가격 변경 시 POS 반영 방법",
        "재고 실사 절차와 주기",
    ],
    "POS/결제": [
        "카드 단말기 오류 대처 방법",
        "현금 시재 정산 방법",
        "무인 결제기 오류 대처법",
        "POS 시스템 재부팅 방법",
        "고객 포인트 적립 오류 해결 방법",
        "교통카드 충전 방법",
        "모바일 결제 오류 시 대처법",
        "영수증 재발행 방법",
    ],
    "매장운영": [
        "개점 시 오픈 절차",
        "폐점 시 마감 절차",
        "야간 근무 시 안전 수칙",
        "교대 근무 시 인수인계 사항",
        "냉장고 온도 이상 시 대처 방법",
        "매장 청소 및 위생 관리 기준",
        "방충/방서 관리 방법",
        "비상 상황(화재/정전) 대응 절차",
    ],
    "고객응대": [
        "고객 컴플레인 대응 매뉴얼",
        "고객 환불 규정과 절차",
        "담배 판매 시 연령 확인 절차",
        "배달 주문 접수 및 처리 방법",
        "택배 접수 방법",
        "주류 판매 시 주의사항",
        "고객 분실물 처리 절차",
        "장애인 고객 응대 지침",
    ],
}

QA_GENERATION_PROMPT = (
    "당신은 GS25 편의점 운영 전문가입니다. 다음 질문에 대해 실무에서 바로 활용할 수 있는 "
    "구체적이고 정확한 답변을 작성하세요.\n\n"
    "규칙:\n"
    "- 실제 편의점 운영 절차에 맞는 답변을 작성하세요\n"
    "- 번호가 있는 단계별 절차로 답변하세요\n"
    "- 특정 매장이나 날짜에 종속되지 않는 범용적 내용으로 작성하세요\n"
    "- 200자 이내로 간결하게 작성하세요\n\n"
    "{context}"
    "질문: {question}\n\n"
    "답변:"
)


# KB별 특화 프롬프트
KB_PROMPTS: dict[str, str] = {
    "g-espa": (
        "다음은 GS25 매장의 ESPA(매장 성과 분석) 자료입니다.\n"
        "이 자료에서 다루는 주제를 참고하되, 어느 GS25 매장에서든 적용 가능한 범용적 질문을 1~3개 만들어주세요.\n\n"
        "중요 규칙:\n"
        "- 특정 매장명, 점주명, 날짜, 숫자 데이터를 절대 포함하지 마세요\n"
        "- 문서의 구체적 사례가 아닌, 그 사례에서 배울 수 있는 일반적 노하우를 질문하세요\n"
        "- 질문은 짧고 간결하게 (한 문장)\n"
        "- 좋은 예: '육가공 매출 올리는 방법은?', '냉장 진열 효율 높이려면?', '객단가 올리는 전략은?'\n"
        "- 나쁜 예: '수지우남점 육가공 매출이 왜 떨어졌나요?', '60대 여성 고객층 대상 프로모션은?'\n\n"
        "[ESPA 자료]\n{chunk}\n\n"
        "범용적 질문 (한 줄에 하나씩):"
    ),
    "drp": (
        "다음은 GS25 가맹점 분쟁조정 답변서/사례입니다.\n"
        "이 자료에서 다루는 분쟁 유형을 참고하되, 어느 점주든 겪을 수 있는 범용적 질문을 1~3개 만들어주세요.\n\n"
        "중요 규칙:\n"
        "- 특정 매장명, 점주명, 사건번호, 날짜를 절대 포함하지 마세요\n"
        "- 구체적 사건이 아닌, 그 유형의 분쟁에 대한 일반적 절차/기준을 질문하세요\n"
        "- 질문은 짧고 간결하게 (한 문장)\n"
        "- 좋은 예: '임대료 분쟁 조정 절차는?', '계약 해지 시 정산 기준은?', '영업손실 보상 기준이 뭐야?'\n"
        "- 나쁜 예: 'GS25 세마역점 분쟁 결과는?', '하남감북점 위약금이 얼마야?'\n\n"
        "[분쟁조정 자료]\n{chunk}\n\n"
        "범용적 질문 (한 줄에 하나씩):"
    ),
    "a-ari": (
        "다음은 GS25 편의점 운영 매뉴얼/가이드 문서입니다.\n"
        "이 자료에서 다루는 주제를 참고하되, 어느 매장에서든 적용 가능한 범용적 질문을 1~3개 만들어주세요.\n\n"
        "중요 규칙:\n"
        "- 특정 매장명, 직원명, 날짜를 절대 포함하지 마세요\n"
        "- 문서의 세부 내용이 아닌, 핵심 절차/규정/방법을 묻는 질문을 만드세요\n"
        "- 질문은 짧고 간결하게 (한 문장)\n"
        "- 좋은 예: '폐기 절차 알려줘', '발주 시스템 사용법은?', '위생 점검 기준이 뭐야?'\n"
        "- 나쁜 예: '전자재계약 홈페이지에서 어떤 서류를 업로드해야 하나요?'\n\n"
        "[운영 매뉴얼]\n{chunk}\n\n"
        "범용적 질문 (한 줄에 하나씩):"
    ),
}

# 기본 프롬프트 (매칭 안 되는 KB용)
DEFAULT_CHUNK_QA_PROMPT = (
    "다음 문서 내용을 바탕으로, 어느 매장에서든 통용되는 범용적인 질문을 1~3개 만들어주세요.\n\n"
    "규칙:\n"
    "- 특정 매장명, 날짜, 직원명을 포함하지 마세요\n"
    "- '~절차 알려줘', '~방법이 뭐야?', '~제도가 뭐야?' 같은 범용적 형태로 작성\n\n"
    "[문서 내용]\n{chunk}\n\n"
    "범용적 질문 (한 줄에 하나씩):"
)

# 답변 불가 / 일반론 감지 패턴
_UNANSWERABLE_PATTERNS = [
    "제공된 문서들에",
    "제공된 문서에서",
    "주어진 문서들에서",
    "명시되어 있지 않",
    "포함되어 있지 않",
    "직접적인 정보가",
    "직접적인 정보는",
    "명확한 정보가",
    "구체적인 정보가 부족",
    "일반적인 의미를 바탕으로",
]


def _is_unanswerable(answer: str) -> bool:
    """답변이 'KB에 없다'는 내용인지 감지."""
    answer_prefix = answer[:200]
    match_count = sum(1 for p in _UNANSWERABLE_PATTERNS if p in answer_prefix)
    return match_count >= 2


CHUNK_QA_PROMPT = (
    "다음 문서 내용을 바탕으로, 어느 매장에서든 통용되는 범용적인 질문을 1~3개 만들어주세요.\n\n"
    "규칙:\n"
    "- 특정 매장명, 날짜, 직원명을 포함하지 마세요\n"
    "- '~절차 알려줘', '~방법이 뭐야?', '~제도가 뭐야?' 같은 범용적 형태로 작성\n\n"
    "[문서 내용]\n{chunk}\n\n"
    "범용적 질문 (한 줄에 하나씩):"
)


async def generate_test_qa(
    llm_client,
    qdrant_url: str,
    kb_ids: list[str],
    count: int = 50,
    rag_api_url: str = "http://localhost:8000",
    quality_filter=None,
    existing_questions: set[str] | None = None,
) -> list[dict[str, Any]]:
    """테스트용 QA 쌍 생성 (KB 청크 기반).

    1. KB 청크를 랜덤 샘플링
    2. Teacher LLM이 청크 내용 보고 질문 생성
    3. Hub Search API로 답변 생성 (검색 + 리랭킹 + 그래프 + LLM)
    4. 답변 품질 확인
    """
    import httpx

    # Step 1: KB 청크 샘플링
    chunks_per_kb = max(count // max(len(kb_ids), 1), 10)
    all_chunks = await _fetch_sample_chunks(qdrant_url, kb_ids, limit=chunks_per_kb)

    flat_chunks: list[dict[str, str]] = []
    for kb_id, contents in all_chunks.items():
        for content in contents:
            if len(content) >= 100:  # 너무 짧은 청크 제외
                flat_chunks.append({"kb_id": kb_id, "content": content})

    if not flat_chunks:
        logger.warning(
            "No chunks found in KBs: %s (checked collections: %s). Falling back to templates.",
            kb_ids, [f"kb_{k.replace('-', '_')}" for k in kb_ids],
        )
        return await _generate_from_templates(llm_client, kb_ids, count, rag_api_url)

    logger.info(
        "Found %d usable chunks (>= 100 chars) from %d KBs",
        len(flat_chunks), len(all_chunks),
    )

    # 기존 질문 세트 (중복 방지용)
    seen_questions = set(existing_questions or set())

    # count보다 많이 샘플링 (중복/필터 탈락 대비)
    sample_size = min(count * 2, len(flat_chunks))
    sampled = random.sample(flat_chunks, sample_size)
    logger.info("Sampled %d chunks from %d KBs for QA generation (existing: %d questions)",
                len(sampled), len(all_chunks), len(seen_questions))

    # Step 2: 청크 → 질문 생성 + Step 3: Hub Search → 답변
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for i, chunk_info in enumerate(sampled):
            try:
                # KB별 특화 프롬프트 선택
                kb = chunk_info["kb_id"]
                prompt_template = KB_PROMPTS.get(kb, DEFAULT_CHUNK_QA_PROMPT)
                prompt = prompt_template.format(chunk=chunk_info["content"][:1500])
                q_response = None
                if llm_client and hasattr(llm_client, "generate"):
                    q_response = await llm_client.generate(prompt, temperature=0.7)
                elif llm_client and hasattr(llm_client, "call"):
                    q_response = await llm_client.call(prompt, temperature=0.7)

                if not q_response:
                    # LLM 없으면 Hub Search에 청크 내용으로 질문 생성 요청
                    resp = await client.post(
                        f"{rag_api_url}/api/v1/search/hub",
                        json={
                            "query": f"다음 내용에 대해 질문을 만들어줘: {chunk_info['content'][:300]}",
                            "kb_ids": kb_ids,
                            "top_k": 3,
                            "include_answer": True,
                        },
                    )
                    if resp.status_code == 200:
                        sr = resp.json()
                        # 답변에서 질문 추출 시도
                        q_response = sr.get("answer", "")
                    if not q_response:
                        continue

                if not q_response:
                    continue

                # 생성된 질문 파싱 (줄바꿈으로 분리)
                questions = [
                    line.strip().lstrip("0123456789.-) ")
                    for line in q_response.split("\n")
                    if line.strip() and len(line.strip()) > 5
                ][:2]  # 청크당 최대 2개

                for question in questions:
                    # Hub Search API로 답변 생성
                    resp = await client.post(
                        f"{rag_api_url}/api/v1/search/hub",
                        json={
                            "query": question,
                            "kb_ids": kb_ids,
                            "top_k": 5,
                            "include_answer": True,
                        },
                    )
                    resp.raise_for_status()
                    search_result = resp.json()

                    answer = search_result.get("answer", "")
                    confidence = search_result.get("confidence", "")

                    # 답변 불가 / 일반론 자동 제외
                    if not answer:
                        continue
                    if _is_unanswerable(answer):
                        logger.debug("Skipped (unanswerable): %s", question[:40])
                        continue
                    if confidence in ("낮음", "low", "없음"):
                        logger.debug("Skipped (low confidence): %s", question[:40])
                        continue

                    # 추론 제거 + 답변 정규화 (QualityFilter 재사용)
                    if quality_filter:
                        answer = await quality_filter.convert_to_answer_only(
                            question, answer,
                        )
                        answer = await quality_filter.normalize_answer_length(answer)

                    result_chunks = search_result.get("chunks", [])
                    source_kbs = list({
                        c.get("kb_id", "") for c in result_chunks if c.get("kb_id")
                    })

                    # 중복 체크 (기존 + 이번 생성분)
                    from rapidfuzz import fuzz as _fuzz
                    is_dup = any(
                        _fuzz.token_sort_ratio(question, seen) > 85
                        for seen in list(seen_questions)[-200:]
                    )
                    if is_dup:
                        logger.debug("Skipped (duplicate): %s", question[:40])
                        continue

                    seen_questions.add(question)
                    results.append({
                        "question": question,
                        "answer": answer.strip(),
                        "source_type": "test_seed",
                        "kb_id": ",".join(source_kbs) if source_kbs else chunk_info["kb_id"],
                        "source_id": f"chunk_based_{search_result.get('query_type', '')}",
                    })

                    if len(results) >= count:
                        break

            except Exception as e:
                logger.warning("Chunk QA generation failed: %s", e)

            if len(results) >= count:
                break
            if (i + 1) % 10 == 0:
                logger.info("Test data generation: %d/%d chunks, %d QA pairs", i + 1, len(sampled), len(results))

    logger.info("Generated %d test QA pairs (chunk-based + Hub Search)", len(results))
    return results


async def _generate_from_templates(
    llm_client, kb_ids: list[str], count: int, rag_api_url: str,
) -> list[dict[str, Any]]:
    """Fallback: 청크가 없을 때 템플릿 기반 생성."""
    import httpx

    all_questions = []
    for category, questions in TEST_QUESTION_TEMPLATES.items():
        for q in questions:
            all_questions.append({"question": q, "category": category})

    selected = random.sample(all_questions, min(count, len(all_questions)))
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60) as client:
        for item in selected:
            try:
                resp = await client.post(
                    f"{rag_api_url}/api/v1/search/hub",
                    json={"query": item["question"], "kb_ids": kb_ids, "top_k": 5, "include_answer": True},
                )
                resp.raise_for_status()
                sr = resp.json()
                answer = sr.get("answer", "")
                if answer:
                    source_kbs = list({c.get("kb_id", "") for c in sr.get("chunks", []) if c.get("kb_id")})
                    results.append({
                        "question": item["question"],
                        "answer": answer.strip(),
                        "source_type": "test_seed",
                        "kb_id": ",".join(source_kbs) if source_kbs else ",".join(kb_ids[:3]),
                        "source_id": f"template_{sr.get('query_type', '')}",
                    })
            except Exception as e:
                logger.warning("Template QA failed for '%s': %s", item["question"][:30], e)

    return results


async def _fetch_sample_chunks(
    qdrant_url: str, kb_ids: list[str], limit: int = 100,
) -> dict[str, list[str]]:
    """Qdrant에서 KB별 샘플 청크 가져오기."""
    import httpx

    chunks: dict[str, list[str]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for kb_id in kb_ids[:5]:
            # kb_id → Qdrant collection name 변환 (kb_ prefix + 하이픈→언더스코어)
            collection = f"kb_{kb_id.replace('-', '_')}"
            try:
                resp = await client.post(
                    f"{qdrant_url}/collections/{collection}/points/scroll",
                    json={"limit": limit, "with_payload": True},
                )
                if resp.status_code == 200:
                    points = resp.json().get("result", {}).get("points", [])
                    chunks[kb_id] = [
                        p.get("payload", {}).get("content", "")
                        for p in points
                        if p.get("payload", {}).get("content")
                    ]
                    logger.info("Fetched %d chunks from %s (%s)", len(chunks[kb_id]), kb_id, collection)
            except Exception as e:
                logger.warning("Failed to fetch chunks from %s (%s): %s", kb_id, collection, e)

    return chunks


def _find_relevant_context(
    question: str, chunks_by_kb: dict[str, list[str]],
) -> str:
    """질문과 관련된 청크를 간단한 키워드 매칭으로 찾기."""
    all_chunks = []
    for kb_chunks in chunks_by_kb.values():
        all_chunks.extend(kb_chunks)

    if not all_chunks:
        return ""

    # 질문 키워드로 관련 청크 찾기
    keywords = [w for w in question.split() if len(w) >= 2]
    scored = []
    for chunk in all_chunks:
        score = sum(1 for kw in keywords if kw in chunk)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return scored[0][1][:500]

    # 매칭 없으면 랜덤 청크
    return random.choice(all_chunks)[:500] if all_chunks else ""
