"""Ownership API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)

# Two routers: one for /admin/ownership, one for /knowledge/experts
admin_router = APIRouter(prefix="/api/v1/admin/ownership", tags=["Ownership"])
knowledge_router = APIRouter(prefix="/api/v1/knowledge/experts", tags=["Ownership"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/documents
# ---------------------------------------------------------------------------
@admin_router.get("/documents")
async def list_document_owners(
    kb_id: str = Query(...),
    status: str | None = Query(default=None),
):
    """List document owners."""
    state = _get_state()
    repo = state.get("doc_owner_repo")
    if repo:
        try:
            owners = await repo.get_by_kb(kb_id)
            return {"owners": owners, "total": len(owners), "kb_id": kb_id}
        except Exception as e:
            logger.warning("Doc owner repo query failed: %s", e)
    return {"owners": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/documents/{document_id}
# ---------------------------------------------------------------------------
@admin_router.get("/documents/{document_id}")
async def get_document_owner(
    document_id: str,
    kb_id: str = Query(...),
):
    """Get document owner."""
    return {
        "document_id": document_id,
        "kb_id": kb_id,
        "owner": None,
        "status": "unassigned",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents
# ---------------------------------------------------------------------------
@admin_router.post("/documents")
async def assign_document_owner(body: dict[str, Any]):
    """Assign document owner."""
    return {"success": True, "message": "Owner assigned"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents/{document_id}/transfer
# ---------------------------------------------------------------------------
@admin_router.post("/documents/{document_id}/transfer")
async def transfer_ownership(document_id: str, body: dict[str, Any]):
    """Transfer document ownership."""
    return {"success": True, "document_id": document_id, "message": "Ownership transferred"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/documents/{document_id}/verify
# ---------------------------------------------------------------------------
@admin_router.post("/documents/{document_id}/verify")
async def verify_document_owner(document_id: str, body: dict[str, Any]):
    """Verify document owner."""
    return {"success": True, "document_id": document_id, "message": "Verified"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/stale
# ---------------------------------------------------------------------------
@admin_router.get("/stale")
async def get_stale_owners(
    kb_id: str = Query(...),
    days_threshold: int = Query(default=90, ge=1),
):
    """Get stale owners."""
    return {
        "stale_owners": [],
        "total": 0,
        "kb_id": kb_id,
        "days_threshold": days_threshold,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/availability/{owner_user_id}
# ---------------------------------------------------------------------------
@admin_router.get("/availability/{owner_user_id}")
async def get_owner_availability(owner_user_id: str):
    """Get owner availability."""
    return {
        "user_id": owner_user_id,
        "available": True,
        "status": "active",
    }


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/ownership/availability/{owner_user_id}
# ---------------------------------------------------------------------------
@admin_router.put("/availability/{owner_user_id}")
async def update_owner_availability(owner_user_id: str, body: dict[str, Any]):
    """Update owner availability."""
    return {"success": True, "user_id": owner_user_id}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ownership/topics
# ---------------------------------------------------------------------------
@admin_router.get("/topics")
async def list_topic_owners(
    kb_id: str = Query(...),
):
    """List topic owners."""
    state = _get_state()
    repo = state.get("topic_owner_repo")
    if repo:
        try:
            topics = await repo.get_by_kb(kb_id)
            return {"topics": topics, "total": len(topics), "kb_id": kb_id}
        except Exception as e:
            logger.warning("Topic owner repo query failed: %s", e)
    return {"topics": [], "total": 0, "kb_id": kb_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/ownership/topics
# ---------------------------------------------------------------------------
@admin_router.post("/topics")
async def assign_topic_owner(body: dict[str, Any]):
    """Assign topic owner."""
    return {"success": True, "message": "Topic owner assigned"}


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/experts/search
# ---------------------------------------------------------------------------
@knowledge_router.get("/search")
async def search_experts(
    query: str = Query(..., max_length=200),
    kb_id: str | None = Query(default=None),
):
    """Search for experts."""
    return {
        "experts": [],
        "total": 0,
        "query": query,
    }
