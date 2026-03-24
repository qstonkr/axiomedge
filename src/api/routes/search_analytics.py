"""Search Analytics API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/search", tags=["Search Analytics"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/history
# ---------------------------------------------------------------------------
@router.get("/history")
async def get_search_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """Get search history."""
    return {
        "searches": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/analytics
# ---------------------------------------------------------------------------
@router.get("/analytics")
async def get_search_analytics():
    """Get search analytics."""
    return {
        "total_searches": 0,
        "unique_queries": 0,
        "avg_results_per_query": 0.0,
        "avg_response_time_ms": 0.0,
        "top_queries": [],
        "zero_result_queries": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/search/injection-stats
# ---------------------------------------------------------------------------
@router.get("/injection-stats")
async def get_search_injection_stats():
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
async def get_agentic_rag_stats():
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
async def get_crag_stats():
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
async def get_search_adapter_stats():
    """Get search adapter stats."""
    return {
        "adapters": [],
        "total_requests": 0,
    }
