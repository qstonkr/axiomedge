"""Pipeline & Ingestion management API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

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
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            recent = await repo.list_recent(limit=10)
            active = [r for r in recent if r.get("status") in ("running", "pending")]
            last_run = recent[0] if recent else None
            return {
                "status": "running" if active else "idle",
                "active_runs": len(active),
                "queued": len([r for r in active if r.get("status") == "pending"]),
                "last_run": last_run,
            }
        except Exception as e:
            logger.warning("Pipeline status query failed: %s", e)
    return {"status": "idle", "active_runs": 0, "queued": 0, "last_run": None}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/metrics
# ---------------------------------------------------------------------------
@router.get("/pipeline/metrics")
async def get_pipeline_metrics():
    """Get pipeline metrics."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            runs = await repo.list_recent(limit=1000)
            total_runs = len(runs)
            successful = [r for r in runs if r.get("status") == "completed"]
            failed = [r for r in runs if r.get("status") == "failed"]
            total_docs = sum(r.get("documents_ingested", 0) for r in runs)
            total_chunks = sum(r.get("chunks_stored", 0) for r in runs)

            durations = []
            for r in successful:
                started = r.get("started_at")
                completed = r.get("completed_at")
                if started and completed:
                    from datetime import datetime
                    if isinstance(started, str):
                        started = datetime.fromisoformat(started)
                    if isinstance(completed, str):
                        completed = datetime.fromisoformat(completed)
                    try:
                        diff = (completed - started).total_seconds()
                        durations.append(diff)
                    except (TypeError, AttributeError):
                        pass
            avg_duration = sum(durations) / len(durations) if durations else 0

            return {
                "total_runs": total_runs,
                "successful_runs": len(successful),
                "failed_runs": len(failed),
                "average_duration_seconds": round(avg_duration, 1),
                "total_documents_processed": total_docs,
                "total_chunks_created": total_chunks,
            }
        except Exception as e:
            logger.warning("Pipeline metrics query failed: %s", e)
    return {
        "total_runs": 0, "successful_runs": 0, "failed_runs": 0,
        "average_duration_seconds": 0, "total_documents_processed": 0, "total_chunks_created": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/runs/{run_id}
# ---------------------------------------------------------------------------
@router.get("/pipeline/runs/{run_id}")
async def get_pipeline_run_detail(run_id: str):
    """Get pipeline run detail."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            run = await repo.get_by_id(run_id)
            if run:
                return run
        except Exception as e:
            logger.warning("Pipeline run detail query failed: %s", e)
    return {
        "run_id": run_id, "status": "unknown", "started_at": None,
        "completed_at": None, "documents_processed": 0, "chunks_created": 0, "errors": [],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/experiments/{kb_id}/latest
# ---------------------------------------------------------------------------
@router.get("/pipeline/experiments/{kb_id}/latest")
async def get_latest_experiment_run(kb_id: str):
    """Get latest experiment run for a KB."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            runs = await repo.list_by_kb(kb_id, limit=1)
            if runs:
                latest = runs[0]
                return {
                    "kb_id": kb_id,
                    "run_id": latest.get("run_id"),
                    "status": latest.get("status", "none"),
                    "created_at": latest.get("created_at"),
                }
        except Exception as e:
            logger.warning("Latest experiment run query failed: %s", e)
    return {"kb_id": kb_id, "run_id": None, "status": "none", "created_at": None}


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
# GET /api/v1/admin/pipeline/gates/blocked
# ---------------------------------------------------------------------------
@router.get("/pipeline/gates/blocked")
async def get_pipeline_gates_blocked():
    """Get all documents blocked by ingestion gates."""
    return {
        "blocked_documents": [],
        "total": 0,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/pipeline/gates/{gate_id}/blocked
# ---------------------------------------------------------------------------
@router.get("/pipeline/gates/{gate_id}/blocked")
async def get_pipeline_gate_blocked(gate_id: str):
    """Get documents blocked by a specific gate."""
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
    kb_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
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
            # Apply status filter if provided
            if status:
                runs = [r for r in runs if r.get("status") == status]
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
    kb_id: Annotated[str | None, Query()] = None,
):
    """Get ingestion stats."""
    state = _get_state()
    repo = state.get("ingestion_run_repo")
    if repo:
        try:
            if kb_id:
                runs = await repo.list_by_kb(kb_id, limit=1000)
            else:
                runs = await repo.list_recent(limit=1000)
            successful = [r for r in runs if r.get("status") == "completed"]
            failed = [r for r in runs if r.get("status") == "failed"]
            total_docs = sum(r.get("documents_ingested", 0) for r in runs)
            total_chunks = sum(r.get("chunks_stored", 0) for r in runs)
            return {
                "total_runs": len(runs),
                "successful": len(successful),
                "failed": len(failed),
                "total_documents": total_docs,
                "total_chunks": total_chunks,
            }
        except Exception as e:
            logger.warning("Ingestion stats query failed: %s", e)
    return {"total_runs": 0, "successful": 0, "failed": 0, "total_documents": 0, "total_chunks": 0}


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
    state = _get_state()
    repo = state.get("category_repo")
    if repo:
        try:
            categories = await repo.get_l1_categories()
            return {"categories": categories, "total": len(categories)}
        except Exception as e:
            logger.warning("Category repo query failed: %s", e)
    return {"categories": [], "total": 0}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/categories/stats
# ---------------------------------------------------------------------------
@router.get("/categories/stats")
async def get_l1_stats():
    """Get L1 category stats aggregated from Qdrant facet API."""
    state = _get_state()
    store = state.get("qdrant_store")
    collections = state.get("qdrant_collections")

    if not store or not collections:
        return {"l1_counts": {}, "total_docs": 0, "etc_count": 0, "etc_ratio": 0.0,
                "kb_breakdown": []}

    try:
        # Discover all KB collections
        raw_names = await collections.get_existing_collection_names()
        prefix = getattr(collections._provider.config, "collection_prefix", "kb") + "_"
        kb_ids = []
        for name in raw_names:
            if name.endswith("__live"):
                kb_id = name[len(prefix):-len("__live")] if name.startswith(prefix) else name
            elif name.startswith(prefix):
                kb_id = name[len(prefix):]
            else:
                continue
            kb_ids.append(kb_id)
        # Deduplicate (live alias + base collection may resolve to same kb_id)
        kb_ids = sorted(set(kb_ids))

        # Aggregate l1_category facets across all KBs
        l1_totals: dict[str, int] = {}
        kb_breakdown: list[list] = []

        results = await asyncio.gather(
            *(store.facet_l1_categories(kb_id) for kb_id in kb_ids),
            return_exceptions=True,
        )

        for kb_id, result in zip(kb_ids, results):
            if isinstance(result, Exception):
                logger.debug("L1 facet failed for %s: %s", kb_id, result)
                continue
            for cat, count in result.items():
                l1_totals[cat] = l1_totals.get(cat, 0) + count
                kb_breakdown.append([kb_id, cat, count])

        total_docs = sum(l1_totals.values())
        etc_count = l1_totals.get("기타", 0)
        etc_ratio = etc_count / total_docs if total_docs > 0 else 0.0

        return {
            "l1_counts": l1_totals,
            "total_docs": total_docs,
            "etc_count": etc_count,
            "etc_ratio": etc_ratio,
            "kb_breakdown": kb_breakdown,
        }
    except Exception as e:
        logger.warning("L1 stats aggregation failed: %s", e)
        return {"l1_counts": {}, "total_docs": 0, "etc_count": 0, "etc_ratio": 0.0,
                "kb_breakdown": []}
