"""Quality, Traceability, Dedup, Eval, Transparency API endpoints.

Stub routes for dashboard compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Quality"])


# ============================================================================
# Knowledge Traceability
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/{doc_id}/provenance
# ---------------------------------------------------------------------------
@router.get("/knowledge/{doc_id}/provenance")
async def get_document_provenance(doc_id: str):
    """Get document provenance."""
    state = _get_state()
    repo = state.get("provenance_repo")
    if repo:
        try:
            prov = await repo.get_by_knowledge_id(doc_id)
            if prov:
                return prov
        except Exception as e:
            logger.warning("Provenance repo get failed: %s", e)
    return {
        "doc_id": doc_id,
        "source": None,
        "ingested_at": None,
        "ingested_by": None,
        "transformations": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/{doc_id}/lineage
# ---------------------------------------------------------------------------
@router.get("/knowledge/{doc_id}/lineage")
async def get_document_lineage(doc_id: str):
    """Get document lineage."""
    return {
        "doc_id": doc_id,
        "lineage": [],
        "parent": None,
        "children": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/{doc_id}/versions
# ---------------------------------------------------------------------------
@router.get("/knowledge/{doc_id}/versions")
async def get_document_versions(doc_id: str):
    """Get document versions."""
    return {
        "doc_id": doc_id,
        "versions": [],
        "current_version": None,
    }


# ============================================================================
# Dedup
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/dedup/stats
# ---------------------------------------------------------------------------
@router.get("/dedup/stats")
async def get_dedup_stats():
    """Get dedup stats."""
    return {
        "total_duplicates_found": 0,
        "total_resolved": 0,
        "pending": 0,
        "stages": {
            "bloom": {"checked": 0, "flagged": 0},
            "lsh": {"checked": 0, "flagged": 0},
            "semhash": {"checked": 0, "flagged": 0},
            "llm": {"checked": 0, "flagged": 0},
        },
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/dedup/conflicts
# ---------------------------------------------------------------------------
@router.get("/dedup/conflicts")
async def get_dedup_conflicts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Get dedup conflicts."""
    return {
        "conflicts": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/dedup/resolve
# ---------------------------------------------------------------------------
@router.post("/dedup/resolve")
async def resolve_dedup_conflict(body: dict[str, Any]):
    """Resolve a dedup conflict."""
    return {"success": True, "message": "Conflict resolved"}


# ============================================================================
# ML Evaluation
# ============================================================================

# ---------------------------------------------------------------------------
# POST /api/v1/admin/eval/trigger
# ---------------------------------------------------------------------------
@router.post("/eval/trigger")
async def trigger_evaluation(body: dict[str, Any]):
    """Trigger ML evaluation."""
    return {
        "success": True,
        "eval_id": "stub",
        "message": "Evaluation triggered (stub)",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/eval/status
# ---------------------------------------------------------------------------
@router.get("/eval/status")
async def get_evaluation_status():
    """Get evaluation status."""
    return {
        "status": "idle",
        "current_eval_id": None,
        "progress": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/eval/history
# ---------------------------------------------------------------------------
@router.get("/eval/history")
async def list_evaluation_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Get evaluation history."""
    return {
        "evaluations": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ============================================================================
# Transparency & Contributors
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/transparency/stats
# ---------------------------------------------------------------------------
@router.get("/transparency/stats")
async def get_transparency_stats():
    """Get transparency stats."""
    return {
        "total_documents": 0,
        "with_provenance": 0,
        "with_owner": 0,
        "verified": 0,
        "transparency_score": 0.0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/contributors
# ---------------------------------------------------------------------------
@router.get("/contributors")
async def list_contributors(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List contributors."""
    return {
        "contributors": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ============================================================================
# Verification
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/verification/pending
# ---------------------------------------------------------------------------
@router.get("/verification/pending")
async def get_verification_pending(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Get pending verifications."""
    return {
        "documents": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/verification/{doc_id}/vote
# ---------------------------------------------------------------------------
@router.post("/verification/{doc_id}/vote")
async def submit_verification_vote(doc_id: str, body: dict[str, Any]):
    """Submit verification vote."""
    return {"success": True, "doc_id": doc_id, "message": "Vote recorded"}


# ============================================================================
# Version Management
# ============================================================================

# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/{doc_id}/rollback
# ---------------------------------------------------------------------------
@router.post("/documents/{doc_id}/rollback")
async def rollback_document_version(doc_id: str, body: dict[str, Any]):
    """Rollback document version."""
    return {"success": True, "doc_id": doc_id, "message": "Rolled back"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/{doc_id}/approve
# ---------------------------------------------------------------------------
@router.post("/documents/{doc_id}/approve")
async def approve_document_version(doc_id: str, body: dict[str, Any]):
    """Approve document version."""
    return {"success": True, "doc_id": doc_id, "message": "Approved"}


# ============================================================================
# Vectorstore / Embedding / Cache
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/vectorstore/stats
# ---------------------------------------------------------------------------
@router.get("/vectorstore/stats")
async def get_vectorstore_stats():
    """Get vectorstore stats."""
    from src.api.app import _get_state

    state = _get_state()
    store = state.get("qdrant_store")
    collections = state.get("qdrant_collections")
    total_points = 0
    collection_stats = []

    if collections and store:
        try:
            names = await collections.get_existing_collection_names()
            for name in names:
                try:
                    count = await store.count(name)
                    total_points += count
                    collection_stats.append({"name": name, "points": count})
                except Exception:
                    collection_stats.append({"name": name, "points": 0})
        except Exception:
            pass

    return {
        "total_points": total_points,
        "collections": collection_stats,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/embedding/stats
# ---------------------------------------------------------------------------
@router.get("/embedding/stats")
async def get_embedding_stats():
    """Get embedding stats."""
    from src.api.app import _get_state

    state = _get_state()
    embedder = state.get("embedder")
    return {
        "model": "bge-m3-onnx" if embedder else "not_initialized",
        "ready": bool(embedder),
        "dimension": 1024,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/cache/stats
# ---------------------------------------------------------------------------
@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache stats."""
    return {
        "hits": 0,
        "misses": 0,
        "size": 0,
        "hit_rate": 0.0,
    }
