"""RAG & Intelligent RAG API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

# Knowledge RAG router
knowledge_router = APIRouter(prefix="/api/v1/knowledge", tags=["RAG"])

# Intelligent RAG router
intelligent_router = APIRouter(prefix="/api/v1/intelligent-rag", tags=["Intelligent RAG"])


# ============================================================================
# Knowledge RAG
# ============================================================================

# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/ask
# ---------------------------------------------------------------------------
@knowledge_router.post("/ask")
async def rag_query(body: dict[str, Any]):
    """RAG query."""
    from src.api.app import _get_state

    state = _get_state()
    rag = state.get("rag_pipeline")
    query = body.get("query", "")
    mode = body.get("mode", "classic")
    kb_ids = body.get("kb_ids")

    if rag:
        try:
            from src.search.rag_pipeline import RAGRequest

            result = await rag.process(RAGRequest(query=query, kb_id=kb_ids[0] if kb_ids else None))
            return result.to_dict()
        except Exception as e:
            logger.warning("RAG query failed: %s", e)

    return {
        "query": query,
        "answer": None,
        "chunks": [],
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/rag/config
# ---------------------------------------------------------------------------
@knowledge_router.get("/rag/config")
async def get_rag_config():
    """Get RAG config."""
    return {
        "mode": "classic",
        "top_k": 5,
        "reranking": False,
        "graph_enabled": False,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/rag/stats
# ---------------------------------------------------------------------------
@knowledge_router.get("/rag/stats")
async def get_rag_stats():
    """Get RAG stats."""
    return {
        "total_queries": 0,
        "avg_response_time_ms": 0.0,
        "avg_chunks_returned": 0.0,
    }


# ============================================================================
# File Upload & Ingest
# ============================================================================

# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/file-upload-ingest
# ---------------------------------------------------------------------------
@knowledge_router.post("/file-upload-ingest")
async def upload_and_ingest(
    file: Any = None,
    files: Any = None,
    kb_id: str = "",
    kb_name: str | None = None,
    enable_vision: str = "false",
    create_new_kb: str = "false",
    tier: str | None = None,
    organization_id: str | None = None,
):
    """Upload and ingest files."""
    from fastapi import File, Form, UploadFile

    return {
        "success": True,
        "kb_id": kb_id,
        "documents_processed": 0,
        "chunks_created": 0,
        "message": "File upload ingest (stub)",
    }


# ============================================================================
# Intelligent RAG
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/intelligent-rag/cache/stats
# ---------------------------------------------------------------------------
@intelligent_router.get("/cache/stats")
async def get_intelligent_rag_cache_stats():
    """Get intelligent RAG cache stats."""
    return {
        "hits": 0,
        "misses": 0,
        "size": 0,
        "hit_rate": 0.0,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/intelligent-rag/cache/invalidate
# ---------------------------------------------------------------------------
@intelligent_router.post("/cache/invalidate")
async def invalidate_rag_cache(body: dict[str, Any]):
    """Invalidate RAG cache."""
    return {"success": True, "invalidated": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/intelligent-rag/cache/clear
# ---------------------------------------------------------------------------
@intelligent_router.post("/cache/clear")
async def clear_rag_cache():
    """Clear RAG cache."""
    return {"success": True, "cleared": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/intelligent-rag/metrics
# ---------------------------------------------------------------------------
@intelligent_router.get("/metrics")
async def get_intelligent_rag_metrics():
    """Get intelligent RAG metrics."""
    return {
        "total_queries": 0,
        "cache_hit_rate": 0.0,
        "avg_latency_ms": 0.0,
        "by_domain": {},
    }


# ---------------------------------------------------------------------------
# GET /api/v1/intelligent-rag/config/domains
# ---------------------------------------------------------------------------
@intelligent_router.get("/config/domains")
async def get_rag_domain_config():
    """Get RAG domain config."""
    return {"domains": [], "default_domain": None}


# ---------------------------------------------------------------------------
# PUT /api/v1/intelligent-rag/config/domains
# ---------------------------------------------------------------------------
@intelligent_router.put("/config/domains")
async def update_rag_domain_config(body: dict[str, Any]):
    """Update RAG domain config."""
    return {"success": True, "message": "Domain config updated"}


# ---------------------------------------------------------------------------
# GET /api/v1/intelligent-rag/health
# ---------------------------------------------------------------------------
@intelligent_router.get("/health")
async def get_intelligent_rag_health():
    """Get intelligent RAG health."""
    return {
        "status": "ok",
        "cache": "ok",
        "llm": "ok",
        "embedder": "ok",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/intelligent-rag/query
# ---------------------------------------------------------------------------
@intelligent_router.post("/query")
async def intelligent_rag_query(body: dict[str, Any]):
    """Intelligent RAG query."""
    return {
        "query": body.get("query", ""),
        "answer": None,
        "chunks": [],
        "domain": body.get("domain"),
        "strategy_used": None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/intelligent-rag/adapters
# ---------------------------------------------------------------------------
@intelligent_router.get("/adapters")
async def get_intelligent_rag_adapters():
    """Get intelligent RAG adapters."""
    return {"adapters": [], "total": 0}
