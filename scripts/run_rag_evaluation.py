"""Run RAG evaluation using golden set Q&A pairs.

For each approved golden set question:
1. Search via Hub Search API (same as user would)
2. Compare answer with expected answer using LLM judge
3. Score: faithfulness, relevancy, completeness
4. Save results to DB + update eval/history API

Usage:
    AWS_PROFILE=jeongbeomkim uv run python scripts/run_rag_evaluation.py
    AWS_PROFILE=jeongbeomkim uv run python scripts/run_rag_evaluation.py a-ari
"""
import sys
import json
import logging
import os
import uuid
import asyncio
import time
from datetime import datetime, timezone

import boto3
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SEARCH_URL = "http://localhost:8000/api/v1/search/hub"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]

JUDGE_PROMPT = """당신은 RAG 평가 봇입니다. 반드시 JSON만 출력하세요. 설명, 마크다운, 줄바꿈 없이 한 줄 JSON만 출력합니다.

질문: {question}
기대 답변: {expected}
실제 답변: {actual}

각 항목을 0.0~1.0으로 평가:
faithfulness=실제답변이 근거있는가, relevancy=질문에 답하는가, completeness=핵심정보 포함하는가

출력: {{"faithfulness": 0.0, "relevancy": 0.0, "completeness": 0.0}}"""


def get_sm_client():
    """Fresh boto3 session each call to handle SSO token refresh."""
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "jeongbeomkim"),
        region_name=os.getenv("SAGEMAKER_REGION", "ap-northeast-2"),
    )
    return session.client("sagemaker-runtime")


def search_and_answer(question: str, kb_ids: list[str]) -> dict:
    """Call Hub Search API to get answer."""
    try:
        resp = requests.post(SEARCH_URL, json={
            "query": question,
            "top_k": 5,
            "kb_ids": kb_ids,
            "include_answer": True,
        }, timeout=120)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"Search failed: {e}")
    return {"answer": None, "chunks": []}


_sm_client = None

def _get_or_refresh_sm_client(force_refresh: bool = False):
    """Reuse SM client, refresh on auth failure."""
    global _sm_client
    if _sm_client is None or force_refresh:
        _sm_client = get_sm_client()
    return _sm_client


def judge_answer(question: str, expected: str, actual: str, retry: int = 2) -> dict | None:
    """LLM judge: compare expected vs actual answer. Returns None only on SSO expiry."""
    endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")
    prompt = JUDGE_PROMPT.format(question=question, expected=expected, actual=actual or "(답변 없음)")

    for attempt in range(retry + 1):
        try:
            sm_client = _get_or_refresh_sm_client(force_refresh=(attempt > 0))
            resp = sm_client.invoke_endpoint(
                EndpointName=endpoint,
                ContentType="application/json",
                Body=json.dumps({
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.1,
                }),
            )
            raw = json.loads(resp["Body"].read())["choices"][0]["message"]["content"].strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                scores = json.loads(raw[start:end])
                return {
                    "faithfulness": min(1.0, max(0.0, float(scores.get("faithfulness", 0)))),
                    "relevancy": min(1.0, max(0.0, float(scores.get("relevancy", 0)))),
                    "completeness": min(1.0, max(0.0, float(scores.get("completeness", 0)))),
                }
            else:
                logger.warning(f"Judge non-JSON (attempt {attempt+1}): {raw[:80]}")
                continue  # Retry
        except Exception as e:
            logger.warning(f"Judge failed (attempt {attempt+1}): {e}")
            if "AccessDeniedException" in str(e):
                logger.error("SSO token expired. Run: aws sso login --profile jeongbeomkim")
                return None
    # All retries exhausted — return zero scores (don't skip, count as failure)
    logger.warning(f"Judge exhausted retries for: {question[:50]}")
    return {"faithfulness": 0.0, "relevancy": 0.0, "completeness": 0.0}


async def load_golden_set(kb_id: str | None = None, status: str = "approved") -> list[dict]:
    """Load golden set from DB. If no approved, use pending."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine("postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db")
    async with engine.begin() as conn:
        if kb_id:
            r = await conn.execute(text(
                "SELECT id, kb_id, question, expected_answer, source_document "
                "FROM rag_golden_set WHERE kb_id = :kb AND status IN ('approved', 'pending') "
                "ORDER BY status ASC, created_at LIMIT 100"
            ), {"kb": kb_id})
        else:
            r = await conn.execute(text(
                "SELECT id, kb_id, question, expected_answer, source_document "
                "FROM rag_golden_set WHERE status IN ('approved', 'pending') "
                "ORDER BY kb_id, status ASC, created_at LIMIT 500"
            ))
        rows = r.fetchall()
    await engine.dispose()
    return [{"id": str(r[0]), "kb_id": r[1], "question": r[2], "expected": r[3], "source_doc": r[4]} for r in rows]


async def save_eval_results(eval_id: str, kb_id: str, results: list[dict], metrics: dict):
    """Save evaluation results to DB."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine("postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db")
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rag_eval_results (
                id UUID PRIMARY KEY,
                eval_id VARCHAR(100) NOT NULL,
                kb_id VARCHAR(100),
                golden_set_id UUID,
                question TEXT,
                expected_answer TEXT,
                actual_answer TEXT,
                faithfulness FLOAT DEFAULT 0,
                relevancy FLOAT DEFAULT 0,
                completeness FLOAT DEFAULT 0,
                search_time_ms FLOAT DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        for r in results:
            await conn.execute(text("""
                INSERT INTO rag_eval_results (id, eval_id, kb_id, golden_set_id, question,
                    expected_answer, actual_answer, faithfulness, relevancy, completeness, search_time_ms)
                VALUES (:id, :eval_id, :kb_id, :gs_id, :q, :expected, :actual, :f, :r, :c, :t)
            """), {
                "id": str(uuid.uuid4()), "eval_id": eval_id, "kb_id": r["kb_id"],
                "gs_id": r["golden_set_id"], "q": r["question"],
                "expected": r["expected"], "actual": r["actual"],
                "f": r["faithfulness"], "r": r["relevancy"], "c": r["completeness"],
                "t": r["search_time_ms"],
            })
    await engine.dispose()


def run_evaluation(kb_ids: list[str]):
    eval_id = str(uuid.uuid4())[:8]
    logger.info(f"Evaluation ID: {eval_id}")

    golden_set = asyncio.run(load_golden_set())
    if kb_ids:
        golden_set = [g for g in golden_set if g["kb_id"] in kb_ids]
    logger.info(f"Golden set: {len(golden_set)} questions")

    if not golden_set:
        logger.error("No golden set found. Run generate_golden_set.py first.")
        return

    results: list[dict] = []
    scores_sum = {"faithfulness": 0, "relevancy": 0, "completeness": 0}
    skipped = 0

    for i, gs in enumerate(golden_set):
        t0 = time.time()
        search_result = search_and_answer(gs["question"], [gs["kb_id"]])
        search_time = (time.time() - t0) * 1000
        actual_answer = search_result.get("answer") or ""

        # Skip if search completely failed (no answer at all)
        if not actual_answer:
            skipped += 1
            logger.debug(f"Skipped (no answer): {gs['question'][:50]}")
            # Still judge with empty answer to measure search coverage

        scores = judge_answer(gs["question"], gs["expected"], actual_answer)
        if scores is None:
            # Unrecoverable (e.g. SSO expired) — skip this question
            skipped += 1
            logger.warning(f"Skipped (judge failed): {gs['question'][:50]}")
            continue

        results.append({
            "kb_id": gs["kb_id"],
            "golden_set_id": gs["id"],
            "question": gs["question"],
            "expected": gs["expected"],
            "actual": actual_answer[:500],
            "faithfulness": scores["faithfulness"],
            "relevancy": scores["relevancy"],
            "completeness": scores["completeness"],
            "search_time_ms": search_time,
        })

        for k in scores_sum:
            scores_sum[k] += scores[k]

        if (i + 1) % 10 == 0:
            n = len(results) or 1
            logger.info(
                f"Progress: {i+1}/{len(golden_set)} (scored: {n}, skipped: {skipped}) | "
                f"F={scores_sum['faithfulness']/n:.3f} "
                f"R={scores_sum['relevancy']/n:.3f} "
                f"C={scores_sum['completeness']/n:.3f}"
            )

    # Final metrics
    n = len(results)
    metrics = {
        "faithfulness": round(scores_sum["faithfulness"] / n, 3) if n else 0,
        "answer_relevancy": round(scores_sum["relevancy"] / n, 3) if n else 0,
        "completeness": round(scores_sum["completeness"] / n, 3) if n else 0,
        "overall_score": round(sum(scores_sum.values()) / (n * 3), 3) if n else 0,
        "total_questions": n,
        "avg_search_time_ms": round(sum(r["search_time_ms"] for r in results) / n, 1) if n else 0,
    }

    logger.info(f"\n{'='*60}")
    logger.info(f"EVALUATION COMPLETE: {eval_id}")
    logger.info(f"  Faithfulness:  {metrics['faithfulness']:.3f}")
    logger.info(f"  Relevancy:     {metrics['answer_relevancy']:.3f}")
    logger.info(f"  Completeness:  {metrics['completeness']:.3f}")
    logger.info(f"  Overall:       {metrics['overall_score']:.3f}")
    logger.info(f"  Avg Search:    {metrics['avg_search_time_ms']:.0f}ms")
    logger.info(f"  Questions:     {metrics['total_questions']}")
    logger.info(f"{'='*60}")

    # Save to DB
    asyncio.run(save_eval_results(eval_id, ",".join(kb_ids) if kb_ids else "all", results, metrics))
    logger.info(f"Results saved to rag_eval_results (eval_id={eval_id})")

    # Also update the eval history API
    try:
        requests.post("http://localhost:8000/api/v1/admin/eval/trigger", json={
            "kb_id": ",".join(kb_ids) if kb_ids else "all",
            "eval_type": "golden_set",
            "metrics": metrics,
        })
    except Exception:
        pass


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ALL_KBS
    run_evaluation(targets)
