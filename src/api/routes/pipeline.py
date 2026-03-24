"""Pipeline & Ingestion management API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Pipeline"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/status
# ---------------------------------------------------------------------------
@router.get("/pipeline/status")
async def get_pipeline_status():
    """Get pipeline status."""
    return {
        "status": "idle",
        "active_runs": 0,
        "queued": 0,
        "last_run": None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/metrics
# ---------------------------------------------------------------------------
@router.get("/pipeline/metrics")
async def get_pipeline_metrics():
    """Get pipeline metrics."""
    return {
        "total_runs": 0,
        "successful_runs": 0,
        "failed_runs": 0,
        "average_duration_seconds": 0,
        "total_documents_processed": 0,
        "total_chunks_created": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/runs/{run_id}
# ---------------------------------------------------------------------------
@router.get("/pipeline/runs/{run_id}")
async def get_pipeline_run_detail(run_id: str):
    """Get pipeline run detail."""
    return {
        "run_id": run_id,
        "status": "unknown",
        "started_at": None,
        "completed_at": None,
        "documents_processed": 0,
        "chunks_created": 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/experiments/{kb_id}/latest
# ---------------------------------------------------------------------------
@router.get("/pipeline/experiments/{kb_id}/latest")
async def get_latest_experiment_run(kb_id: str):
    """Get latest experiment run for a KB."""
    return {
        "kb_id": kb_id,
        "run_id": None,
        "status": "none",
        "created_at": None,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/{kb_id}/sync
# ---------------------------------------------------------------------------
@router.post("/kb/{kb_id}/sync")
async def trigger_kb_sync(kb_id: str, body: dict[str, Any]):
    """Trigger KB sync."""
    run_id = str(uuid.uuid4())
    return {
        "success": True,
        "kb_id": kb_id,
        "run_id": run_id,
        "message": "Sync triggered",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/{kb_id}/sync/validate
# ---------------------------------------------------------------------------
@router.post("/kb/{kb_id}/sync/validate")
async def validate_kb_sync(kb_id: str, body: dict[str, Any]):
    """Validate KB sync config."""
    return {
        "valid": True,
        "kb_id": kb_id,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/pipeline/publish/dry-run
# ---------------------------------------------------------------------------
@router.post("/pipeline/publish/dry-run")
async def publish_experiment_dry_run(body: dict[str, Any]):
    """Dry-run publish experiment."""
    return {
        "success": True,
        "kb_id": body.get("kb_id", ""),
        "would_publish": 0,
        "would_remove": 0,
        "diff": [],
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/pipeline/publish/execute
# ---------------------------------------------------------------------------
@router.post("/pipeline/publish/execute")
async def publish_experiment_execute(body: dict[str, Any]):
    """Execute publish experiment."""
    return {
        "success": True,
        "kb_id": body.get("kb_id", ""),
        "published": 0,
        "removed": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/gates/stats
# ---------------------------------------------------------------------------
@router.get("/pipeline/gates/stats")
async def get_pipeline_gates_stats():
    """Get pipeline gates stats."""
    return {
        "gates": [],
        "total_blocked": 0,
        "total_passed": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/gates/{gate_id}/blocked
# ---------------------------------------------------------------------------
@router.get("/pipeline/gates/{gate_id}/blocked")
async def get_pipeline_gate_blocked(gate_id: str):
    """Get documents blocked by a gate."""
    return {
        "gate_id": gate_id,
        "blocked_documents": [],
        "total": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/ingest/jobs
# ---------------------------------------------------------------------------
@router.get("/knowledge/ingest/jobs")
async def list_ingestion_runs(
    kb_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List ingestion runs."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            if kb_id:
                runs = await repo.list_by_kb(kb_id, limit=page_size)
            else:
                runs = await repo.list_recent(limit=page_size)
            return {"runs": runs, "total": len(runs), "page": page, "page_size": page_size}
        except Exception as e:
            logger.warning("Ingestion run repo query failed: %s", e)
    return {"runs": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/knowledge/ingest/status/{run_id}
# ---------------------------------------------------------------------------
@router.get("/knowledge/ingest/status/{run_id}")
async def get_ingestion_run(run_id: str):
    """Get ingestion run status."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            run = await repo.get_by_id(run_id)
            if run:
                return run
        except Exception as e:
            logger.warning("Ingestion run repo get failed: %s", e)
    return {
        "run_id": run_id,
        "status": "unknown",
        "started_at": None,
        "completed_at": None,
        "documents_processed": 0,
        "chunks_created": 0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/knowledge/ingest
# ---------------------------------------------------------------------------
@router.post("/knowledge/ingest")
async def trigger_ingestion(body: dict[str, Any]):
    """Trigger ingestion."""
    run_id = str(uuid.uuid4())
    return {
        "success": True,
        "run_id": run_id,
        "message": "Ingestion triggered",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/knowledge/ingest/jobs/{run_id}/cancel
# ---------------------------------------------------------------------------
@router.post("/knowledge/ingest/jobs/{run_id}/cancel")
async def cancel_ingestion(run_id: str):
    """Cancel an ingestion run."""
    return {"success": True, "run_id": run_id, "message": "Cancelled"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ingestion/stats
# ---------------------------------------------------------------------------
@router.get("/ingestion/stats")
async def get_ingestion_stats(
    kb_id: str | None = Query(default=None),
):
    """Get ingestion stats."""
    return {
        "total_runs": 0,
        "successful": 0,
        "failed": 0,
        "total_documents": 0,
        "total_chunks": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/ingestion/schedules
# ---------------------------------------------------------------------------
@router.get("/ingestion/schedules")
async def list_ingestion_schedules():
    """List ingestion schedules."""
    return {"schedules": [], "total": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/categories
# ---------------------------------------------------------------------------
@router.get("/categories")
async def list_l1_categories():
    """List L1 categories."""
    return {"categories": [], "total": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/categories/stats
# ---------------------------------------------------------------------------
@router.get("/categories/stats")
async def get_l1_stats():
    """Get L1 category stats."""
    return {
        "categories": [],
        "total_documents": 0,
        "uncategorized": 0,
    }
