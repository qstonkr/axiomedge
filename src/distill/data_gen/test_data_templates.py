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


async def generate_test_qa(
    llm_client,
    qdrant_url: str,
    kb_ids: list[str],
    count: int = 50,
) -> list[dict[str, Any]]:
    """테스트용 QA 쌍 생성.

    1. 질문 템플릿에서 count개 선택
    2. 각 질문에 대해 KB 청크를 컨텍스트로 제공 (가능한 경우)
    3. Teacher LLM으로 답변 생성
    """
    # 질문 선택 (카테고리별 균등 배분)
    all_questions = []
    for category, questions in TEST_QUESTION_TEMPLATES.items():
        for q in questions:
            all_questions.append({"question": q, "category": category})

    selected = random.sample(all_questions, min(count, len(all_questions)))

    # KB 청크 가져오기 (컨텍스트용)
    chunks_by_kb = await _fetch_sample_chunks(qdrant_url, kb_ids)

    results: list[dict[str, Any]] = []
    for i, item in enumerate(selected):
        question = item["question"]
        context = _find_relevant_context(question, chunks_by_kb)

        try:
            context_str = f"[참고 정보]\n{context}\n\n" if context else ""
            prompt = QA_GENERATION_PROMPT.format(
                question=question, context=context_str,
            )
            # SageMaker client: .generate() / Ollama: .generate()
            if hasattr(llm_client, "generate"):
                answer = await llm_client.generate(prompt, temperature=0.3)
            elif hasattr(llm_client, "call"):
                answer = await llm_client.call(prompt, temperature=0.3)
            else:
                logger.warning("LLM client has no generate/call method")
                continue
            if answer:
                results.append({
                    "question": question,
                    "answer": answer.strip(),
                    "source_type": "test_seed",
                    "kb_id": ",".join(kb_ids[:3]) if kb_ids else "",
                    "category": item["category"],
                })
        except Exception as e:
            logger.warning("Test QA generation failed for '%s': %s", question[:30], e)

        if (i + 1) % 10 == 0:
            logger.info("Test data generation: %d/%d", i + 1, len(selected))

    logger.info("Generated %d test QA pairs", len(results))
    return results


async def _fetch_sample_chunks(
    qdrant_url: str, kb_ids: list[str], limit: int = 100,
) -> dict[str, list[str]]:
    """Qdrant에서 KB별 샘플 청크 가져오기."""
    import httpx

    chunks: dict[str, list[str]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for kb_id in kb_ids[:3]:
            try:
                resp = await client.post(
                    f"{qdrant_url}/collections/{kb_id}/points/scroll",
                    json={"limit": limit, "with_payload": True},
                )
                if resp.status_code == 200:
                    points = resp.json().get("result", {}).get("points", [])
                    chunks[kb_id] = [
                        p.get("payload", {}).get("content", "")
                        for p in points
                        if p.get("payload", {}).get("content")
                    ]
            except Exception as e:
                logger.warning("Failed to fetch chunks from %s: %s", kb_id, e)

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
