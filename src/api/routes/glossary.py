"""Glossary API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/glossary", tags=["Glossary"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary
# ---------------------------------------------------------------------------
@router.get("")
async def list_glossary_terms(
    kb_id: str = Query(default="all"),
    status: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    term_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    """List glossary terms."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            offset = (page - 1) * page_size
            terms = await repo.list_by_kb(
                kb_id=kb_id, status=status, scope=scope,
                term_type=term_type, limit=page_size, offset=offset,
            )
            total = await repo.count_by_kb(kb_id=kb_id, status=status, scope=scope, term_type=term_type)
            return {"terms": terms, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.warning("Glossary repo query failed: %s", e)
    return {"terms": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.get("/{term_id}")
async def get_glossary_term(term_id: str):
    """Get single glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            term = await repo.get_by_id(term_id)
            if term:
                return term
        except Exception as e:
            logger.warning("Glossary repo get failed: %s", e)
    raise HTTPException(status_code=404, detail="Term not found")


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary
# ---------------------------------------------------------------------------
@router.post("")
async def create_glossary_term(body: dict[str, Any]):
    """Create a glossary term."""
    term_id = str(uuid.uuid4())
    return {
        "success": True,
        "term_id": term_id,
        "message": "Term created",
    }


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.patch("/{term_id}")
async def update_glossary_term(term_id: str, body: dict[str, Any]):
    """Update a glossary term."""
    return {"success": True, "term_id": term_id, "message": "Term updated"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/approve
# ---------------------------------------------------------------------------
@router.post("/{term_id}/approve")
async def approve_glossary_term(term_id: str, body: dict[str, Any]):
    """Approve a glossary term."""
    return {"success": True, "term_id": term_id, "status": "approved"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/reject
# ---------------------------------------------------------------------------
@router.post("/{term_id}/reject")
async def reject_glossary_term(term_id: str, body: dict[str, Any]):
    """Reject a glossary term."""
    return {"success": True, "term_id": term_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/{term_id}
# ---------------------------------------------------------------------------
@router.delete("/{term_id}")
async def delete_glossary_term(term_id: str):
    """Delete a glossary term."""
    state = _get_state()
    repo = state.get("glossary_repo")
    if repo:
        try:
            deleted = await repo.delete(term_id)
            return {"success": deleted, "term_id": term_id}
        except Exception as e:
            logger.warning("Glossary repo delete failed: %s", e)
    return {"success": True, "term_id": term_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/{term_id}/promote-global
# ---------------------------------------------------------------------------
@router.post("/{term_id}/promote-global")
async def promote_glossary_term_to_global(term_id: str):
    """Promote a glossary term to global scope."""
    return {"success": True, "term_id": term_id, "scope": "global"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/import-csv
# ---------------------------------------------------------------------------
@router.post("/import-csv")
async def import_glossary_csv(
    file: UploadFile = File(...),
    encoding: str = Query(default="utf-8"),
    term_type: str = Query(default="term"),
):
    """Import glossary terms from CSV."""
    return {
        "success": True,
        "imported": 0,
        "skipped": 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/glossary/by-type/{term_type}
# ---------------------------------------------------------------------------
@router.delete("/by-type/{term_type}")
async def delete_glossary_by_type(
    term_type: str,
    kb_id: str = Query(default="global-standard"),
):
    """Delete glossary terms by type."""
    return {"success": True, "deleted": 0, "term_type": term_type}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/add-synonym
# ---------------------------------------------------------------------------
@router.post("/add-synonym")
async def add_synonym_to_standard(body: dict[str, Any]):
    """Add synonym to a standard term."""
    return {"success": True, "message": "Synonym added"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/similarity-check
# ---------------------------------------------------------------------------
@router.post("/similarity-check")
async def check_pending_similarity(
    threshold: float = Query(default=0.7),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """Check pending term similarity."""
    return {
        "pairs": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/glossary/similarity-cleanup
# ---------------------------------------------------------------------------
@router.post("/similarity-cleanup")
async def cleanup_pending_by_similarity(
    threshold: float = Query(default=0.7),
    body: dict[str, Any] | None = None,
):
    """Cleanup pending terms by similarity."""
    return {"success": True, "removed": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/glossary/similarity-distribution
# ---------------------------------------------------------------------------
@router.get("/similarity-distribution")
async def get_similarity_distribution():
    """Get similarity score distribution."""
    return {
        "distribution": [],
        "total_pairs": 0,
        "mean_similarity": 0.0,
    }
