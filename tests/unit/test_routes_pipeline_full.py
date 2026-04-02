"""Unit tests for src/api/routes/pipeline.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import pipeline as pipeline_mod


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


# ============================================================================
# Pipeline Status
# ============================================================================

class TestPipelineStatus:
    def test_status_with_repo_active_runs(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {"status": "running", "started_at": "2024-01-01"},
            {"status": "completed"},
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_status())
        assert result["status"] == "running"
        assert result["active_runs"] == 1

    def test_status_idle(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {"status": "completed"},
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_status())
        assert result["status"] == "idle"

    def test_status_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_pipeline_status())
        assert result["status"] == "idle"

    def test_status_pending(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {"status": "pending"},
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_status())
        assert result["status"] == "running"
        assert result["queued"] == 1

    def test_status_exception(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_status())
        assert result["status"] == "idle"


# ============================================================================
# Pipeline Metrics
# ============================================================================

class TestPipelineMetrics:
    def test_metrics_with_runs(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {
                "status": "completed",
                "documents_ingested": 10,
                "chunks_stored": 50,
                "started_at": "2024-01-01T00:00:00",
                "completed_at": "2024-01-01T00:01:00",
            },
            {
                "status": "failed",
                "documents_ingested": 0,
                "chunks_stored": 0,
            },
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_metrics())
        assert result["total_runs"] == 2
        assert result["successful_runs"] == 1
        assert result["failed_runs"] == 1
        assert result["total_documents_processed"] == 10
        assert result["average_duration_seconds"] == 60.0

    def test_metrics_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_pipeline_metrics())
        assert result["total_runs"] == 0

    def test_metrics_exception(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_metrics())
        assert result["total_runs"] == 0


# ============================================================================
# Pipeline Run Detail
# ============================================================================

class TestPipelineRunDetail:
    def test_detail_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"run_id": "r1", "status": "completed"})
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_run_detail("r1"))
        assert result["run_id"] == "r1"

    def test_detail_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_pipeline_run_detail("r1"))
        assert result["status"] == "unknown"

    def test_detail_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_pipeline_run_detail("r1"))
        assert result["status"] == "unknown"


# ============================================================================
# Latest Experiment Run
# ============================================================================

class TestLatestExperimentRun:
    def test_latest_found(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[{"run_id": "r1", "status": "completed", "created_at": "2024-01-01"}])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_latest_experiment_run("kb1"))
        assert result["run_id"] == "r1"

    def test_latest_none(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_latest_experiment_run("kb1"))
        assert result["status"] == "none"

    def test_latest_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_latest_experiment_run("kb1"))
        assert result["status"] == "none"


# ============================================================================
# KB Sync
# ============================================================================

class TestKbSync:
    def test_trigger_sync(self):
        result = _run(pipeline_mod.trigger_kb_sync("kb1", {"source": "s3"}))
        assert result["success"] is True
        assert result["kb_id"] == "kb1"

    def test_validate_sync(self):
        result = _run(pipeline_mod.validate_kb_sync("kb1", {"source": "s3"}))
        assert result["valid"] is True


# ============================================================================
# Publish
# ============================================================================

class TestPublish:
    def test_dry_run(self):
        result = _run(pipeline_mod.publish_experiment_dry_run({"kb_id": "kb1"}))
        assert result["success"] is True

    def test_execute(self):
        result = _run(pipeline_mod.publish_experiment_execute({"kb_id": "kb1"}))
        assert result["success"] is True


# ============================================================================
# Gates
# ============================================================================

class TestGates:
    def test_gates_stats(self):
        result = _run(pipeline_mod.get_pipeline_gates_stats())
        assert result["total_blocked"] == 0

    def test_gates_blocked(self):
        result = _run(pipeline_mod.get_pipeline_gates_blocked())
        assert result["total"] == 0

    def test_gate_blocked(self):
        result = _run(pipeline_mod.get_pipeline_gate_blocked("gate1"))
        assert result["gate_id"] == "gate1"


# ============================================================================
# Ingestion Runs
# ============================================================================

class TestIngestionRuns:
    def test_list_all(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[{"run_id": "r1", "status": "completed"}])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.list_ingestion_runs(kb_id=None, status=None, page=1, page_size=20))
        assert result["total"] == 1

    def test_list_by_kb(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[{"run_id": "r1"}])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.list_ingestion_runs(kb_id="kb1", status=None, page=1, page_size=20))
        assert result["total"] == 1

    def test_list_with_status_filter(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {"run_id": "r1", "status": "completed"},
            {"run_id": "r2", "status": "failed"},
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.list_ingestion_runs(kb_id=None, status="completed", page=1, page_size=20))
        assert result["total"] == 1

    def test_list_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.list_ingestion_runs(kb_id=None, status=None, page=1, page_size=20))
        assert result["total"] == 0


# ============================================================================
# Ingestion Run Status
# ============================================================================

class TestIngestionRunStatus:
    def test_get_run_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value={"run_id": "r1", "status": "completed"})
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_ingestion_run("r1"))
        assert result["status"] == "completed"

    def test_get_run_not_found(self):
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_ingestion_run("r1"))
        assert result["status"] == "unknown"


# ============================================================================
# Trigger / Cancel Ingestion
# ============================================================================

class TestTriggerCancel:
    def test_trigger(self):
        result = _run(pipeline_mod.trigger_ingestion({"kb_id": "kb1"}))
        assert result["success"] is True

    def test_cancel(self):
        result = _run(pipeline_mod.cancel_ingestion("r1"))
        assert result["success"] is True


# ============================================================================
# Ingestion Stats
# ============================================================================

class TestIngestionStats:
    def test_stats_with_repo(self):
        repo = AsyncMock()
        repo.list_recent = AsyncMock(return_value=[
            {"status": "completed", "documents_ingested": 10, "chunks_stored": 50},
            {"status": "failed", "documents_ingested": 0, "chunks_stored": 0},
        ])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_ingestion_stats(kb_id=None))
        assert result["total_runs"] == 2
        assert result["successful"] == 1
        assert result["total_documents"] == 10

    def test_stats_by_kb(self):
        repo = AsyncMock()
        repo.list_by_kb = AsyncMock(return_value=[])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(ingestion_run_repo=repo)):
            result = _run(pipeline_mod.get_ingestion_stats(kb_id="kb1"))
        assert result["total_runs"] == 0

    def test_stats_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_ingestion_stats(kb_id=None))
        assert result["total_runs"] == 0


# ============================================================================
# Schedules
# ============================================================================

class TestSchedules:
    def test_list(self):
        result = _run(pipeline_mod.list_ingestion_schedules())
        assert result["total"] == 0


# ============================================================================
# Categories
# ============================================================================

class TestCategories:
    def test_list_categories(self):
        repo = AsyncMock()
        repo.get_l1_categories = AsyncMock(return_value=[{"name": "IT운영"}, {"name": "보안"}])
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(category_repo=repo)):
            result = _run(pipeline_mod.list_l1_categories())
        assert result["total"] == 2

    def test_list_categories_no_repo(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.list_l1_categories())
        assert result["total"] == 0

    def test_list_categories_exception(self):
        repo = AsyncMock()
        repo.get_l1_categories = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(category_repo=repo)):
            result = _run(pipeline_mod.list_l1_categories())
        assert result["total"] == 0


# ============================================================================
# L1 Category Stats
# ============================================================================

class TestL1Stats:
    def test_no_store(self):
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state()):
            result = _run(pipeline_mod.get_l1_stats())
        assert result["total_docs"] == 0

    def test_with_store(self):
        store = AsyncMock()
        store.facet_l1_categories = AsyncMock(side_effect=[
            {"IT운영": 5, "보안": 3, "기타": 2},
        ])
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["kb_itops__live"])
        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections._provider = provider

        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(
            qdrant_store=store, qdrant_collections=collections
        )):
            result = _run(pipeline_mod.get_l1_stats())
        assert result["total_docs"] == 10
        assert result["etc_count"] == 2

    def test_with_store_exception(self):
        store = AsyncMock()
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(pipeline_mod, "_get_state", return_value=_mock_state(
            qdrant_store=store, qdrant_collections=collections
        )):
            result = _run(pipeline_mod.get_l1_stats())
        assert result["total_docs"] == 0
