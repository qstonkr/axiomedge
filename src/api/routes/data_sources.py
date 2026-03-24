"""Data Sources API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/data-sources", tags=["Data Sources"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.get("")
async def list_data_sources():
    """List data sources."""
    return {"sources": [], "total": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.post("")
async def create_data_source(body: dict[str, Any]):
    """Create a data source."""
    source_id = str(uuid.uuid4())
    return {"success": True, "source_id": source_id, "message": "Data source created"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.get("/{source_id}")
async def get_data_source(source_id: str):
    """Get data source."""
    return {
        "source_id": source_id,
        "name": "",
        "type": "unknown",
        "status": "inactive",
        "last_sync": None,
        "config": {},
    }


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.put("/{source_id}")
async def update_data_source(source_id: str, body: dict[str, Any]):
    """Update data source."""
    return {"success": True, "source_id": source_id, "message": "Updated"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.delete("/{source_id}")
async def delete_data_source(source_id: str):
    """Delete data source."""
    return {"success": True, "source_id": source_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/{source_id}/trigger
# ---------------------------------------------------------------------------
@router.post("/{source_id}/trigger")
async def trigger_data_source_sync(
    source_id: str,
    sync_mode: str = Query(default="resume"),
):
    """Trigger data source sync."""
    return {
        "success": True,
        "source_id": source_id,
        "sync_mode": sync_mode,
        "message": "Sync triggered",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}/status
# ---------------------------------------------------------------------------
@router.get("/{source_id}/status")
async def get_data_source_status(source_id: str):
    """Get data source status."""
    return {
        "source_id": source_id,
        "status": "idle",
        "last_sync": None,
        "documents_synced": 0,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/file-ingest
# ---------------------------------------------------------------------------
@router.post("/file-ingest")
async def trigger_file_ingest(body: dict[str, Any]):
    """Trigger file ingest."""
    return {"success": True, "message": "File ingest triggered"}
