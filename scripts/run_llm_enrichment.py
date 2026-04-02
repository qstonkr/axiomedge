"""Batch 2: LLM-based enrichment for existing chunks.

Requires SageMaker (GRAPHRAG_USE_SAGEMAKER=true) or Ollama.
Processes: GraphRAG, L2 category (future), term definition enrichment (future).

Currently wraps run_graphrag_parallel.py — extend with L2/definition logic later.

Usage:
    # GraphRAG only (current)
    GRAPHRAG_USE_SAGEMAKER=true AWS_PROFILE=jeongbeomkim GRAPHRAG_WORKERS=8 \
        uv run python scripts/run_llm_enrichment.py graphrag drp g-espa partnertalk hax

    # Future: L2 category assignment
    USE_SAGEMAKER_LLM=true AWS_PROFILE=jeongbeomkim \
        uv run python scripts/run_llm_enrichment.py l2-category a-ari drp

    # Future: Term definition enrichment
    USE_SAGEMAKER_LLM=true AWS_PROFILE=jeongbeomkim \
        uv run python scripts/run_llm_enrichment.py term-enrich a-ari
"""
import os
import sys
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_graphrag(kb_ids: list[str]):
    """Run parallel GraphRAG extraction.

    NOTE: OWNS/CATEGORIZED_AS edges are handled by run_metadata_backfill.py (Batch 1).
    This only runs LLM-based entity/relationship extraction (Store, Person, Process, etc.)
    """
    workers = os.getenv("GRAPHRAG_WORKERS", "8")
    logger.info(f"GraphRAG: {kb_ids} with {workers} workers")
    logger.info("NOTE: OWNS/CATEGORIZED_AS edges are NOT created here (use run_metadata_backfill.py)")
    subprocess.run(
        ["uv", "run", "python", "scripts/run_graphrag_parallel.py", *kb_ids],
        env={
            **os.environ,
            "GRAPHRAG_USE_SAGEMAKER": os.getenv("GRAPHRAG_USE_SAGEMAKER", "true"),
            "AWS_PROFILE": os.getenv("AWS_PROFILE", "jeongbeomkim"),
            "GRAPHRAG_WORKERS": workers,
        },
    )


def run_l2_category(kb_ids: list[str]):
    """Assign L2 categories using LLM classification.

    For each unique document in each KB:
    1. Get L1 category (already assigned)
    2. Send title + first chunk to LLM: "이 문서의 세부 카테고리는?"
    3. Check if similar L2 already exists (embedding similarity > 0.85)
    4. Create new L2 or assign existing
    5. Update Qdrant chunks + Neo4j CATEGORIZED_AS edge
    """
    import json
    import requests
    import boto3

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "jeongbeomkim"),
        region_name=os.getenv("SAGEMAKER_REGION", "ap-northeast-2"),
    )
    sm_client = session.client("sagemaker-runtime")
    endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")

    QDRANT_URL = "http://localhost:6333"

    PROMPT = """문서의 세부 카테고리를 2-6자 한국어 명사로 1개만 출력하세요. 설명 없이 단어만.

L1: {l1_category}
제목: {title}
내용: {content}

L2:"""

    for kb_id in kb_ids:
        collection = f"kb_{kb_id.replace('-', '_')}"
        logger.info(f"[{kb_id}] L2 category assignment...")

        # Get unique documents
        docs: dict[str, dict] = {}
        offset = None
        while True:
            body = {"limit": 100, "with_payload": ["doc_id", "document_name", "l1_category", "content"], "with_vector": False}
            if offset:
                body["offset"] = offset
            resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
            data = resp.json()["result"]
            for p in data["points"]:
                pay = p["payload"]
                did = pay.get("doc_id", "")
                if did and did not in docs:
                    docs[did] = {
                        "title": pay.get("document_name", ""),
                        "l1": pay.get("l1_category", "기타"),
                        "content": pay.get("content", "")[:500],
                    }
            offset = data.get("next_page_offset")
            if not offset:
                break

        logger.info(f"[{kb_id}] {len(docs)} unique documents")
        assigned = 0

        for doc_id, doc in docs.items():
            try:
                prompt = PROMPT.format(
                    l1_category=doc["l1"],
                    title=doc["title"],
                    content=doc["content"][:300],
                )
                resp = sm_client.invoke_endpoint(
                    EndpointName=endpoint,
                    ContentType="application/json",
                    Body=json.dumps({
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 30,
                        "temperature": 0.1,
                    }),
                )
                result = json.loads(resp["Body"].read())
                raw = result["choices"][0]["message"]["content"].strip()
                # Take first line only, remove quotes/parens/explanation
                l2_name = raw.split("\n")[0].strip().strip('"').strip("'").strip()
                # Remove parenthetical explanation
                if "(" in l2_name:
                    l2_name = l2_name[:l2_name.index("(")].strip()
                # Truncate to max 10 chars
                l2_name = l2_name[:10]

                if l2_name and 2 <= len(l2_name) <= 10:
                    # Update Qdrant chunks for this document
                    scroll_offset = None
                    while True:
                        sb = {
                            "limit": 100, "with_payload": ["doc_id"], "with_vector": False,
                            "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
                        }
                        if scroll_offset:
                            sb["offset"] = scroll_offset
                        sr = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=sb)
                        pts = sr.json()["result"]["points"]
                        if not pts:
                            break
                        point_ids = [p["id"] for p in pts]
                        requests.post(
                            f"{QDRANT_URL}/collections/{collection}/points/payload",
                            json={"points": point_ids, "payload": {"l2_category": l2_name}},
                        )
                        scroll_offset = sr.json()["result"].get("next_page_offset")
                        if not scroll_offset:
                            break
                    assigned += 1

                    if assigned % 50 == 0:
                        logger.info(f"[{kb_id}] {assigned}/{len(docs)} assigned...")
            except Exception as e:
                logger.debug(f"[{kb_id}] Failed for {doc_id}: {e}")

        logger.info(f"[{kb_id}] L2 assigned: {assigned}/{len(docs)}")


def run_term_enrich(kb_ids: list[str]):
    """Enrich term definitions using LLM.

    For terms with empty definitions:
    1. Find context chunks containing the term
    2. Send to LLM: "이 용어를 정의하세요"
    3. Save definition to glossary
    """
    import json
    import asyncio
    import boto3
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "jeongbeomkim"),
        region_name=os.getenv("SAGEMAKER_REGION", "ap-northeast-2"),
    )
    sm_client = session.client("sagemaker-runtime")
    endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")

    PROMPT = """다음 용어를 제공된 문맥을 참고하여 1문장으로 정의하세요. 문맥에 없는 내용은 추측하지 마세요.

용어: {term}
문맥: {context}

정의 (1문장):"""

    QDRANT_URL = "http://localhost:6333"

    async def _enrich():
        from src.config import DEFAULT_DATABASE_URL
        engine = create_async_engine(DEFAULT_DATABASE_URL)

        for kb_id in kb_ids:
            collection = f"kb_{kb_id.replace('-', '_')}"
            async with engine.begin() as conn:
                r = await conn.execute(text(
                    "SELECT id, term FROM glossary_terms "
                    "WHERE kb_id = :kb_id AND (definition IS NULL OR definition = '') "
                    "AND status = 'approved' "
                    "ORDER BY occurrence_count DESC LIMIT 500"
                ), {"kb_id": kb_id})
                terms = r.fetchall()

            logger.info(f"[{kb_id}] {len(terms)} terms to enrich")
            enriched = 0

            for term_id, term_text in terms:
                try:
                    # Find context chunks containing this term
                    import requests as _rq
                    ctx_resp = _rq.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json={
                        "limit": 2, "with_payload": ["content"], "with_vector": False,
                        "filter": {"must": [{"key": "morphemes", "match": {"text": term_text}}]},
                    })
                    ctx_chunks = ctx_resp.json().get("result", {}).get("points", [])
                    context = " ".join(p["payload"].get("content", "")[:200] for p in ctx_chunks)[:400]
                    if not context:
                        context = f"KB '{kb_id}'에서 발견된 용어"

                    prompt = PROMPT.format(
                        term=term_text,
                        context=context,
                    )
                    resp = sm_client.invoke_endpoint(
                        EndpointName=endpoint,
                        ContentType="application/json",
                        Body=json.dumps({
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 100,
                            "temperature": 0.2,
                        }),
                    )
                    result = json.loads(resp["Body"].read())
                    definition = result["choices"][0]["message"]["content"].strip()
                    # Take first sentence only
                    for sep in [".", "다.", "니다."]:
                        if sep in definition:
                            definition = definition[:definition.index(sep) + len(sep)]
                            break
                    definition = definition[:200]  # Max 200 chars

                    if definition and len(definition) >= 5:
                        async with engine.begin() as conn:
                            await conn.execute(text(
                                "UPDATE glossary_terms SET definition = :def WHERE id = :id"
                            ), {"def": definition, "id": str(term_id)})
                        enriched += 1

                        if enriched % 50 == 0:
                            logger.info(f"[{kb_id}] {enriched}/{len(terms)} enriched...")
                except Exception as e:
                    logger.debug(f"[{kb_id}] Failed for '{term_text}': {e}")

            logger.info(f"[{kb_id}] Enriched: {enriched}/{len(terms)}")
        await engine.dispose()

    try:
        asyncio.run(_enrich())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


COMMANDS = {
    "graphrag": run_graphrag,
    "l2-category": run_l2_category,
    "term-enrich": run_term_enrich,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command> [kb_ids...]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    command = sys.argv[1]
    kb_ids = sys.argv[2:] if len(sys.argv) > 2 else []

    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    if not kb_ids:
        print("No KB IDs specified.")
        sys.exit(1)

    logger.info(f"Command: {command}, KBs: {kb_ids}")
    COMMANDS[command](kb_ids)
