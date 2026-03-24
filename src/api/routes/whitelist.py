"""Whitelist API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/knowledge/whitelist", tags=["Whitelist"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/whitelist
# ---------------------------------------------------------------------------
@router.get("")
async def list_whitelist(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    active_only: bool = Query(default=True),
):
    """List whitelist entries."""
    return {
        "entries": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/knowledge/whitelist
# ---------------------------------------------------------------------------
@router.post("")
async def add_whitelist_entry(body: dict[str, Any]):
    """Add whitelist entry."""
    entry_id = str(uuid.uuid4())
    return {"success": True, "entry_id": entry_id, "message": "Entry added"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/knowledge/whitelist/{entry_id}
# ---------------------------------------------------------------------------
@router.delete("/{entry_id}")
async def remove_whitelist_entry(entry_id: str):
    """Remove whitelist entry."""
    return {"success": True, "entry_id": entry_id}


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/knowledge/whitelist/{entry_id}/extend
# ---------------------------------------------------------------------------
@router.patch("/{entry_id}/extend")
async def extend_whitelist_ttl(entry_id: str, body: dict[str, Any]):
    """Extend whitelist TTL."""
    return {"success": True, "entry_id": entry_id, "message": "TTL extended"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/knowledge/whitelist/sync
# ---------------------------------------------------------------------------
@router.post("/sync")
async def sync_whitelist_to_configmap():
    """Sync whitelist to configmap."""
    return {"success": True, "message": "Synced"}
