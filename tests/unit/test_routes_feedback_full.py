"""Unit tests for src/api/routes/feedback.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import feedback as feedback_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    return state


def _make_app():
    app = FastAPI()
    app.include_router(feedback_mod.admin_router)
    app.include_router(feedback_mod.knowledge_router)
    return app


# ============================================================================
# List Feedback (GET /api/v1/admin/feedback/list)
# ============================================================================

class TestListFeedback:
    def test_list_feedback_with_repo(self):
        repo = AsyncMock()
        repo.list_all = AsyncMock(return_value=[{"id": "f1", "type": "upvote"}])
        repo.count = AsyncMock(return_value=1)

        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.list_feedback(page=1, page_size=20))
        assert result["total"] == 1
        assert len(result["feedback"]) == 1

    def test_list_feedback_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.list_feedback(page=1, page_size=20))
        assert result["total"] == 0
        assert result["feedback"] == []

    def test_list_feedback_repo_exception(self):
        repo = AsyncMock()
        repo.list_all = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.list_feedback(page=1, page_size=20))
        assert result["total"] == 0

    def test_list_feedback_with_filters(self):
        repo = AsyncMock()
        repo.list_all = AsyncMock(return_value=[])
        repo.count = AsyncMock(return_value=0)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.list_feedback(status="pending", feedback_type="upvote", page=2, page_size=10))
        assert result["page"] == 2
        repo.list_all.assert_called_once_with(status="pending", feedback_type="upvote", limit=10, offset=10)


# ============================================================================
# Create Feedback (POST /api/v1/knowledge/feedback)
# ============================================================================

class TestCreateFeedback:
    def test_create_with_repo(self):
        repo = AsyncMock()
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.create_feedback({"entry_id": "e1", "feedback_type": "upvote"}))
        assert result["success"] is True
        assert "feedback_id" in result

    def test_create_with_custom_id(self):
        repo = AsyncMock()
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.create_feedback({"id": "custom_id"}))
        assert result["feedback_id"] == "custom_id"

    def test_create_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.create_feedback({"entry_id": "e1"}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_create_repo_exception(self):
        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=RuntimeError("db error"))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.create_feedback({"entry_id": "e1"}))
            # HTTPException from FastAPI
            assert "500" in str(exc_info.value.status_code) or exc_info.value.status_code == 500


# ============================================================================
# Update Feedback (PATCH /api/v1/admin/feedback/{feedback_id})
# ============================================================================

class TestUpdateFeedback:
    def test_update_with_repo(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "f1", "status": "pending"})
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.update_feedback("f1", {"status": "resolved"}))
        assert result["success"] is True

    def test_update_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.update_feedback("f1", {"status": "resolved"}))
            assert exc_info.value.status_code == 404

    def test_update_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.update_feedback("f1", {"status": "resolved"}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_update_repo_save_exception(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "f1"})
        repo.save = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.update_feedback("f1", {"status": "resolved"}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Feedback Stats (GET /api/v1/admin/feedback/stats)
# ============================================================================

class TestFeedbackStats:
    def test_stats_with_repo(self):
        repo = AsyncMock()
        repo.count = AsyncMock(side_effect=lambda status=None, feedback_type=None: {
            (None, None): 10,
            ("pending", None): 3,
            (None, "upvote"): 5,
            (None, "downvote"): 2,
        }.get((status, feedback_type), 0))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.get_feedback_stats())
        assert result["total"] == 10
        assert result["pending"] == 3
        assert result["positive"] == 5
        assert result["negative"] == 2
        assert result["neutral"] == 3  # 10 - 5 - 2

    def test_stats_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.get_feedback_stats())
        assert result["total"] == 0

    def test_stats_exception(self):
        repo = AsyncMock()
        repo.count = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.get_feedback_stats())
        assert result["total"] == 0


# ============================================================================
# Workflow Stats (GET /api/v1/admin/feedback/workflow-stats)
# ============================================================================

class TestWorkflowStats:
    def test_workflow_stats(self):
        repo = AsyncMock()
        repo.count = AsyncMock(side_effect=lambda status=None: {
            "pending": 2, "in_review": 1, "resolved": 5, "rejected": 3,
        }.get(status, 0))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(feedback_repo=repo)):
            result = _run(feedback_mod.get_feedback_workflow_stats())
        assert result["pending"] == 2
        assert result["resolved"] == 5

    def test_workflow_stats_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.get_feedback_workflow_stats())
        assert result["pending"] == 0


# ============================================================================
# Error Reports
# ============================================================================

class TestListErrorReports:
    def test_list_with_repo(self):
        repo = AsyncMock()
        repo.get_open_reports = AsyncMock(return_value=[
            {"id": "r1", "status": "pending", "error_type": "ocr"},
        ])
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.list_error_reports(kb_id=None, status=None, page=1, page_size=20))
        assert result["total"] == 1

    def test_list_with_status_filter(self):
        repo = AsyncMock()
        repo.get_open_reports = AsyncMock(return_value=[
            {"id": "r1", "status": "pending"},
            {"id": "r2", "status": "resolved"},
        ])
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.list_error_reports(kb_id=None, status="pending", page=1, page_size=20))
        assert result["total"] == 1
        assert result["reports"][0]["id"] == "r1"

    def test_list_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.list_error_reports(kb_id=None, status=None, page=1, page_size=20))
        assert result["total"] == 0


class TestGetErrorReport:
    def test_get_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "r1", "status": "pending"})
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.get_error_report("r1"))
        assert result["id"] == "r1"

    def test_get_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.get_error_report("r1"))
            assert exc_info.value.status_code == 404

    def test_get_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.get_error_report("r1"))
            assert exc_info.value.status_code == 404


class TestErrorReportStatistics:
    def test_statistics(self):
        repo = AsyncMock()
        repo.get_open_reports = AsyncMock(return_value=[
            {"error_type": "ocr", "status": "pending"},
            {"error_type": "ocr", "status": "resolved"},
            {"error_type": "parse", "status": "pending"},
        ])
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.get_error_report_statistics(kb_id=None, days=30))
        assert result["total"] == 3
        assert result["by_type"]["ocr"] == 2
        assert result["by_status"]["pending"] == 2

    def test_statistics_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.get_error_report_statistics(kb_id=None, days=30))
        assert result["total"] == 0


class TestCreateErrorReport:
    def test_create_with_repo(self):
        repo = AsyncMock()
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.create_error_report({"error_type": "ocr"}))
        assert result["success"] is True

    def test_create_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.create_error_report({"error_type": "ocr"}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_create_repo_exception(self):
        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.create_error_report({"error_type": "ocr"}))
            assert exc_info.value.status_code == 500


class TestResolveErrorReport:
    def test_resolve_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "r1", "status": "pending"})
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.resolve_error_report("r1", {"resolution_note": "Fixed"}))
        assert result["status"] == "resolved"

    def test_resolve_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.resolve_error_report("r1", {}))
            assert exc_info.value.status_code == 404

    def test_resolve_no_repo(self):
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state()):
            result = _run(feedback_mod.resolve_error_report("r1", {}))
        assert result["status"] == "resolved"


class TestRejectErrorReport:
    def test_reject_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "r1"})
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.reject_error_report("r1", {"reason": "Not valid"}))
        assert result["status"] == "rejected"

    def test_reject_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.reject_error_report("r1", {}))
            assert exc_info.value.status_code == 404


class TestEscalateErrorReport:
    def test_escalate_success(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"id": "r1"})
        repo.save = AsyncMock()
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            result = _run(feedback_mod.escalate_error_report("r1", {"assigned_to": "admin"}))
        assert result["status"] == "escalated"

    def test_escalate_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(feedback_mod, "_get_state", return_value=_mock_state(error_report_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(feedback_mod.escalate_error_report("r1", {}))
            assert exc_info.value.status_code == 404


class TestLearningArtifacts:
    def test_get_learning_artifacts(self):
        result = _run(feedback_mod.get_learning_artifacts(page=1, page_size=20))
        assert result["total"] == 0
        assert result["artifacts"] == []
