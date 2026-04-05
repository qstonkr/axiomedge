"""Generate golden Q&A set from KB chunks using SageMaker EXAONE.

For each KB, selects high-quality chunks and asks LLM to generate
question-answer pairs. Results are saved to PostgreSQL for review
in the dashboard before RAG evaluation.

Usage:
    AWS_PROFILE=jeongbeomkim uv run python scripts/generate_golden_set.py
    AWS_PROFILE=jeongbeomkim uv run python scripts/generate_golden_set.py a-ari drp
"""
import sys
import json
import logging
import os
import uuid
import asyncio

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]
QUESTIONS_PER_KB = 50

# KB별 질문 유형 가이드 — 해당 KB의 특성에 맞는 질문을 생성하도록 유도
KB_CONTEXT = {
    "a-ari": "이 KB는 GS25 가맹점 계약, 양수도, 폐점, 정산, 경영주 지원 절차 문서입니다. 절차/프로세스/규정/정산 방식을 묻는 질문을 만드세요.",
    "drp": "이 KB는 가맹 분쟁 관련 법령/지침/조정 문서입니다. 분쟁 원인, 조정답변서 내용, 법적 근거, 해결 절차를 묻는 질문을 만드세요.",
    "g-espa": "이 KB는 GS25 점포 ESPA 실행보고서(매출 분석, 상권 분석, 경쟁점 비교, 개선 활동)입니다. 특정 점포의 매출/성과/활동 결과를 묻는 질문을 만드세요.",
    "partnertalk": "이 KB는 GS홈쇼핑 파트너사 문의/답변(상품 등록, 배송, 정산, 시스템 이슈)입니다. 특정 협력사의 문의 내용과 해결 방법을 묻는 질문을 만드세요.",
    "hax": "이 KB는 GS홈쇼핑 IT개발/운영 주간보고(배포, 장애, 프로젝트 진행, 시스템 운영)입니다. 특정 날짜/담당자의 업무 진행 상황을 묻는 질문을 만드세요.",
    "itops_general": "이 KB는 GS홈쇼핑 IT운영 문서(API 테스트, 시스템 설정, 배포, 운영 문의, 비즈니스 로직, 서비스 정책)입니다. 특정 시스템/API의 설정값, 테스트 결과, 비즈니스 로직(결제/주문/회원/정산 처리 규칙), 서비스 정책(할인/쿠폰/배송 정책)을 묻는 질문을 만드세요.",
}

PROMPT = """다음 문서 내용을 읽고, 이 문서에서 답변할 수 있는 질문과 정답을 3개 생성하세요.

{kb_context}

문서 제목: {title}
문서 내용:
{content}

규칙:
- 문서에 명시된 사실만으로 답변 가능한 질문을 만드세요
- 질문은 실제 사용자가 할 법한 자연스러운 한국어로 작성
- 답변은 문서 내용을 근거로 1-3문장으로 간결하게
- 수치, 절차, 담당자 등 구체적 정보를 묻는 질문 우선
- 반드시 질문에 구체적 맥락(문서 제목, 날짜, 프로젝트명, 점포명 등)을 포함하세요
- 점포/매장이 언급된 경우 반드시 점포명을 질문에 포함하세요 (예: "수지우남점", "자양공원점")
- 담당자가 언급된 경우 반드시 담당자명과 해당 시점을 함께 포함하세요
- 절대 금지: "차주", "이번 달", "다음 주", "지난 주", "최근", "현재" 같은 상대적 시점 표현
  (대신 "2024년 4월", "3월 3주차" 같은 절대적 시점을 사용)

JSON 배열로만 출력하세요:
[{{"question": "질문1", "answer": "답변1", "source_slide": "관련 슬라이드/페이지"}}, ...]"""


def get_sm_client():
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "jeongbeomkim"),
        region_name=os.getenv("SAGEMAKER_REGION", "ap-northeast-2"),
    )
    return session.client("sagemaker-runtime")


def fetch_quality_chunks(collection: str, limit: int = 20) -> list[dict]:
    """Fetch high-quality chunks (GOLD/SILVER, longest content)."""
    chunks = []
    offset = None
    while len(chunks) < limit * 3:
        body = {
            "limit": 100,
            "with_payload": ["content", "document_name", "quality_tier", "chunk_type"],
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset
        resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
        data = resp.json()["result"]
        for p in data["points"]:
            pay = p["payload"]
            if pay.get("chunk_type") == "title":
                continue
            chunks.append({
                "content": pay.get("content", ""),
                "title": pay.get("document_name", ""),
                "quality": pay.get("quality_tier", ""),
            })
        offset = data.get("next_page_offset")
        if not offset:
            break

    # Sort by content length (longer = more info), prefer GOLD/SILVER
    tier_order = {"GOLD": 0, "SILVER": 1, "BRONZE": 2}
    chunks.sort(key=lambda c: (tier_order.get(c["quality"], 3), -len(c["content"])))
    return chunks[:limit]


def generate_qa_from_chunk(sm_client, chunk: dict, kb_id: str = "") -> list[dict]:
    """Generate Q&A pairs from a single chunk using LLM."""
    endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")
    kb_context = KB_CONTEXT.get(kb_id, "")
    prompt = PROMPT.format(
        title=chunk["title"], content=chunk["content"][:1500], kb_context=kb_context,
    )

    try:
        resp = sm_client.invoke_endpoint(
            EndpointName=endpoint,
            ContentType="application/json",
            Body=json.dumps({
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.3,
            }),
        )
        raw = json.loads(resp["Body"].read())["choices"][0]["message"]["content"].strip()

        # Parse JSON from response — handle trailing commas, broken JSON
        import re
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = raw[start:end]
            # Fix trailing commas before ] or }
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            # Fix unescaped newlines inside JSON strings
            json_str = re.sub(r'(?<=": ")(.*)(?=")', lambda m: m.group(0).replace('\n', '\\n'), json_str, flags=re.DOTALL)
            try:
                qa_list = json.loads(json_str)
            except json.JSONDecodeError:
                # Last resort: extract individual objects
                qa_list = []
                for m in re.finditer(r'\{[^{}]+\}', json_str):
                    try:
                        obj_str = re.sub(r',\s*}', '}', m.group())
                        qa_list.append(json.loads(obj_str))
                    except json.JSONDecodeError:
                        continue
            return [
                {
                    "question": qa.get("question", ""),
                    "answer": qa.get("answer", ""),
                    "source_slide": qa.get("source_slide", ""),
                    "source_document": chunk["title"],
                }
                for qa in qa_list
                if qa.get("question") and qa.get("answer")
            ]
    except Exception as e:
        logger.warning(f"QA generation failed: {e}")
    return []


async def save_golden_set(kb_id: str, qa_pairs: list[dict]):
    """Save generated Q&A pairs to PostgreSQL."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    from src.config import DEFAULT_DATABASE_URL
    engine = create_async_engine(DEFAULT_DATABASE_URL)

    # Create table if not exists
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rag_golden_set (
                id UUID PRIMARY KEY,
                kb_id VARCHAR(100) NOT NULL,
                question TEXT NOT NULL,
                expected_answer TEXT NOT NULL,
                source_document VARCHAR(500),
                source_slide VARCHAR(100),
                status VARCHAR(20) DEFAULT 'pending',
                reviewed_by VARCHAR(100),
                reviewed_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))

    # Insert Q&A pairs (each in its own transaction to avoid cascade failures)
    saved = 0
    for qa in qa_pairs:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("""
                    INSERT INTO rag_golden_set (id, kb_id, question, expected_answer, source_document, source_slide)
                    VALUES (:id, :kb_id, :question, :answer, :doc, :slide)
                    ON CONFLICT DO NOTHING
                """), {
                    "id": str(uuid.uuid4()),
                    "kb_id": kb_id,
                    "question": qa["question"][:500],
                    "answer": qa["answer"][:2000],
                    "doc": qa.get("source_document", "")[:500],
                    "slide": qa.get("source_slide", "")[:100],
                })
            saved += 1
        except Exception as e:
            logger.warning(f"Save failed for '{qa['question'][:50]}': {e}")

    await engine.dispose()
    return saved


def run_generate(kb_id: str):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Fetching quality chunks...")
    chunks = fetch_quality_chunks(collection, limit=QUESTIONS_PER_KB // 3 + 5)
    logger.info(f"[{kb_id}] {len(chunks)} chunks selected")

    sm_client = get_sm_client()
    all_qa: list[dict] = []

    for i, chunk in enumerate(chunks):
        qa_pairs = generate_qa_from_chunk(sm_client, chunk, kb_id=kb_id)
        all_qa.extend(qa_pairs)
        if (i + 1) % 5 == 0:
            logger.info(f"[{kb_id}] {i+1}/{len(chunks)} chunks processed, {len(all_qa)} Q&As generated")
        if len(all_qa) >= QUESTIONS_PER_KB:
            break

    all_qa = all_qa[:QUESTIONS_PER_KB]
    logger.info(f"[{kb_id}] Generated {len(all_qa)} Q&A pairs")

    # Save to DB
    saved = asyncio.run(save_golden_set(kb_id, all_qa))
    logger.info(f"[{kb_id}] Saved {saved} to rag_golden_set table")

    # Show samples
    for qa in all_qa[:3]:
        logger.info(f"  Q: {qa['question']}")
        logger.info(f"  A: {qa['answer'][:80]}")
        logger.info("")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ALL_KBS

    for kb_id in targets:
        logger.info(f"\n{'='*60}")
        logger.info(f"[START] {kb_id}")
        logger.info(f"{'='*60}")
        run_generate(kb_id)

    logger.info(f"\n{'='*60}")
    logger.info("ALL DONE")
