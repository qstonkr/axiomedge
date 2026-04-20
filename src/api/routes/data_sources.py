"""Data Sources API endpoints - wired to DataSourceRepository.

모든 핸들러가 ``OrgContext`` 를 주입받아 ``organization_id`` 를 repo 호출에
전달 — cross-tenant 누설 차단 (0005_data_source_org_required.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.app import _get_state
from src.auth.dependencies import OrgContext, get_current_org

logger = logging.getLogger(__name__)

_DS_NOT_FOUND = "Data source not found"
_background_tasks: set[asyncio.Task] = set()  # prevent premature GC of fire-and-forget tasks
router = APIRouter(prefix="/api/v1/admin/data-sources", tags=["Data Sources"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.get("")
async def list_data_sources(
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """List data sources — caller's org scope only."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            sources = await repo.list(organization_id=org.id)
            return {"sources": sources, "total": len(sources)}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo list failed: %s", e)
    return {"sources": [], "total": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.post("", responses={500: {"description": "Failed to create data source"}})
async def create_data_source(
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Create a data source — automatically scoped to caller's org."""
    state = _get_state()
    repo = state.get("data_source_repo")
    source_id = body.get("id") or str(uuid.uuid4())
    if repo:
        try:
            data = dict(body)
            data.setdefault("id", source_id)
            data.setdefault("status", "active")
            # body 가 organization_id 보내도 무시 — caller 의 org 로 강제.
            data.pop("organization_id", None)
            await repo.register(data, organization_id=org.id)
            return {"success": True, "source_id": source_id, "message": "Data source created"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo register failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Data source created (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.get("/{source_id}", responses={404: {"description": "Data source not found"}})
async def get_data_source(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get data source — cross-org 시 404 (존재 누설 X)."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id, organization_id=org.id)
            if source:
                return source
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo get failed: %s", e)
    raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.put("/{source_id}", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to update data source"}})  # noqa: E501
async def update_data_source(
    source_id: str,
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Update data source — cross-org 시 404."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            existing = await repo.get(source_id, organization_id=org.id)
            if not existing:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            # Update status if provided, otherwise keep current
            status = body.get("status", existing.get("status", "active"))
            error_message = body.get("error_message")
            updated = await repo.update_status(
                source_id, status, organization_id=org.id, error_message=error_message,
            )
            if not updated:
                # update 사이에 cross-org delete 가 끼었거나 race — 404 로 매핑.
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            return {"success": True, "source_id": source_id, "message": "Updated"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo update failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Updated (stub - no DB)"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.delete("/{source_id}", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to delete data source"}})  # noqa: E501
async def delete_data_source(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, bool | str]:
    """Delete data source — cross-org 시 404."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            deleted = await repo.delete(source_id, organization_id=org.id)
            if not deleted:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            return {"success": True, "source_id": source_id}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo delete failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to delete data source: {e}")
    return {"success": True, "source_id": source_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/{source_id}/trigger
# ---------------------------------------------------------------------------
@router.post("/{source_id}/trigger", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to trigger sync"}})  # noqa: E501
async def trigger_data_source_sync(
    source_id: str,
    sync_mode: Annotated[str, Query()] = "resume",
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Trigger data source sync (crawl → ingest pipeline) — caller org scope."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            existing = await repo.get(source_id, organization_id=org.id)
            if not existing:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            await repo.update_status(source_id, "syncing", organization_id=org.id)

            # Launch background sync task — existing dict carries organization_id
            # so the sync runner can pass it through to repo.complete_sync().
            from src.api.routes.data_source_sync import run_data_source_sync
            task = asyncio.create_task(
                run_data_source_sync(existing, state, sync_mode=sync_mode),
                name=f"ds-sync-{source_id[:8]}",
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            return {
                "success": True,
                "source_id": source_id,
                "sync_mode": sync_mode,
                "message": "Sync triggered — crawling and ingestion started in background",
            }
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source trigger sync failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to trigger sync: {e}")
    return {"success": True, "source_id": source_id, "sync_mode": sync_mode, "message": "Sync triggered (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}/status
# ---------------------------------------------------------------------------
@router.get("/{source_id}/status")
async def get_data_source_status(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get data source status — cross-org 시 idle/null fallback."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id, organization_id=org.id)
            if source:
                return {
                    "source_id": source_id,
                    "status": source.get("status", "idle"),
                    "last_sync": source.get("last_sync_at"),
                    "documents_synced": source.get("last_sync_result", {}).get("documents_synced", 0),
                }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source status query failed: %s", e)
    return {"source_id": source_id, "status": "idle", "last_sync": None, "documents_synced": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/file-ingest
# ---------------------------------------------------------------------------
@router.post("/file-ingest")
async def trigger_file_ingest(
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Trigger file ingest — caller org scope."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source_name = body.get("source_name", "file-upload")
            source = await repo.get_by_name(source_name, organization_id=org.id)
            if source:
                await repo.update_status(
                    source["id"], "syncing", organization_id=org.id,
                )
            return {"success": True, "message": "File ingest triggered"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("File ingest trigger failed: %s", e)
    return {"success": True, "message": "File ingest triggered"}
