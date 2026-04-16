"""Health check endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


async def _check_services(state) -> dict[str, bool]:
    """Run health checks for all backend services.

    각 서비스 체크는 best-effort — 실패해도 다른 체크는 계속 진행. 다만
    실패 원인을 반드시 debug 로그로 남겨 /health 가 degraded 뜰 때 원인
    추적 가능하게 한다 (이전엔 조용히 false 반환해서 디버깅 불가).
    """
    checks: dict[str, bool] = {}

    # Qdrant
    try:
        provider = state.get("qdrant_provider")
        if provider:
            client = await provider.ensure_client()
            await client.get_collections()
            checks["qdrant"] = True
        else:
            checks["qdrant"] = False
    except Exception as e:
        logger.debug("Health check qdrant failed: %s", e)
        checks["qdrant"] = False

    # Neo4j
    try:
        neo4j = state.get("neo4j")
        checks["neo4j"] = await neo4j.health_check() if neo4j else False
    except Exception as e:
        logger.debug("Health check neo4j failed: %s", e)
        checks["neo4j"] = False

    # Embedding
    try:
        embedder = state.get("embedder")
        checks["embedding"] = embedder.is_ready() if embedder else False
    except Exception as e:
        logger.debug("Health check embedding failed: %s", e)
        checks["embedding"] = False

    # LLM (Ollama)
    try:
        llm = state.get("llm")
        if llm:
            import httpx
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(f"{llm._config.base_url}/api/version", timeout=3)
            checks["llm"] = resp.status_code == 200
        else:
            checks["llm"] = False
    except Exception as e:
        logger.debug("Health check llm failed: %s", e)
        checks["llm"] = False

    # Redis
    try:
        cache = state.get("search_cache")
        if cache:
            await cache._redis.ping()
            checks["redis"] = True
        else:
            checks["redis"] = False
    except Exception as e:
        logger.debug("Health check redis failed: %s", e)
        checks["redis"] = False

    # PostgreSQL
    try:
        db = state.get("db_session_factory")
        checks["database"] = db is not None
    except Exception as e:
        logger.debug("Health check database failed: %s", e)
        checks["database"] = False

    # PaddleOCR
    try:
        import os

        import httpx
        ocr_url = os.getenv("PADDLEOCR_API_URL", "http://localhost:8866/ocr").replace("/ocr", "/health")
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(ocr_url, timeout=3)
        checks["paddleocr"] = resp.status_code == 200
    except Exception as e:
        logger.debug("Health check paddleocr failed: %s", e)
        checks["paddleocr"] = False

    return checks


@router.get("/health")
async def health():
    state = _get_state()
    checks = await _check_services(state)
    healthy = checks.get("qdrant", False) and checks.get("embedding", False)
    status = "healthy" if healthy else "degraded"
    return {"status": status, "checks": checks}
