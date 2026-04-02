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


def _get_db_url() -> str:
    from src.config import get_settings
    return get_settings().database.database_url


def get_sm_client():
    """Fresh boto3 session each call to handle SSO token refresh."""
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "jeongbeomkim"),
        region_name=os.getenv("SAGEMAKER_REGION", "ap-northeast-2"),
    )
    return session.client("sagemaker-runtime")


def _get_auth_headers() -> dict[str, str]:
    """Build auth headers if AUTH_ENABLED. Returns empty dict if auth off."""
    if os.getenv("AUTH_ENABLED", "false").lower() != "true":
        return {}
    token = os.getenv("EVAL_API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    api_key = os.getenv("EVAL_API_KEY", "")
    if api_key:
        return {"X-API-Key": api_key}
    logger.warning("AUTH_ENABLED=true but no EVAL_API_TOKEN or EVAL_API_KEY set")
    return {}


def search_and_answer(question: str, kb_ids: list[str]) -> dict:
    """Call Hub Search API to get answer."""
    try:
        resp = requests.post(SEARCH_URL, json={
            "query": question,
            "top_k": 5,
            "kb_ids": kb_ids,
            "include_answer": True,
        }, headers=_get_auth_headers(), timeout=120)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            logger.error("Auth failed (401). Set EVAL_API_TOKEN or EVAL_API_KEY env var.")
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
                continue
        except Exception as e:
            logger.warning(f"Judge failed (attempt {attempt+1}): {e}")
            if "AccessDeniedException" in str(e):
                logger.error("SSO token expired. Run: aws sso login --profile jeongbeomkim")
                return None
    logger.warning(f"Judge exhausted retries for: {question[:50]}")
    return {"faithfulness": 0.0, "relevancy": 0.0, "completeness": 0.0}


async def load_golden_set(engine, kb_id: str | None = None) -> list[dict]:
    """Load golden set from DB. If no approved, use pending."""
    from sqlalchemy import text

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
    return [{"id": str(r[0]), "kb_id": r[1], "question": r[2], "expected": r[3], "source_doc": r[4]} for r in rows]


async def save_eval_results(engine, eval_id: str, kb_id: str, results: list[dict]):
    """Save evaluation results to DB."""
    from sqlalchemy import text

    # DDL in separate transaction so it persists even if INSERTs fail
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rag_eval_results (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

    # INSERT in batches of 50 — partial saves survive on failure
    batch_size = 50
    saved = 0
    for start in range(0, len(results), batch_size):
        batch = results[start:start + batch_size]
        try:
            async with engine.begin() as conn:
                for r in batch:
                    gs_id = r["golden_set_id"] or None
                    await conn.execute(text("""
                        INSERT INTO rag_eval_results
                            (eval_id, kb_id, golden_set_id, question,
                             expected_answer, actual_answer, faithfulness, relevancy,
                             completeness, search_time_ms)
                        VALUES (:eval_id, :kb_id, CAST(:gs_id AS UUID),
                                :q, :expected, :actual, :f, :r, :c, :t)
                    """), {
                        "eval_id": eval_id, "kb_id": r["kb_id"],
                        "gs_id": gs_id, "q": r["question"],
                        "expected": r["expected"], "actual": r["actual"],
                        "f": r["faithfulness"], "r": r["relevancy"],
                        "c": r["completeness"], "t": r["search_time_ms"],
                    })
            saved += len(batch)
        except Exception as e:
            logger.error(f"Failed to save batch {start}-{start+len(batch)}: {e}")
    logger.info(f"DB save: {saved}/{len(results)} results inserted")


async def async_main(kb_ids: list[str]):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_get_db_url())
    try:
        eval_id = str(uuid.uuid4())[:8]
        logger.info(f"Evaluation ID: {eval_id}")

        golden_set = await load_golden_set(engine)
        if kb_ids:
            golden_set = [g for g in golden_set if g["kb_id"] in kb_ids]
        logger.info(f"Golden set: {len(golden_set)} questions")

        if not golden_set:
            logger.error("No golden set found. Run generate_golden_set.py first.")
            return

        # Skip already-evaluated questions for this eval_id (resume support)
        from sqlalchemy import text as _text
        evaluated_ids: set[str] = set()
        try:
            async with engine.begin() as conn:
                rows = await conn.execute(_text(
                    "SELECT CAST(golden_set_id AS TEXT) FROM rag_eval_results WHERE eval_id = :eid"
                ), {"eid": eval_id})
                evaluated_ids = {r[0] for r in rows.fetchall() if r[0]}
        except Exception:
            pass  # Table may not exist yet
        if evaluated_ids:
            before = len(golden_set)
            golden_set = [g for g in golden_set if g["id"] not in evaluated_ids]
            logger.info(f"Resuming: skipped {before - len(golden_set)} already-evaluated, {len(golden_set)} remaining")

        results: list[dict] = []
        scores_sum = {"faithfulness": 0, "relevancy": 0, "completeness": 0}
        skipped = 0
        batch_to_save: list[dict] = []

        for i, gs in enumerate(golden_set):
            # Search with retry on timeout
            actual_answer = ""
            search_time = 0.0
            for attempt in range(2):
                try:
                    t0 = time.time()
                    search_result = search_and_answer(gs["question"], [gs["kb_id"]])
                    search_time = (time.time() - t0) * 1000
                    actual_answer = search_result.get("answer") or ""
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.warning(f"Search retry for: {gs['question'][:40]}... ({e})")
                        time.sleep(3)
                    else:
                        logger.warning(f"Search failed after retry: {gs['question'][:40]}")

            if not actual_answer:
                skipped += 1
                logger.debug(f"Skipped (no answer): {gs['question'][:50]}")

            scores = judge_answer(gs["question"], gs["expected"], actual_answer)
            if scores is None:
                skipped += 1
                logger.warning(f"Skipped (judge failed): {gs['question'][:50]}")
                continue

            result = {
                "kb_id": gs["kb_id"],
                "golden_set_id": gs["id"],
                "question": gs["question"],
                "expected": gs["expected"],
                "actual": actual_answer[:500],
                "faithfulness": scores["faithfulness"],
                "relevancy": scores["relevancy"],
                "completeness": scores["completeness"],
                "search_time_ms": search_time,
            }
            results.append(result)
            batch_to_save.append(result)

            for k in scores_sum:
                scores_sum[k] += scores[k]

            # Save every 10 results (crash-safe incremental save)
            if len(batch_to_save) >= 10:
                await save_eval_results(engine, eval_id, ",".join(kb_ids) if kb_ids else "all", batch_to_save)
                batch_to_save = []

            if (i + 1) % 10 == 0:
                n = len(results) or 1
                logger.info(
                    f"Progress: {i+1}/{len(golden_set)} (scored: {n}, skipped: {skipped}) | "
                    f"F={scores_sum['faithfulness']/n:.3f} "
                    f"R={scores_sum['relevancy']/n:.3f} "
                    f"C={scores_sum['completeness']/n:.3f}"
                )

        # Save remaining batch
        if batch_to_save:
            await save_eval_results(engine, eval_id, ",".join(kb_ids) if kb_ids else "all", batch_to_save)

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
    finally:
        await engine.dispose()


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ALL_KBS
    asyncio.run(async_main(targets))
