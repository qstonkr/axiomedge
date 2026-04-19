"""Feedback & Error Reports API endpoints - stub routes for dashboard compatibility."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.api.app import _get_state
from src.core.models import FeedbackType

logger = logging.getLogger(__name__)

_ERR_REPORT_NOT_FOUND = "Error report not found"

# Frontend (Next.js) sends uppercase enum (``SUGGESTION``) and the
# UI-only token ``ERROR_REPORT`` which maps to backend's ``report``. Accept
# any case + the alias so we don't 500 on perfectly reasonable client input.
_FEEDBACK_TYPE_ALIAS = {
    "error_report": "report",
    "errorreport": "report",
}


def _normalize_feedback_type(raw: Any) -> str:
    key = str(raw or "general").strip().lower()
    key = _FEEDBACK_TYPE_ALIAS.get(key, key)
    try:
        return FeedbackType(key).value
    except ValueError:
        # 알 수 없는 type → ``general`` 로 흡수 (저장은 됨, admin 이 분류).
        logger.warning("unknown feedback_type %r — coerced to 'general'", raw)
        return FeedbackType.GENERAL.value


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
    status: Annotated[str | None, Query()] = None,
    feedback_type: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List feedback."""
    state = _get_state()
    repo = state.get("feedback_repo")
    if repo:
        try:
            offset = (page - 1) * page_size
            items = await repo.list_all(status=status, feedback_type=feedback_type, limit=page_size, offset=offset)
            total = await repo.count(status=status)
            return {"feedback": items, "total": total, "page": page, "page_size": page_size}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback repo query failed: %s", e)
    return {"feedback": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/feedback
# ---------------------------------------------------------------------------
@knowledge_router.post(
    "/feedback",
    responses={500: {"description": "Failed to create feedback"}},
)
async def create_feedback(body: dict[str, Any]) -> dict[str, Any]:
    """Create feedback."""
    state = _get_state()
    repo = state.get("feedback_repo")
    feedback_id = body.get("id") or str(uuid.uuid4())
    if repo:
        try:
            # ``dict.get(key, default)`` 는 key 가 **있고 None 이면** None 반환
            # (default 미적용). 클라이언트가 ``"document_id": null`` 같이 보내면
            # NOT NULL 컬럼 충돌로 500. ``or`` 체인으로 None/빈문자열 모두 흡수.
            feedback_data = {
                "id": feedback_id,
                "entry_id": (
                    body.get("entry_id") or body.get("document_id") or "unknown"
                ),
                "kb_id": body.get("kb_id") or "default",
                "user_id": (
                    body.get("user_id") or body.get("reporter") or "anonymous"
                ),
                "feedback_type": _normalize_feedback_type(
                    body.get("feedback_type") or body.get("type"),
                ),
                "status": body.get("status") or "pending",
                "description": (
                    body.get("description") or body.get("content") or ""
                ),
                "error_category": body.get("error_category"),
                "suggested_content": body.get("suggested_content"),
            }
            await repo.save(feedback_data)
            return {"success": True, "feedback_id": feedback_id, "message": "Feedback recorded"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback repo save failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create feedback: {e}")
    return {"success": True, "feedback_id": feedback_id, "message": "Feedback recorded (stub - no DB)"}


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/feedback/{feedback_id}
# ---------------------------------------------------------------------------
@admin_router.patch(
    "/feedback/{feedback_id}",
    responses={
        404: {"description": "Feedback not found"},
        500: {"description": "Failed to update feedback"},
    },
)
async def update_feedback(feedback_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update feedback."""
    state = _get_state()
    repo = state.get("feedback_repo")
    if repo:
        try:
            existing = await repo.get_by_id(feedback_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Feedback not found")
            update_data = dict(body)
            update_data["id"] = feedback_id
            await repo.save(update_data)
            return {"success": True, "feedback_id": feedback_id, "message": "Feedback updated"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback repo update failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update feedback: {e}")
    return {"success": True, "feedback_id": feedback_id, "message": "Feedback updated (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback/stats
# ---------------------------------------------------------------------------
@admin_router.get("/feedback/stats")
async def get_feedback_stats() -> dict[str, Any]:
    """Get feedback stats."""
    state = _get_state()
    repo = state.get("feedback_repo")
    if repo:
        try:
            total = await repo.count()
            pending = await repo.count(status="pending")
            # Use count with feedback_type filters instead of loading all items
            positive = await repo.count(feedback_type=FeedbackType.UPVOTE)
            negative = await repo.count(feedback_type=FeedbackType.DOWNVOTE)
            neutral = max(0, total - positive - negative)
            by_type = {"upvote": positive, "downvote": negative, "other": neutral}
            return {"total": total, "pending": pending, "positive": positive, "negative": negative, "neutral": neutral, "by_type": by_type}  # noqa: E501
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback stats query failed: %s", e)
    return {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "by_type": {}}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback/workflow-stats
# ---------------------------------------------------------------------------
@admin_router.get("/feedback/workflow-stats")
async def get_feedback_workflow_stats() -> dict[str, int]:
    """Get feedback workflow stats."""
    state = _get_state()
    repo = state.get("feedback_repo")
    if repo:
        try:
            pending = await repo.count(status="pending")
            in_review = await repo.count(status="in_review")
            resolved = await repo.count(status="resolved")
            rejected = await repo.count(status="rejected")
            return {"pending": pending, "in_review": in_review, "resolved": resolved, "rejected": rejected}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Feedback workflow stats failed: %s", e)
    return {"pending": 0, "in_review": 0, "resolved": 0, "rejected": 0}


# ============================================================================
# Error Reports
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports
# ---------------------------------------------------------------------------
@admin_router.get("/error-reports")
async def list_error_reports(
    kb_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """List error reports."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            reports = await repo.get_open_reports(kb_id=kb_id)
            # Filter by status if provided
            if status:
                reports = [r for r in reports if r.get("status") == status]
            total = len(reports)
            offset = (page - 1) * page_size
            paged = reports[offset:offset + page_size]
            return {"reports": paged, "total": total, "page": page, "page_size": page_size}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report repo query failed: %s", e)
    return {"reports": [], "total": 0, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports/statistics
# NOTE: Must be defined BEFORE /error-reports/{report_id} to avoid path capture
# ---------------------------------------------------------------------------
@admin_router.get("/error-reports/statistics")
async def get_error_report_statistics(
    kb_id: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1)] = 30,
) -> dict[str, Any]:
    """Get error report statistics."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            reports = await repo.get_open_reports(kb_id=kb_id)
            by_type: dict[str, int] = {}
            by_status: dict[str, int] = {}
            for r in reports:
                et = r.get("error_type", "unknown")
                by_type[et] = by_type.get(et, 0) + 1
                st = r.get("status", "unknown")
                by_status[st] = by_status.get(st, 0) + 1
            return {"total": len(reports), "by_type": by_type, "by_status": by_status, "trend": []}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report statistics failed: %s", e)
    return {"total": 0, "by_type": {}, "by_status": {}, "trend": []}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/error-reports/{report_id}
# ---------------------------------------------------------------------------
@admin_router.get(
    "/error-reports/{report_id}",
    responses={404: {"description": "Error report not found"}},
)
async def get_error_report(report_id: str) -> dict[str, Any]:
    """Get error report."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            report = await repo.get_by_id(report_id)
            if report:
                return report
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report repo get failed: %s", e)
    raise HTTPException(status_code=404, detail=_ERR_REPORT_NOT_FOUND)


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/report-error
# ---------------------------------------------------------------------------
@knowledge_router.post(
    "/report-error",
    responses={500: {"description": "Failed to create error report"}},
)
async def create_error_report(body: dict[str, Any]) -> dict[str, Any]:
    """Create error report."""
    state = _get_state()
    repo = state.get("error_report_repo")
    report_id = body.get("id") or str(uuid.uuid4())
    if repo:
        try:
            # 같은 None-aware 패턴 (위 create_feedback 주석 참조).
            report_data = {
                "id": report_id,
                "document_id": (
                    body.get("document_id") or body.get("message_id") or "unknown"
                ),
                "kb_id": body.get("kb_id") or body.get("kb") or "default",
                "error_type": (
                    body.get("error_type") or body.get("type") or "incorrect_answer"
                ),
                "description": (
                    body.get("description")
                    or body.get("title")
                    or body.get("content")
                    or ""
                ),
                "reporter_user_id": (
                    body.get("reporter_user_id")
                    or body.get("reporter")
                    or body.get("session_id")
                    or "anonymous"
                ),
                "status": body.get("status") or "pending",
                "priority": body.get("priority") or "medium",
            }
            await repo.save(report_data)
            return {"success": True, "report_id": report_id, "message": "Error reported"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report repo save failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create error report: {e}")
    return {"success": True, "report_id": report_id, "message": "Error reported (stub - no DB)"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/resolve
# ---------------------------------------------------------------------------
@admin_router.post(
    "/error-reports/{report_id}/resolve",
    responses={
        404: {"description": "Error report not found"},
        500: {"description": "Failed to resolve report"},
    },
)
async def resolve_error_report(report_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Resolve error report."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            existing = await repo.get_by_id(report_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_ERR_REPORT_NOT_FOUND)
            from datetime import UTC, datetime
            update_data = {
                "id": report_id,
                "status": "resolved",
                "resolution_note": body.get("resolution_note", ""),
                "resolved_at": datetime.now(UTC).isoformat(),
            }
            await repo.save(update_data)
            return {"success": True, "report_id": report_id, "status": "resolved"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report resolve failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to resolve report: {e}")
    return {"success": True, "report_id": report_id, "status": "resolved"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/reject
# ---------------------------------------------------------------------------
@admin_router.post(
    "/error-reports/{report_id}/reject",
    responses={
        404: {"description": "Error report not found"},
        500: {"description": "Failed to reject report"},
    },
)
async def reject_error_report(report_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Reject error report."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            existing = await repo.get_by_id(report_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_ERR_REPORT_NOT_FOUND)
            update_data = {"id": report_id, "status": "rejected", "resolution_note": body.get("reason", "")}
            await repo.save(update_data)
            return {"success": True, "report_id": report_id, "status": "rejected"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report reject failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to reject report: {e}")
    return {"success": True, "report_id": report_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/error-reports/{report_id}/escalate
# ---------------------------------------------------------------------------
@admin_router.post(
    "/error-reports/{report_id}/escalate",
    responses={
        404: {"description": "Error report not found"},
        500: {"description": "Failed to escalate report"},
    },
)
async def escalate_error_report(report_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Escalate error report."""
    state = _get_state()
    repo = state.get("error_report_repo")
    if repo:
        try:
            existing = await repo.get_by_id(report_id)
            if not existing:
                raise HTTPException(status_code=404, detail=_ERR_REPORT_NOT_FOUND)
            update_data = {
                "id": report_id,
                "status": "escalated",
                "assigned_to": body.get("assigned_to"),
            }
            await repo.save(update_data)
            return {"success": True, "report_id": report_id, "status": "escalated"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Error report escalate failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to escalate report: {e}")
    return {"success": True, "report_id": report_id, "status": "escalated"}


# ============================================================================
# Learning Artifacts
# ============================================================================

# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/learning/low-confidence
# ---------------------------------------------------------------------------
@admin_router.get("/kb/learning/low-confidence")
async def get_learning_artifacts(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Get low-confidence learning artifacts."""
    return {
        "artifacts": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
    }
