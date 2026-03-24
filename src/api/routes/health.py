"""Health check endpoints."""

from fastapi import APIRouter

from src.api.app import _get_state

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    state = _get_state()
    checks = {
        "qdrant": "qdrant_provider" in state,
        "neo4j": "neo4j" in state,
        "embedding": "embedder" in state,
        "llm": "llm" in state,
    }
    healthy = all(checks.values())
    return {
        "status": "healthy" if healthy else "degraded",
        "checks": checks,
    }
