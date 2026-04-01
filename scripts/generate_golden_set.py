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
from datetime import datetime, timezone

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]
QUESTIONS_PER_KB = 50

PROMPT = """다음 문서 내용을 읽고, 이 문서에서 답변할 수 있는 질문과 정답을 3개 생성하세요.

문서 제목: {title}
문서 내용:
{content}

규칙:
- 문서에 명시된 사실만으로 답변 가능한 질문을 만드세요
- 질문은 실제 사용자가 할 법한 자연스러운 한국어로 작성
- 답변은 문서 내용을 근거로 1-3문장으로 간결하게
- 수치, 절차, 담당자 등 구체적 정보를 묻는 질문 우선

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


def generate_qa_from_chunk(sm_client, chunk: dict) -> list[dict]:
    """Generate Q&A pairs from a single chunk using LLM."""
    endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")
    prompt = PROMPT.format(title=chunk["title"], content=chunk["content"][:1500])

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

        # Parse JSON from response
        # Find JSON array in response
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            qa_list = json.loads(raw[start:end])
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

    engine = create_async_engine("postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db")

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

    # Insert Q&A pairs
    saved = 0
    async with engine.begin() as conn:
        for qa in qa_pairs:
            try:
                await conn.execute(text("""
                    INSERT INTO rag_golden_set (id, kb_id, question, expected_answer, source_document, source_slide)
                    VALUES (:id, :kb_id, :question, :answer, :doc, :slide)
                    ON CONFLICT DO NOTHING
                """), {
                    "id": str(uuid.uuid4()),
                    "kb_id": kb_id,
                    "question": qa["question"],
                    "answer": qa["answer"],
                    "doc": qa.get("source_document", ""),
                    "slide": qa.get("source_slide", ""),
                })
                saved += 1
            except Exception:
                pass

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
        qa_pairs = generate_qa_from_chunk(sm_client, chunk)
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
