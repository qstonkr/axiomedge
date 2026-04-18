"""Search Analytics API endpoints - backed by UsageLogRepository."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/search", tags=["Search Analytics"])


def _get_usage_repo() -> Any:
    return _get_state().get("usage_log_repo")


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/history
# ---------------------------------------------------------------------------
@router.get("/history")
async def get_search_history(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Get search history."""
    repo = _get_usage_repo()
    if not repo:
        return {
            "searches": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
        }

    offset = (page - 1) * page_size
    result = await repo.list_recent(limit=page_size, offset=offset)
    return {
        "searches": result["searches"],
        "total": result["total"],
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/analytics
# ---------------------------------------------------------------------------
@router.get("/analytics")
async def get_search_analytics(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """Get search analytics."""
    repo = _get_usage_repo()
    if not repo:
        return {
            "total_searches": 0,
            "unique_queries": 0,
            "avg_results_per_query": 0.0,
            "avg_response_time_ms": 0.0,
            "top_queries": [],
            "zero_result_queries": [],
        }

    analytics = await repo.get_analytics(days=days)
    return {
        "total_searches": analytics["total_searches"],
        "unique_queries": len(analytics["top_queries"]),
        "avg_results_per_query": analytics["avg_results_per_query"],
        "avg_response_time_ms": analytics["avg_response_time_ms"],
        "top_queries": analytics["top_queries"],
        "top_kbs": analytics["top_kbs"],
        "period_days": analytics["period_days"],
        "unique_users": analytics["unique_users"],
        "zero_result_queries": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/user-history
# ---------------------------------------------------------------------------
@router.get("/user-history")
async def get_user_search_history(
    user_id: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Get search history for a specific user."""
    repo = _get_usage_repo()
    if not repo:
        return {"searches": [], "user_id": user_id}

    searches = await repo.get_by_user(user_id=user_id, limit=limit)
    return {"searches": searches, "user_id": user_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/injection-stats
# ---------------------------------------------------------------------------
@router.get("/injection-stats")
async def get_search_injection_stats() -> dict[str, int]:
    """Get search injection stats."""
    return {
        "total_injections": 0,
        "glossary_injections": 0,
        "synonym_injections": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/agentic-rag-stats
# ---------------------------------------------------------------------------
@router.get("/agentic-rag-stats")
async def get_agentic_rag_stats() -> dict[str, int | float]:
    """Get agentic RAG stats."""
    return {
        "total_queries": 0,
        "tool_calls": 0,
        "avg_steps": 0.0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/crag-stats
# ---------------------------------------------------------------------------
@router.get("/crag-stats")
async def get_crag_stats() -> dict[str, int | float]:
    """Get CRAG stats."""
    return {
        "total_queries": 0,
        "corrections_applied": 0,
        "correction_rate": 0.0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/adapter-stats
# ---------------------------------------------------------------------------
@router.get("/adapter-stats")
async def get_search_adapter_stats() -> dict[str, Any]:
    """Get search adapter stats."""
    return {
        "adapters": [],
        "total_requests": 0,
    }
