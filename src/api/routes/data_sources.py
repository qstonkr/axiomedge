"""Data Sources API endpoints - wired to DataSourceRepository."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/data-sources", tags=["Data Sources"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.get("")
async def list_data_sources():
    """List data sources."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            sources = await repo.list()
            return {"sources": sources, "total": len(sources)}
        except Exception as e:
            logger.warning("Data source repo list failed: %s", e)
    return {"sources": [], "total": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.post("")
async def create_data_source(body: dict[str, Any]):
    """Create a data source."""
    state = _get_state()
    repo = state.get("data_source_repo")
    source_id = body.get("id") or str(uuid.uuid4())
    if repo:
        try:
            data = dict(body)
            data.setdefault("id", source_id)
            data.setdefault("status", "active")
            await repo.register(data)
            return {"success": True, "source_id": source_id, "message": "Data source created"}
        except Exception as e:
            logger.warning("Data source repo register failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Data source created (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.get("/{source_id}")
async def get_data_source(source_id: str):
    """Get data source."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id)
            if source:
                return source
        except Exception as e:
            logger.warning("Data source repo get failed: %s", e)
    raise HTTPException(status_code=404, detail="Data source not found")


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.put("/{source_id}")
async def update_data_source(source_id: str, body: dict[str, Any]):
    """Update data source."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            existing = await repo.get(source_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Data source not found")
            # Update status if provided, otherwise keep current
            status = body.get("status", existing.get("status", "active"))
            error_message = body.get("error_message")
            await repo.update_status(source_id, status, error_message)
            return {"success": True, "source_id": source_id, "message": "Updated"}
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Data source repo update failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Updated (stub - no DB)"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.delete("/{source_id}")
async def delete_data_source(source_id: str):
    """Delete data source."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            deleted = await repo.delete(source_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Data source not found")
            return {"success": True, "source_id": source_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Data source repo delete failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to delete data source: {e}")
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
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            existing = await repo.get(source_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Data source not found")
            await repo.update_status(source_id, "syncing")
            return {"success": True, "source_id": source_id, "sync_mode": sync_mode, "message": "Sync triggered"}
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Data source trigger sync failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to trigger sync: {e}")
    return {"success": True, "source_id": source_id, "sync_mode": sync_mode, "message": "Sync triggered (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}/status
# ---------------------------------------------------------------------------
@router.get("/{source_id}/status")
async def get_data_source_status(source_id: str):
    """Get data source status."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id)
            if source:
                return {
                    "source_id": source_id,
                    "status": source.get("status", "idle"),
                    "last_sync": source.get("last_sync_at"),
                    "documents_synced": source.get("last_sync_result", {}).get("documents_synced", 0),
                }
        except Exception as e:
            logger.warning("Data source status query failed: %s", e)
    return {"source_id": source_id, "status": "idle", "last_sync": None, "documents_synced": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/file-ingest
# ---------------------------------------------------------------------------
@router.post("/file-ingest")
async def trigger_file_ingest(body: dict[str, Any]):
    """Trigger file ingest."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source_name = body.get("source_name", "file-upload")
            source = await repo.get_by_name(source_name)
            if source:
                await repo.update_status(source["id"], "syncing")
            return {"success": True, "message": "File ingest triggered"}
        except Exception as e:
            logger.warning("File ingest trigger failed: %s", e)
    return {"success": True, "message": "File ingest triggered"}
