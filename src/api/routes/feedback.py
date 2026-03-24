"""Feedback & Error Reports API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query

from src.api.app import _get_state

logger = logging.getLogger(__name__)

# Two routers: admin feedback + knowledge feedback/error-report
admin_router = APIRouter(prefix="/api/v1/admin", tags=["Feedback"])
knowledge_router = APIRouter(prefix="/api/v1/knowledge", tags=["Feedback"])


# ============================================================================
# Feedback
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback/list
# ---------------------------------------------------------------------------
@admin_router.get("/feedback/list")
async def list_feedback(
    status: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List feedback."""
    state = _get_state()
    repo = state.get("feedback_repo")
    if repo:
        try:
            offset = (page - 1) * page_size
            items = await repo.list_all(status=status, feedback_type=feedback_type, limit=page_size, offset=offset)
            total = await repo.count(status=status)
            return {"feedback": items, "total": total, "page": page, "page_size": page_size}
        except Exception as e:
            logger.warning("Feedback repo query failed: %s", e)
    return {"feedback": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/feedback
# ---------------------------------------------------------------------------
@knowledge_router.post("/feedback")
async def create_feedback(body: dict[str, Any]):
    """Create feedback."""
    feedback_id = str(uuid.uuid4())
    return {"success": True, "feedback_id": feedback_id, "message": "Feedback recorded"}


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/feedback/{feedback_id}
# ---------------------------------------------------------------------------
@admin_router.patch("/feedback/{feedback_id}")
async def update_feedback(feedback_id: str, body: dict[str, Any]):
    """Update feedback."""
    return {"success": True, "feedback_id": feedback_id, "message": "Feedback updated"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback/stats
# ---------------------------------------------------------------------------
@admin_router.get("/feedback/stats")
async def get_feedback_stats():
    """Get feedback stats."""
    return {
        "total": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "by_type": {},
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback/workflow-stats
# ---------------------------------------------------------------------------
@admin_router.get("/feedback/workflow-stats")
async def get_feedback_workflow_stats():
    """Get feedback workflow stats."""
    return {
        "pending": 0,
        "in_review": 0,
        "resolved": 0,
        "rejected": 0,
    }


# ============================================================================
# Error Reports
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports
# ---------------------------------------------------------------------------
@admin_router.get("/error-reports")
async def list_error_reports(
    kb_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List error reports."""
    return {
        "reports": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports/{report_id}
# ---------------------------------------------------------------------------
@admin_router.get("/error-reports/{report_id}")
async def get_error_report(report_id: str):
    """Get error report."""
    return {
        "report_id": report_id,
        "status": "unknown",
        "error_type": None,
        "description": "",
        "created_at": None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports/statistics
# ---------------------------------------------------------------------------
@admin_router.get("/error-reports/statistics")
async def get_error_report_statistics(
    kb_id: str | None = Query(default=None),
    days: int = Query(default=30, ge=1),
):
    """Get error report statistics."""
    return {
        "total": 0,
        "by_type": {},
        "by_status": {},
        "trend": [],
    }


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/report-error
# ---------------------------------------------------------------------------
@knowledge_router.post("/report-error")
async def create_error_report(body: dict[str, Any]):
    """Create error report."""
    report_id = str(uuid.uuid4())
    return {"success": True, "report_id": report_id, "message": "Error reported"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/resolve
# ---------------------------------------------------------------------------
@admin_router.post("/error-reports/{report_id}/resolve")
async def resolve_error_report(report_id: str, body: dict[str, Any]):
    """Resolve error report."""
    return {"success": True, "report_id": report_id, "status": "resolved"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/reject
# ---------------------------------------------------------------------------
@admin_router.post("/error-reports/{report_id}/reject")
async def reject_error_report(report_id: str, body: dict[str, Any]):
    """Reject error report."""
    return {"success": True, "report_id": report_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/escalate
# ---------------------------------------------------------------------------
@admin_router.post("/error-reports/{report_id}/escalate")
async def escalate_error_report(report_id: str, body: dict[str, Any]):
    """Escalate error report."""
    return {"success": True, "report_id": report_id, "status": "escalated"}


# ============================================================================
# Learning Artifacts
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/learning/low-confidence
# ---------------------------------------------------------------------------
@admin_router.get("/kb/learning/low-confidence")
async def get_learning_artifacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Get low-confidence learning artifacts."""
    return {
        "artifacts": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }
