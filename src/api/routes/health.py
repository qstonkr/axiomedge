"""Health check endpoints."""

from fastapi import APIRouter

from src.api.app import _get_state

router = APIRouter(tags=["Health"])


async def _check_services(state) -> dict[str, bool]:
    """Run health checks for all backend services."""
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
    except Exception:
        checks["qdrant"] = False

    # Neo4j
    try:
        neo4j = state.get("neo4j")
        checks["neo4j"] = await neo4j.health_check() if neo4j else False
    except Exception:
        checks["neo4j"] = False

    # Embedding
    try:
        embedder = state.get("embedder")
        checks["embedding"] = embedder.is_ready() if embedder else False
    except Exception:
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
    except Exception:
        checks["llm"] = False

    # Redis
    try:
        cache = state.get("search_cache")
        if cache:
            await cache._redis.ping()
            checks["redis"] = True
        else:
            checks["redis"] = False
    except Exception:
        checks["redis"] = False

    # PostgreSQL
    try:
        db = state.get("db_session_factory")
        checks["database"] = db is not None
    except Exception:
        checks["database"] = False

    # PaddleOCR
    try:
        import httpx
        import os
        ocr_url = os.getenv("PADDLEOCR_API_URL", "http://localhost:8866/ocr").replace("/ocr", "/health")
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(ocr_url, timeout=3)
        checks["paddleocr"] = resp.status_code == 200
    except Exception:
        checks["paddleocr"] = False

    return checks


@router.get("/health")
async def health():
    state = _get_state()
    checks = await _check_services(state)
    healthy = checks.get("qdrant", False) and checks.get("embedding", False)
    status = "healthy" if healthy else "degraded"
    return {"status": status, "checks": checks}
