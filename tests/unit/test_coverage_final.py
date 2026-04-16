"""Comprehensive coverage tests targeting uncovered lines across multiple modules.

Targets ~800+ lines across:
- Database repositories (data_source, feedback, ingestion_run, lifecycle, search_group,
  traceability, trust_score, usage_log)
- Auth (dependencies, role_service, service)
- Cache (l2_semantic_cache)
- Routes (search_groups)
- CV pipeline (arrow_detector, ocr_with_coords)
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

_TEST_UUID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_repo(RepoClass):
    """Create a repo with a mocked async session."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    # Make add() a plain MagicMock (not AsyncMock) so repos don't need await
    session.add = MagicMock()
    maker = MagicMock(return_value=session)
    return RepoClass(maker), session


def _mock_scalar_one_or_none(session, value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    session.execute = AsyncMock(return_value=result)


def _mock_scalars_all(session, values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)


def _mock_scalar(session, value):
    result = MagicMock()
    result.scalar.return_value = value
    session.execute = AsyncMock(return_value=result)


# ---------------------------------------------------------------------------
# data_source.py
# ---------------------------------------------------------------------------

class TestDataSourceRepository:
    def test_safe_json_loads_empty(self):
        from src.stores.postgres.repositories.data_source import _safe_json_loads
        assert _safe_json_loads(None) == {}
        assert _safe_json_loads("") == {}
        assert _safe_json_loads(None, []) == []

    def test_safe_json_loads_valid(self):
        from src.stores.postgres.repositories.data_source import _safe_json_loads
        assert _safe_json_loads('{"a": 1}') == {"a": 1}

    def test_safe_json_loads_invalid(self):
        from src.stores.postgres.repositories.data_source import _safe_json_loads
        assert _safe_json_loads("not-json") == {}
        assert _safe_json_loads("not-json", {"default": True}) == {"default": True}

    def test_utc_now(self):
        from src.stores.postgres.repositories.data_source import _utc_now
        now = _utc_now()
        assert now.tzinfo is not None

    def test_register(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        data = {
            "id": "ds-1", "name": "test", "source_type": "file", "kb_id": "kb-1",
            "crawl_config": {"url": "http://example.com"},
            "pipeline_config": {"max_pages": 10},
            "last_sync_result": {"status": "ok"},
            "metadata": {"tag": "v1"},
        }
        with patch("src.stores.postgres.repositories.data_source.DataSourceModel"):
            result = _run(repo.register(data))
        assert result == data
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    def test_register_rollback_on_error(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(DataSourceRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with patch("src.stores.postgres.repositories.data_source.DataSourceModel"):
            with pytest.raises(SQLAlchemyError):
                _run(repo.register({"id": "ds-1"}))
        session.rollback.assert_awaited_once()

    def test_get_found(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        model = MagicMock()
        model.id = "ds-1"
        model.name = "test"
        model.source_type = "file"
        model.kb_id = "kb-1"
        model.crawl_config = None
        model.pipeline_config = None
        model.schedule = None
        model.status = "active"
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = None
        model.last_sync_at = None
        model.last_sync_result = None
        model.error_message = None
        model.metadata_ = None
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get("ds-1"))
        assert result["id"] == "ds-1"

    def test_get_not_found(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.get("nonexistent"))
        assert result is None

    def test_get_by_name(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.get_by_name("test"))
        assert result is None

    def test_list_with_filters(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.list(source_type="file", status="active"))
        assert result == []

    def test_list_no_filters(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.list())
        assert result == []

    def test_update_status(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        _run(repo.update_status("ds-1", "error", "something broke"))
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    def test_update_status_rollback(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(DataSourceRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.update_status("ds-1", "error"))
        session.rollback.assert_awaited_once()

    def test_delete_found(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        result_mock = MagicMock()
        result_mock.rowcount = 1
        session.execute = AsyncMock(return_value=result_mock)
        assert _run(repo.delete("ds-1")) is True

    def test_delete_not_found(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        repo, session = _make_repo(DataSourceRepository)
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute = AsyncMock(return_value=result_mock)
        assert _run(repo.delete("ds-1")) is False

    def test_to_dict(self):
        from src.stores.postgres.repositories.data_source import DataSourceRepository
        model = MagicMock()
        model.id = "ds-1"
        model.name = "test"
        model.source_type = "web"
        model.kb_id = "kb-1"
        model.crawl_config = '{"a":1}'
        model.pipeline_config = '{"b":2}'
        model.schedule = "daily"
        model.status = "active"
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = None
        model.last_sync_at = None
        model.last_sync_result = '{"ok":true}'
        model.error_message = None
        model.metadata_ = '{"tag":"v1"}'
        d = DataSourceRepository._to_dict(model)
        assert d["crawl_config"] == {"a": 1}
        assert d["metadata"] == {"tag": "v1"}


# ---------------------------------------------------------------------------
# feedback.py
# ---------------------------------------------------------------------------

class TestFeedbackRepository:
    def _make_model(self, **overrides):
        m = MagicMock()
        m.id = "fb-1"
        m.entry_id = "e-1"
        m.kb_id = "kb-1"
        m.user_id = "u-1"
        m.feedback_type = "upvote"
        m.status = "pending"
        m.error_category = None
        m.description = "good"
        m.suggested_content = None
        m.reviewer_id = None
        m.review_note = None
        m.reviewed_at = None
        m.kts_impact = None
        m.created_at = datetime.now(timezone.utc)
        m.updated_at = None
        for k, v in overrides.items():
            setattr(m, k, v)
        return m

    def test_save_new(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar_one_or_none(session, None)  # no existing
        _run(repo.save({"id": "fb-1", "entry_id": "e-1"}))
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_save_update_existing(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        existing = self._make_model()
        _mock_scalar_one_or_none(session, existing)
        _run(repo.save({"id": "fb-1", "status": "approved"}))
        session.commit.assert_awaited()

    def test_save_rollback(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar_one_or_none(session, None)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.save({"id": "fb-1"}))
        session.rollback.assert_awaited_once()

    def test_get_by_id_found(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get_by_id("fb-1"))
        assert result["id"] == "fb-1"

    def test_get_by_id_not_found(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_id("nonexistent")) is None

    def test_get_by_entry(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.get_by_entry("e-1", "kb-1"))
        assert len(result) == 1

    def test_get_by_user(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_by_user("u-1"))
        assert result == []

    def test_get_pending_reviews_with_kb(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_pending_reviews(kb_id="kb-1"))
        assert result == []

    def test_get_pending_reviews_no_kb(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_pending_reviews())
        assert result == []

    def test_get_votes_for_entry(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        # Need two execute calls - upvotes then downvotes
        r1 = MagicMock()
        r1.scalar.return_value = 5
        r2 = MagicMock()
        r2.scalar.return_value = 2
        session.execute = AsyncMock(side_effect=[r1, r2])
        up, down = _run(repo.get_votes_for_entry("e-1", "kb-1"))
        assert up == 5
        assert down == 2

    def test_list_all_with_filters(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.list_all(status="pending", feedback_type="upvote"))
        assert result == []

    def test_count_with_filters(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar(session, 10)
        result = _run(repo.count(status="pending", feedback_type="upvote"))
        assert result == 10

    def test_count_none(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar(session, None)
        result = _run(repo.count())
        assert result == 0

    def test_delete_found(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.delete("fb-1"))
        assert result is True
        session.delete.assert_awaited_once()

    def test_delete_not_found(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository
        repo, session = _make_repo(FeedbackRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.delete("nonexistent"))
        assert result is False


# ---------------------------------------------------------------------------
# ingestion_run.py
# ---------------------------------------------------------------------------

class TestIngestionRunRepository:
    def test_create(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        data = {
            "id": "run-1", "kb_id": "kb-1",
            "errors": ["err1", "err2"],
            "metadata": {"key": "val"},
        }
        with patch("src.stores.postgres.repositories.ingestion_run.IngestionRunModel"):
            _run(repo.create(data))
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    def test_create_rollback(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(IngestionRunRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with patch("src.stores.postgres.repositories.ingestion_run.IngestionRunModel"):
            with pytest.raises(SQLAlchemyError):
                _run(repo.create({"id": "run-1"}))

    def test_complete(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        data = {
            "status": "completed",
            "errors": ["e1"],
            "metadata": {"dur": 100},
        }
        _run(repo.complete("run-1", data))
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    def test_complete_with_completed_at(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        now = datetime.now(timezone.utc)
        _run(repo.complete("run-1", {"status": "done", "completed_at": now}))
        session.commit.assert_awaited_once()

    def test_complete_rollback(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(IngestionRunRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.complete("run-1", {"status": "failed"}))

    def _make_model(self):
        m = MagicMock()
        m.id = "run-1"
        m.kb_id = "kb-1"
        m.source_type = "file"
        m.source_name = "test.pdf"
        m.status = "completed"
        m.version_fingerprint = "abc"
        m.documents_fetched = 5
        m.documents_ingested = 4
        m.documents_held = 0
        m.documents_rejected = 1
        m.chunks_stored = 20
        m.chunks_deduped = 2
        m.errors = '["err1"]'
        m.run_metadata = '{"key":"val"}'
        m.started_at = datetime.now(timezone.utc)
        m.completed_at = datetime.now(timezone.utc)
        m.created_at = datetime.now(timezone.utc)
        return m

    def test_get_by_id_found(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get_by_id("run-1"))
        assert result["id"] == "run-1"
        assert result["errors"] == ["err1"]

    def test_get_by_id_not_found(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_id("nonexistent")) is None

    def test_get_by_id_error(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(IngestionRunRepository)
        session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
        assert _run(repo.get_by_id("run-1")) is None

    def test_list_by_kb(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.list_by_kb("kb-1"))
        assert len(result) == 1

    def test_list_by_kb_error(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(IngestionRunRepository)
        session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
        assert _run(repo.list_by_kb("kb-1")) == []

    def test_list_recent(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        repo, session = _make_repo(IngestionRunRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.list_recent())
        assert result == []

    def test_list_recent_error(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(IngestionRunRepository)
        session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
        assert _run(repo.list_recent()) == []

    def test_to_dict_bad_json(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        model = self._make_model()
        model.errors = "not-json"
        model.run_metadata = "not-json"
        d = IngestionRunRepository._to_dict(model)
        assert d["errors"] == []
        assert d["metadata"] == {}

    def test_to_dict_none_fields(self):
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        model = self._make_model()
        model.status = None
        model.documents_fetched = None
        model.documents_ingested = None
        model.documents_held = None
        model.documents_rejected = None
        model.chunks_stored = None
        model.chunks_deduped = None
        model.errors = None
        model.run_metadata = None
        d = IngestionRunRepository._to_dict(model)
        assert d["status"] == "running"
        assert d["documents_fetched"] == 0


# ---------------------------------------------------------------------------
# lifecycle.py
# ---------------------------------------------------------------------------

class TestDocumentLifecycleRepository:
    def _make_model(self):
        m = MagicMock()
        m.id = "lc-1"
        m.document_id = "doc-1"
        m.kb_id = "kb-1"
        m.status = "active"
        m.previous_status = None
        m.status_changed_at = None
        m.status_changed_by = None
        m.auto_archive_at = None
        m.deletion_scheduled_at = None
        m.created_at = datetime.now(timezone.utc)
        m.updated_at = None
        return m

    def test_save_new(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        _mock_scalar_one_or_none(session, None)
        _run(repo.save({"id": "lc-1", "document_id": "doc-1", "kb_id": "kb-1"}))
        session.add.assert_called()
        session.commit.assert_awaited()

    def test_save_update_existing(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        existing = self._make_model()
        _mock_scalar_one_or_none(session, existing)
        _run(repo.save({"document_id": "doc-1", "kb_id": "kb-1", "status": "archived"}))
        session.commit.assert_awaited()

    def test_save_with_transitions(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        existing = self._make_model()
        # First execute returns existing model, second returns count
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = existing
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        session.execute = AsyncMock(side_effect=[result1, count_result])
        transitions = [{"from_status": "draft", "to_status": "active", "transitioned_by": "u-1"}]
        with patch("src.stores.postgres.repositories.lifecycle.LifecycleTransitionModel"):
            _run(repo.save({
                "document_id": "doc-1", "kb_id": "kb-1",
                "transitions": transitions,
            }))
        session.commit.assert_awaited()

    def test_save_rollback(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(DocumentLifecycleRepository)
        _mock_scalar_one_or_none(session, None)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.save({"id": "lc-1", "document_id": "doc-1", "kb_id": "kb-1"}))

    def test_get_by_document_found(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        model = self._make_model()
        # First execute for the model, second for transitions
        r1 = MagicMock()
        r1.scalar_one_or_none.return_value = model
        r2 = MagicMock()
        r2.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(repo.get_by_document("doc-1", "kb-1"))
        assert result["document_id"] == "doc-1"
        assert result["transitions"] == []

    def test_get_by_document_not_found(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_document("doc-1", "kb-1")) is None

    def test_list_by_kb(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        # First call returns models, subsequent calls return transitions for each
        r1 = MagicMock()
        r1.scalars.return_value.all.return_value = [self._make_model()]
        r2 = MagicMock()
        r2.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(repo.list_by_kb("kb-1"))
        assert len(result) == 1

    def test_list_by_status(self):
        from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
        repo, session = _make_repo(DocumentLifecycleRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.list_by_status("kb-1", "active"))
        assert result == []


# ---------------------------------------------------------------------------
# search_group.py
# ---------------------------------------------------------------------------

class TestSearchGroupRepository:
    def _make_model(self):
        m = MagicMock()
        m.id = _TEST_UUID
        m.name = "test-group"
        m.description = "desc"
        m.kb_ids = ["kb-1", "kb-2"]
        m.is_default = False
        m.created_by = "u-1"
        m.created_at = datetime.now(timezone.utc)
        m.updated_at = datetime.now(timezone.utc)
        return m

    def test_create(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        model = self._make_model()
        session.refresh = AsyncMock()
        with patch("src.stores.postgres.repositories.search_group.KBSearchGroupModel", return_value=model):
            result = _run(repo.create("test-group", ["kb-1"]))
        assert result["name"] == "test-group"

    def test_get_found(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get(_TEST_UUID))
        assert result["name"] == "test-group"

    def test_get_not_found(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get(str(uuid.uuid4()))) is None

    def test_get_by_name(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_name("test")) is None

    def test_get_default(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        model = self._make_model()
        model.is_default = True
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get_default())
        assert result["is_default"] is True

    def test_list_all(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.list_all())
        assert len(result) == 1

    def test_update_with_default(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        model = self._make_model()
        # First execute clears other defaults, second does the update
        r1 = MagicMock()  # clear defaults
        r2 = MagicMock()  # update + returning
        r2.scalar_one_or_none.return_value = model
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(repo.update(_TEST_UUID, name="new-name", is_default=True, kb_ids=["kb-3"], description="new"))
        assert result is not None

    def test_update_not_found(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        r1 = MagicMock()
        r1.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=r1)
        result = _run(repo.update(_TEST_UUID, name="new-name"))
        assert result is None

    def test_delete_found(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        result_mock = MagicMock()
        result_mock.rowcount = 1
        session.execute = AsyncMock(return_value=result_mock)
        assert _run(repo.delete(_TEST_UUID)) is True

    def test_delete_not_found(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute = AsyncMock(return_value=result_mock)
        assert _run(repo.delete(_TEST_UUID)) is False

    def test_resolve_kb_ids_by_id(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.resolve_kb_ids(group_id=_TEST_UUID))
        assert result == ["kb-1", "kb-2"]

    def test_resolve_kb_ids_by_name(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.resolve_kb_ids(group_name="test"))
        assert result == []

    def test_resolve_kb_ids_default(self):
        from src.stores.postgres.repositories.search_group import SearchGroupRepository
        repo, session = _make_repo(SearchGroupRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.resolve_kb_ids())
        assert result == []


# ---------------------------------------------------------------------------
# traceability.py (ProvenanceRepository)
# ---------------------------------------------------------------------------

class TestProvenanceRepository:
    def _make_model(self):
        m = MagicMock()
        m.id = "prov-1"
        m.knowledge_id = "k-1"
        m.kb_id = "kb-1"
        m.ingestion_run_id = "run-1"
        m.source_type = "file"
        m.source_url = "http://example.com"
        m.source_id = "s-1"
        m.source_system = "local"
        m.crawled_at = None
        m.crawled_by = None
        m.extraction_metadata = '{"tool":"ocr"}'
        m.original_author = "author"
        m.original_created_at = None
        m.original_modified_at = None
        m.contributors = '["a","b"]'
        m.verification_status = "verified"
        m.quality_score = 0.9
        m.content_hash = "abc123"
        m.created_at = datetime.now(timezone.utc)
        m.updated_at = None
        return m

    def test_save(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        data = {
            "knowledge_id": "k-1", "kb_id": "kb-1",
            "extraction_metadata": {"tool": "ocr"},
            "contributors": ["a", "b"],
        }
        _run(repo.save(data))
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    def test_save_with_id(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        data = {
            "id": "prov-existing",
            "knowledge_id": "k-1", "kb_id": "kb-1",
        }
        _run(repo.save(data))
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    def test_save_rollback(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(ProvenanceRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.save({"knowledge_id": "k-1", "kb_id": "kb-1"}))

    def test_upsert_new(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        _mock_scalar_one_or_none(session, None)
        result = _run(repo.upsert({
            "knowledge_id": "k-1", "kb_id": "kb-1",
            "extraction_metadata": {"tool": "ocr"},
            "contributors": ["a"],
        }))
        assert result is None  # no previous hash
        session.add.assert_called_once()

    def test_upsert_existing(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        existing = self._make_model()
        existing.content_hash = "old-hash"
        _mock_scalar_one_or_none(session, existing)
        result = _run(repo.upsert({
            "knowledge_id": "k-1", "kb_id": "kb-1",
            "extraction_metadata": {"tool": "v2"},
            "contributors": ["c"],
            "content_hash": "new-hash",
        }))
        assert result == "old-hash"

    def test_upsert_rollback(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(ProvenanceRepository)
        _mock_scalar_one_or_none(session, None)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.upsert({"knowledge_id": "k-1", "kb_id": "kb-1"}))

    def test_get_by_knowledge_id(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get_by_knowledge_id("k-1"))
        assert result["knowledge_id"] == "k-1"
        assert result["extraction_metadata"] == {"tool": "ocr"}
        assert result["contributors"] == ["a", "b"]

    def test_get_by_knowledge_and_kb(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_knowledge_and_kb("k-1", "kb-1")) is None

    def test_get_by_source(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.get_by_source("file", "s-1"))
        assert len(result) == 1

    def test_get_by_run_id(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        repo, session = _make_repo(ProvenanceRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_by_run_id("run-1"))
        assert result == []

    def test_to_dict_bad_json(self):
        from src.stores.postgres.repositories.traceability import ProvenanceRepository
        model = self._make_model()
        model.extraction_metadata = "not-json"
        model.contributors = "not-json"
        d = ProvenanceRepository._to_dict(model)
        assert d["extraction_metadata"] is None
        assert d["contributors"] == []


# ---------------------------------------------------------------------------
# trust_score.py
# ---------------------------------------------------------------------------

class TestTrustScoreRepository:
    def _make_model(self):
        m = MagicMock()
        m.id = "ts-1"
        m.entry_id = "e-1"
        m.kb_id = "kb-1"
        m.kts_score = 0.85
        m.confidence_tier = "high"
        m.source_credibility = 0.9
        m.freshness_score = 0.7
        m.user_validation_score = 0.8
        m.usage_score = 0.6
        m.hallucination_score = 0.1
        m.consistency_score = 0.95
        m.source_type = "document"
        m.freshness_domain = "standard"
        m.upvotes = 10
        m.downvotes = 2
        m.expert_reviews = 1
        m.open_error_reports = 0
        m.view_count = 100
        m.citation_count = 5
        m.bookmark_count = 3
        m.last_evaluated_at = None
        m.created_at = datetime.now(timezone.utc)
        m.updated_at = datetime.now(timezone.utc)
        return m

    def test_save_new(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalar_one_or_none(session, None)
        _run(repo.save({"entry_id": "e-1", "kb_id": "kb-1", "kts_score": 0.8}))
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_save_update(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        existing = self._make_model()
        _mock_scalar_one_or_none(session, existing)
        _run(repo.save({"entry_id": "e-1", "kb_id": "kb-1", "kts_score": 0.9}))
        session.commit.assert_awaited()

    def test_save_rollback(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalar_one_or_none(session, None)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with pytest.raises(SQLAlchemyError):
            _run(repo.save({"entry_id": "e-1", "kb_id": "kb-1"}))

    def test_get_by_entry_found(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        result = _run(repo.get_by_entry("e-1", "kb-1"))
        assert result["kts_score"] == 0.85

    def test_get_by_entry_not_found(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.get_by_entry("e-1", "kb-1")) is None

    def test_get_by_kb(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.get_by_kb("kb-1", sort="top"))
        assert len(result) == 1

    def test_get_by_kb_recent(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_by_kb("kb-1", sort="recent"))
        assert result == []

    def test_get_by_kb_trending(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_by_kb("kb-1", sort="trending"))
        assert result == []

    def test_get_stale_entries(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_stale_entries("kb-1"))
        assert result == []

    def test_get_needs_review_with_kb(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_needs_review(kb_id="kb-1"))
        assert result == []

    def test_get_needs_review_no_kb(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalars_all(session, [])
        result = _run(repo.get_needs_review())
        assert result == []

    def test_delete_found(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        model = self._make_model()
        _mock_scalar_one_or_none(session, model)
        assert _run(repo.delete("e-1", "kb-1")) is True

    def test_delete_not_found(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        repo, session = _make_repo(TrustScoreRepository)
        _mock_scalar_one_or_none(session, None)
        assert _run(repo.delete("e-1", "kb-1")) is False

    def test_sort_expression(self):
        from src.stores.postgres.repositories.trust_score import TrustScoreRepository
        # Just ensure no exceptions
        TrustScoreRepository._sort_expression("top")
        TrustScoreRepository._sort_expression("recent")
        TrustScoreRepository._sort_expression("trending")


# ---------------------------------------------------------------------------
# usage_log.py
# ---------------------------------------------------------------------------

class TestUsageLogRepository:
    def _make_model(self):
        m = MagicMock()
        m.id = "ul-1"
        m.knowledge_id = "k-1"
        m.kb_id = "kb-1"
        m.usage_type = "hub_search"
        m.user_id = "u-1"
        m.session_id = "sess-1"
        m.context = '{"total_chunks": 10, "search_time_ms": 50.0}'
        m.created_at = datetime.now(timezone.utc)
        return m

    def test_log_search(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        repo, session = _make_repo(UsageLogRepository)
        with patch("src.stores.postgres.repositories.usage_log.UsageLogModel"):
            _run(repo.log_search("k-1", "kb-1", user_id="u-1", context={"q": "test"}))
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_log_search_error(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        from sqlalchemy.exc import SQLAlchemyError
        repo, session = _make_repo(UsageLogRepository)
        session.commit = AsyncMock(side_effect=SQLAlchemyError("fail"))
        with patch("src.stores.postgres.repositories.usage_log.UsageLogModel"):
            _run(repo.log_search("k-1", "kb-1"))  # should not raise
        session.rollback.assert_awaited()

    def test_list_recent(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        repo, session = _make_repo(UsageLogRepository)
        model = self._make_model()
        # execute is called twice: count then rows
        r1 = MagicMock()
        r1.scalar.return_value = 1
        r2 = MagicMock()
        r2.scalars.return_value.all.return_value = [model]
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(repo.list_recent())
        assert result["total"] == 1
        assert len(result["searches"]) == 1

    def test_get_analytics(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        repo, session = _make_repo(UsageLogRepository)
        # 5 execute calls: total, unique_users, top_queries, top_kbs, sample_contexts
        r_total = MagicMock()
        r_total.scalar.return_value = 10
        r_unique = MagicMock()
        r_unique.scalar.return_value = 5
        r_top_queries = MagicMock()
        r_top_queries.__iter__ = MagicMock(return_value=iter([
            MagicMock(knowledge_id="q1", count=5),
        ]))
        r_top_kbs = MagicMock()
        r_top_kbs.__iter__ = MagicMock(return_value=iter([
            MagicMock(kb_id="kb-1", count=8),
        ]))
        r_sample = MagicMock()
        r_sample.scalars.return_value.all.return_value = [
            '{"total_chunks": 10, "search_time_ms": 50.0}',
            '{"total_chunks": 20, "search_time_ms": 100.0}',
            "not-json",
        ]
        session.execute = AsyncMock(side_effect=[r_total, r_unique, r_top_queries, r_top_kbs, r_sample])
        result = _run(repo.get_analytics(days=7))
        assert result["total_searches"] == 10
        assert result["unique_users"] == 5
        assert result["avg_results_per_query"] == 15.0  # (10+20)/2
        assert result["avg_response_time_ms"] == 75.0   # (50+100)/2

    def test_get_analytics_zero_searches(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        repo, session = _make_repo(UsageLogRepository)
        r_total = MagicMock()
        r_total.scalar.return_value = 0
        r_unique = MagicMock()
        r_unique.scalar.return_value = 0
        r_top_queries = MagicMock()
        r_top_queries.__iter__ = MagicMock(return_value=iter([]))
        r_top_kbs = MagicMock()
        r_top_kbs.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[r_total, r_unique, r_top_queries, r_top_kbs])
        result = _run(repo.get_analytics())
        assert result["avg_results_per_query"] == 0.0

    def test_get_by_user(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        repo, session = _make_repo(UsageLogRepository)
        _mock_scalars_all(session, [self._make_model()])
        result = _run(repo.get_by_user("u-1"))
        assert len(result) == 1

    def test_to_dict_bad_context(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        model = self._make_model()
        model.context = "not-json"
        d = UsageLogRepository._to_dict(model)
        assert d["context"] == {}

    def test_to_dict_none_context(self):
        from src.stores.postgres.repositories.usage_log import UsageLogRepository
        model = self._make_model()
        model.context = None
        d = UsageLogRepository._to_dict(model)
        assert d["context"] == {}


# ---------------------------------------------------------------------------
# auth/dependencies.py
# ---------------------------------------------------------------------------

class TestAuthDependencies:
    def _make_request(self, headers=None, cookies=None, path_params=None, app_state=None):
        request = MagicMock()
        request.headers = headers or {}
        request.cookies = cookies or {}
        request.path_params = path_params or {}
        if app_state is not None:
            request.app.state._app_state = app_state
        else:
            request.app.state._app_state = None
        return request

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_bearer(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        app_state = {"auth_provider": auth_provider, "auth_service": None}
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_apikey(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        app_state = {"auth_provider": auth_provider, "auth_service": None}
        request = self._make_request(
            headers={"Authorization": "ApiKey key123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_x_api_key(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        app_state = {"auth_provider": auth_provider, "auth_service": None}
        request = self._make_request(
            headers={"X-API-Key": "key123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_cookie(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        app_state = {"auth_provider": auth_provider, "auth_service": None}
        request = self._make_request(
            headers={},
            cookies={"access_token": "tok123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_no_token(self):
        from src.auth.dependencies import get_current_user
        from fastapi import HTTPException
        request = self._make_request(headers={})
        with pytest.raises(HTTPException) as exc_info:
            _run(get_current_user(request))
        assert exc_info.value.status_code == 401

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_no_state(self):
        from src.auth.dependencies import get_current_user
        from fastapi import HTTPException
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state=None,
        )
        # _get_app_state returns None when no _app_state
        with pytest.raises(HTTPException) as exc_info:
            _run(get_current_user(request))
        assert exc_info.value.status_code == 503

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_no_provider(self):
        from src.auth.dependencies import get_current_user
        from fastapi import HTTPException
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state={"auth_provider": None},
        )
        with pytest.raises(HTTPException) as exc_info:
            _run(get_current_user(request))
        assert exc_info.value.status_code == 503

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_with_sync(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        auth_service = AsyncMock()
        auth_service.sync_user_from_idp = AsyncMock()
        app_state = {"auth_provider": auth_provider, "auth_service": auth_service}
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"
        auth_service.sync_user_from_idp.assert_awaited_once()

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_sync_failure_non_blocking(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="keycloak")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        auth_service = AsyncMock()
        auth_service.sync_user_from_idp = AsyncMock(side_effect=Exception("sync failed"))
        app_state = {"auth_provider": auth_provider, "auth_service": auth_service}
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_internal_provider_skips_sync(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="test@test.com", display_name="Test", provider="internal")
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        auth_service = AsyncMock()
        app_state = {"auth_provider": auth_provider, "auth_service": auth_service}
        request = self._make_request(
            headers={"Authorization": "Bearer tok123"},
            app_state=app_state,
        )
        result = _run(get_current_user(request))
        assert result.sub == "u-1"
        auth_service.sync_user_from_idp.assert_not_awaited()

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_get_current_user_auth_error(self):
        from src.auth.dependencies import get_current_user
        from src.auth.providers import AuthenticationError
        from fastapi import HTTPException
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(side_effect=AuthenticationError("bad token", 401))
        app_state = {"auth_provider": auth_provider}
        request = self._make_request(
            headers={"Authorization": "Bearer bad"},
            app_state=app_state,
        )
        with pytest.raises(HTTPException) as exc_info:
            _run(get_current_user(request))
        assert exc_info.value.status_code == 401

    def test_get_optional_user_returns_none(self):
        from src.auth.dependencies import get_optional_user
        # When auth is enabled but no token, returns None
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            request = self._make_request(headers={})
            result = _run(get_optional_user(request))
            assert result is None

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_role_with_rbac(self):
        from src.auth.dependencies import require_role
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["viewer"])
        auth_provider = AsyncMock()
        auth_provider.verify_token = AsyncMock(return_value=user)
        rbac = MagicMock()
        rbac.get_highest_role.return_value = "admin"
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])
        app_state = {
            "auth_provider": auth_provider, "auth_service": auth_service,
            "rbac_engine": rbac,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_role("admin")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_role_fallback_idp_roles(self):
        from src.auth.dependencies import require_role
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["admin"])
        app_state = {"rbac_engine": None, "auth_service": None}
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_role("admin")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_role_denied(self):
        from src.auth.dependencies import require_role
        from src.auth.providers import AuthUser
        from fastapi import HTTPException
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["viewer"])
        app_state = {"rbac_engine": None, "auth_service": None}
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            _run(check_fn(request, user))
        assert exc_info.value.status_code == 403

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_role_no_state(self):
        from src.auth.dependencies import require_role
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["admin"])
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=None,
        )
        check_fn = require_role("admin")
        # No state = allow (graceful degradation)
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_role_user_role_in_required(self):
        from src.auth.dependencies import require_role
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["viewer"])
        rbac = MagicMock()
        rbac.get_highest_role.return_value = "viewer"
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[{"role": "viewer"}, {"role": "kb_manager"}])
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_role("kb_manager")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_permission_allowed(self):
        from src.auth.dependencies import require_permission
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=[])
        rbac = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        rbac.check_permission.return_value = decision
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_permission("glossary", "import")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_permission_denied(self):
        from src.auth.dependencies import require_permission
        from src.auth.providers import AuthUser
        from fastapi import HTTPException
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=[])
        rbac = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        decision.reason = "no perm"
        rbac.check_permission.return_value = decision
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[])
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            app_state=app_state,
        )
        check_fn = require_permission("glossary", "import")
        with pytest.raises(HTTPException) as exc_info:
            _run(check_fn(request, user))
        assert exc_info.value.status_code == 403

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_kb_access_admin_bypass(self):
        from src.auth.dependencies import require_kb_access
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=["admin"])
        rbac = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        rbac.check_permission.return_value = decision
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            path_params={"kb_id": "kb-1"},
            app_state=app_state,
        )
        check_fn = require_kb_access("contributor")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_kb_access_kb_permission(self):
        from src.auth.dependencies import require_kb_access
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=[])
        rbac = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        rbac.check_permission.return_value = decision
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[])
        auth_service.get_kb_permission = AsyncMock(return_value="contributor")
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac, "abac_engine": None,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            path_params={"kb_id": "kb-1"},
            app_state=app_state,
        )
        check_fn = require_kb_access("contributor")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_kb_access_denied(self):
        from src.auth.dependencies import require_kb_access
        from src.auth.providers import AuthUser
        from fastapi import HTTPException
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=[])
        rbac = MagicMock()
        decision = MagicMock()
        decision.allowed = False
        rbac.check_permission.return_value = decision
        auth_service = AsyncMock()
        auth_service.get_user_roles = AsyncMock(return_value=[])
        auth_service.get_kb_permission = AsyncMock(return_value=None)
        app_state = {
            "auth_provider": AsyncMock(), "auth_service": auth_service,
            "rbac_engine": rbac, "abac_engine": None,
        }
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            path_params={"kb_id": "kb-1"},
            app_state=app_state,
        )
        check_fn = require_kb_access("manager")
        with pytest.raises(HTTPException) as exc_info:
            _run(check_fn(request, user))
        assert exc_info.value.status_code == 403

    @patch("src.auth.dependencies.AUTH_ENABLED", True)
    def test_require_kb_access_no_kb_id(self):
        from src.auth.dependencies import require_kb_access
        from src.auth.providers import AuthUser
        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k", roles=[])
        app_state = {"auth_provider": AsyncMock(), "auth_service": None, "rbac_engine": None}
        request = self._make_request(
            headers={"Authorization": "Bearer tok"},
            path_params={},
            app_state=app_state,
        )
        check_fn = require_kb_access("reader")
        result = _run(check_fn(request, user))
        assert result.sub == "u-1"


# ---------------------------------------------------------------------------
# auth/service.py — facade delegate tests
# ---------------------------------------------------------------------------

class TestAuthServiceFacade:
    @patch("src.auth.service.create_async_engine")
    @patch("src.auth.service.async_sessionmaker")
    def test_delegate_methods(self, mock_session_maker, mock_engine):
        from src.auth.service import AuthService
        from src.auth.providers import AuthUser
        svc = AuthService("sqlite+aiosqlite:///test.db")

        # Test that facade methods delegate correctly
        svc._roles = MagicMock()
        svc._roles.get_user_roles = AsyncMock(return_value=[])
        svc._roles.assign_role = AsyncMock(return_value={"id": "1"})
        svc._roles.revoke_role = AsyncMock(return_value=True)
        svc._roles.get_kb_permission = AsyncMock(return_value="reader")
        svc._roles.set_kb_permission = AsyncMock(return_value={"ok": True})
        svc._roles.list_kb_permissions = AsyncMock(return_value=[])
        svc._roles.remove_kb_permission = AsyncMock(return_value=True)

        assert _run(svc.get_user_roles("u-1")) == []
        assert _run(svc.assign_role("u-1", "admin"))["id"] == "1"
        assert _run(svc.revoke_role("u-1", "admin")) is True
        assert _run(svc.get_kb_permission("u-1", "kb-1")) == "reader"
        assert _run(svc.set_kb_permission("u-1", "kb-1", "contributor"))["ok"] is True
        assert _run(svc.list_kb_permissions("kb-1")) == []
        assert _run(svc.remove_kb_permission("u-1", "kb-1")) is True

        svc._users = MagicMock()
        svc._users.sync_user_from_idp = AsyncMock(return_value={"id": "1"})
        svc._users.create_user = AsyncMock(return_value={"id": "2"})
        svc._users.update_user = AsyncMock(return_value={"id": "1"})
        svc._users.delete_user = AsyncMock(return_value=True)
        svc._users.get_user = AsyncMock(return_value={"id": "1"})
        svc._users.list_users = AsyncMock(return_value=[])

        user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k")
        _run(svc.sync_user_from_idp(user))
        _run(svc.create_user("t@t.com", "T"))
        _run(svc.update_user("u-1", display_name="New"))
        _run(svc.delete_user("u-1"))
        _run(svc.get_user("u-1"))
        _run(svc.list_users())

        svc._auth = MagicMock()
        svc._auth.authenticate = AsyncMock(return_value={"token": "abc"})
        svc._auth.create_user_with_password = AsyncMock(return_value={"id": "3"})
        svc._auth.change_password = AsyncMock(return_value=True)

        _run(svc.authenticate("t@t.com", "pass"))
        _run(svc.create_user_with_password("t@t.com", "pass", "T"))
        _run(svc.change_password("u-1", "old", "new"))

        svc._activity = MagicMock()
        svc._activity.log_activity = AsyncMock()
        svc._activity.get_user_activities = AsyncMock(return_value=[])
        svc._activity.get_activity_summary = AsyncMock(return_value={})

        _run(svc.log_activity("u-1", "search", "kb"))
        _run(svc.get_user_activities("u-1"))
        _run(svc.get_activity_summary("u-1"))

    @patch("src.auth.service.create_async_engine")
    @patch("src.auth.service.async_sessionmaker")
    def test_close(self, mock_session_maker, mock_engine):
        from src.auth.service import AuthService
        svc = AuthService("sqlite+aiosqlite:///test.db")
        svc._engine = AsyncMock()
        svc._engine.dispose = AsyncMock()
        _run(svc.close())
        svc._engine.dispose.assert_awaited_once()

    @patch("src.auth.service.create_async_engine")
    @patch("src.auth.service.async_sessionmaker")
    def test_session_helper(self, mock_session_maker, mock_engine):
        from src.auth.service import AuthService
        svc = AuthService("sqlite+aiosqlite:///test.db")
        svc._session_factory = MagicMock(return_value="session")
        assert svc._session() == "session"


# ---------------------------------------------------------------------------
# auth/role_service.py
# ---------------------------------------------------------------------------

class TestRoleService:
    def _make_svc(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        maker = MagicMock(return_value=session)
        from src.auth.role_service import RoleService
        return RoleService(maker), session

    def test_get_user_roles_not_found(self):
        svc, session = self._make_svc()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        result = _run(svc.get_user_roles("u-1"))
        assert result == []

    def test_get_user_roles_found(self):
        svc, session = self._make_svc()
        # First execute: find user
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        # Second execute: get roles
        role = MagicMock()
        role.name = "admin"
        role.display_name = "Admin"
        ur = MagicMock()
        ur.scope_type = "global"
        ur.scope_id = None
        ur.expires_at = None
        r2 = MagicMock()
        r2.all.return_value = [(ur, role)]
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(svc.get_user_roles("u-1"))
        assert len(result) == 1
        assert result[0]["role"] == "admin"

    def test_assign_role_user_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = None
        session.execute = AsyncMock(return_value=r1)
        with pytest.raises(ValueError, match="User not found"):
            _run(svc.assign_role("u-1", "admin"))

    def test_assign_role_role_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(side_effect=[r1, r2])
        with pytest.raises(ValueError, match="Role not found"):
            _run(svc.assign_role("u-1", "nonexistent"))

    def test_revoke_role_user_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = None
        session.execute = AsyncMock(return_value=r1)
        assert _run(svc.revoke_role("u-1", "admin")) is False

    def test_revoke_role_role_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.first.return_value = None
        session.execute = AsyncMock(side_effect=[r1, r2])
        assert _run(svc.revoke_role("u-1", "admin")) is False

    def test_revoke_role_success(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.first.return_value = ("role-id",)
        r3 = MagicMock()
        r3.rowcount = 1
        session.execute = AsyncMock(side_effect=[r1, r2, r3])
        assert _run(svc.revoke_role("u-1", "admin", scope_type="global", scope_id="s1")) is True

    def test_get_kb_permission_user_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = None
        session.execute = AsyncMock(return_value=r1)
        assert _run(svc.get_kb_permission("u-1", "kb-1")) is None

    def test_get_kb_permission_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.scalar_one_or_none.return_value = "contributor"
        session.execute = AsyncMock(side_effect=[r1, r2])
        assert _run(svc.get_kb_permission("u-1", "kb-1")) == "contributor"

    def test_set_kb_permission_user_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = None
        session.execute = AsyncMock(return_value=r1)
        with pytest.raises(ValueError, match="User not found"):
            _run(svc.set_kb_permission("u-1", "kb-1", "reader"))

    def test_set_kb_permission_new(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(svc.set_kb_permission("u-1", "kb-1", "reader"))
        assert result["permission_level"] == "reader"
        session.add.assert_called_once()

    def test_set_kb_permission_update(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        existing = MagicMock()
        existing.permission_level = "reader"
        r2 = MagicMock()
        r2.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(side_effect=[r1, r2])
        result = _run(svc.set_kb_permission("u-1", "kb-1", "contributor"))
        assert existing.permission_level == "contributor"

    def test_list_kb_permissions(self):
        svc, session = self._make_svc()
        perm = MagicMock()
        perm.permission_level = "reader"
        perm.granted_by = "admin"
        user = MagicMock()
        user.id = "u-1"
        user.email = "t@t.com"
        user.display_name = "Test"
        r = MagicMock()
        r.all.return_value = [(perm, user)]
        session.execute = AsyncMock(return_value=r)
        result = _run(svc.list_kb_permissions("kb-1"))
        assert len(result) == 1

    def test_remove_kb_permission_user_not_found(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = None
        session.execute = AsyncMock(return_value=r1)
        assert _run(svc.remove_kb_permission("u-1", "kb-1")) is False

    def test_remove_kb_permission_success(self):
        svc, session = self._make_svc()
        r1 = MagicMock()
        r1.first.return_value = ("internal-id",)
        r2 = MagicMock()
        r2.rowcount = 1
        session.execute = AsyncMock(side_effect=[r1, r2])
        assert _run(svc.remove_kb_permission("u-1", "kb-1")) is True


# ---------------------------------------------------------------------------
# cache/l2_semantic_cache.py
# ---------------------------------------------------------------------------

class TestL2SemanticCache:
    def test_cosine_similarity(self):
        from src.stores.redis.l2_semantic_cache import _cosine_similarity
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0  # diff length
        assert _cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0  # zero norm

    def test_set_with_embedding_provider(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        from src.stores.redis.cache_types import CacheEntry
        redis_mock = AsyncMock()
        provider = AsyncMock()
        provider.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._embedding_provider = provider
        cache._ttl_seconds = 3600
        cache._prefix = "test"
        entry = CacheEntry(key="k1", query="hello", response={"answer": "world"})
        _run(cache.set(entry))
        redis_mock.setex.assert_awaited_once()

    def test_set_without_provider(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        from src.stores.redis.cache_types import CacheEntry
        redis_mock = AsyncMock()
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._embedding_provider = None
        cache._ttl_seconds = 3600
        cache._prefix = "test"
        entry = CacheEntry(key="k1", query="hello", response={"answer": "world"}, embedding=[0.1])
        _run(cache.set(entry))
        redis_mock.setex.assert_awaited_once()

    def test_set_embed_failure(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        from src.stores.redis.cache_types import CacheEntry
        redis_mock = AsyncMock()
        provider = AsyncMock()
        provider.embed = AsyncMock(side_effect=Exception("embed fail"))
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._embedding_provider = provider
        cache._ttl_seconds = 3600
        cache._prefix = "test"
        entry = CacheEntry(key="k1", query="hello", response={"answer": "world"})
        _run(cache.set(entry))  # should not raise
        redis_mock.setex.assert_awaited_once()

    def test_set_redis_error(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        from src.stores.redis.cache_types import CacheEntry
        redis_mock = AsyncMock()
        redis_mock.setex = AsyncMock(side_effect=Exception("redis fail"))
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._embedding_provider = None
        cache._ttl_seconds = 3600
        cache._prefix = "test"
        entry = CacheEntry(key="k1", query="hello", response="resp", embedding=[0.1])
        _run(cache.set(entry))  # should not raise

    def test_delete_success(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        redis_mock.delete = AsyncMock(return_value=1)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        assert _run(cache.delete("k1")) is True

    def test_delete_not_found(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        redis_mock.delete = AsyncMock(return_value=0)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        assert _run(cache.delete("k1")) is False

    def test_delete_error(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        redis_mock.delete = AsyncMock(side_effect=Exception("fail"))
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        assert _run(cache.delete("k1")) is False

    def test_semantic_search_with_matches(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({
            "query": "hello",
            "response": {"answer": "world"},
            "embedding": [1.0, 0.0, 0.0],
            "domain": "general",
            "metadata": {},
        })
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.8))
        assert result is not None
        assert result.similarity == pytest.approx(1.0)

    def test_semantic_search_no_match(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({
            "query": "hello",
            "response": {"answer": "world"},
            "embedding": [0.0, 1.0, 0.0],
            "domain": "general",
            "metadata": {},
        })
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.99))
        assert result is None

    def test_semantic_search_kb_isolation(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({
            "query": "hello",
            "response": {"answer": "world"},
            "embedding": [1.0, 0.0, 0.0],
            "domain": "general",
            "metadata": {"kb_ids": ["kb-2"]},
        })
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.8, kb_ids=["kb-1"]))
        assert result is None  # different KB

    def test_semantic_search_version_check(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({
            "query": "hello",
            "response": {"answer": "world", "_cache_version": "v1"},
            "embedding": [1.0, 0.0, 0.0],
            "domain": "general",
            "metadata": {},
        })
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.8, cache_version="v2"))
        assert result is None  # version mismatch

    def test_semantic_search_no_embedding_in_stored(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({
            "query": "hello",
            "response": "world",
            "domain": "general",
            "metadata": {},
        })
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.8))
        assert result is None

    def test_semantic_search_error(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        redis_mock.scan = AsyncMock(side_effect=Exception("redis error"))
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        cache._max_entries = 5000
        result = _run(cache._semantic_search([1.0, 0.0, 0.0], 0.8))
        assert result is None

    def test_invalidate_by_metadata_value(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({"metadata": {"kb_id": "kb-1"}})
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        redis_mock.delete = AsyncMock(return_value=1)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        count = _run(cache.invalidate_by_metadata_value("kb_id", "kb-1"))
        assert count == 1

    def test_invalidate_by_metadata_list_value(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        stored = json.dumps({"metadata": {"kb_ids": ["kb-1", "kb-2"]}})
        redis_mock.scan = AsyncMock(return_value=(0, ["test:k1"]))
        redis_mock.get = AsyncMock(return_value=stored)
        redis_mock.delete = AsyncMock(return_value=1)
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        count = _run(cache.invalidate_by_metadata_value("kb_ids", "kb-1"))
        assert count == 1

    def test_invalidate_by_metadata_error(self):
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        redis_mock = AsyncMock()
        redis_mock.scan = AsyncMock(side_effect=Exception("fail"))
        cache = L2SemanticCache.__new__(L2SemanticCache)
        cache._redis = redis_mock
        cache._prefix = "test"
        count = _run(cache.invalidate_by_metadata_value("kb_id", "kb-1"))
        assert count == 0


# ---------------------------------------------------------------------------
# api/routes/search_groups.py
# ---------------------------------------------------------------------------

class TestSearchGroupRoutes:
    """Test search_groups route functions directly via mocked _get_state."""

    def _mock_state(self, repo):
        state = MagicMock()
        state.get.return_value = repo
        return state

    def test_list_groups_no_repo(self):
        with patch("src.api.app._state", {"search_group_repo": None}):
            from src.api.routes.search_groups import list_groups
            result = _run(list_groups())
            assert result == {"groups": []}

    def test_list_groups_with_repo(self):
        repo = AsyncMock()
        repo.list_all = AsyncMock(return_value=[{"id": "sg-1"}])
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import list_groups
            result = _run(list_groups())
            assert len(result["groups"]) == 1

    def test_create_group_no_repo(self):
        from fastapi import HTTPException
        with patch("src.api.app._state", {"search_group_repo": None}):
            from src.api.routes.search_groups import create_group, CreateGroupRequest
            req = CreateGroupRequest(name="test", kb_ids=["kb-1"])
            with pytest.raises(HTTPException) as exc_info:
                _run(create_group(req))
            assert exc_info.value.status_code == 503

    def test_create_group_success(self):
        repo = AsyncMock()
        repo.create = AsyncMock(return_value={"id": "sg-1", "name": "test"})
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import create_group, CreateGroupRequest
            req = CreateGroupRequest(name="test", kb_ids=["kb-1"], description="d", is_default=True)
            result = _run(create_group(req))
            assert result["name"] == "test"

    def test_get_group_not_found(self):
        from fastapi import HTTPException
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import get_group
            with pytest.raises(HTTPException) as exc_info:
                _run(get_group("nonexistent"))
            assert exc_info.value.status_code == 404

    def test_get_group_success(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value={"id": "sg-1"})
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import get_group
            result = _run(get_group("sg-1"))
            assert result["id"] == "sg-1"

    def test_update_group_not_found(self):
        from fastapi import HTTPException
        repo = AsyncMock()
        repo.update = AsyncMock(return_value=None)
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import update_group, UpdateGroupRequest
            req = UpdateGroupRequest(name="new")
            with pytest.raises(HTTPException) as exc_info:
                _run(update_group("sg-1", req))
            assert exc_info.value.status_code == 404

    def test_update_group_success(self):
        repo = AsyncMock()
        repo.update = AsyncMock(return_value={"id": "sg-1", "name": "new"})
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import update_group, UpdateGroupRequest
            req = UpdateGroupRequest(name="new")
            result = _run(update_group("sg-1", req))
            assert result["name"] == "new"

    def test_delete_group_not_found(self):
        from fastapi import HTTPException
        repo = AsyncMock()
        repo.delete = AsyncMock(return_value=False)
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import delete_group
            with pytest.raises(HTTPException) as exc_info:
                _run(delete_group("nonexistent"))
            assert exc_info.value.status_code == 404

    def test_delete_group_success(self):
        repo = AsyncMock()
        repo.delete = AsyncMock(return_value=True)
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import delete_group
            result = _run(delete_group("sg-1"))
            assert result["success"] is True

    def test_get_group_kbs(self):
        repo = AsyncMock()
        repo.resolve_kb_ids = AsyncMock(return_value=["kb-1", "kb-2"])
        with patch("src.api.app._state", {"search_group_repo": repo}):
            from src.api.routes.search_groups import get_group_kbs
            result = _run(get_group_kbs("sg-1"))
            assert result["kb_ids"] == ["kb-1", "kb-2"]

    def test_get_group_no_repo(self):
        from fastapi import HTTPException
        with patch("src.api.app._state", {"search_group_repo": None}):
            from src.api.routes.search_groups import get_group
            with pytest.raises(HTTPException) as exc_info:
                _run(get_group("sg-1"))
            assert exc_info.value.status_code == 503

    def test_delete_group_no_repo(self):
        from fastapi import HTTPException
        with patch("src.api.app._state", {"search_group_repo": None}):
            from src.api.routes.search_groups import delete_group
            with pytest.raises(HTTPException) as exc_info:
                _run(delete_group("sg-1"))
            assert exc_info.value.status_code == 503

    def test_get_group_kbs_no_repo(self):
        from fastapi import HTTPException
        with patch("src.api.app._state", {"search_group_repo": None}):
            from src.api.routes.search_groups import get_group_kbs
            with pytest.raises(HTTPException) as exc_info:
                _run(get_group_kbs("sg-1"))
            assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# cv_pipeline/arrow_detector.py
# ---------------------------------------------------------------------------

class TestArrowDetector:
    def test_point_distance(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        d = ArrowDetector._point_distance((0.0, 0.0), (3.0, 4.0))
        assert d == pytest.approx(5.0)

    def test_merge_segments_empty(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        assert det._merge_segments([]) == []

    def test_merge_segments_single(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        result = det._merge_segments([((0.0, 0.0), (100.0, 0.0))])
        assert len(result) == 1

    def test_merge_segments_nearby(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        segs = [
            ((0.0, 0.0), (50.0, 0.0)),
            ((55.0, 0.0), (100.0, 0.0)),  # close to end of first
        ]
        result = det._merge_segments(segs)
        assert len(result) == 1  # merged

    def test_merge_segments_far_apart(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        segs = [
            ((0.0, 0.0), (50.0, 0.0)),
            ((200.0, 200.0), (300.0, 200.0)),  # far away
        ]
        result = det._merge_segments(segs)
        assert len(result) == 2

    def test_detect_no_lines(self):
        import numpy as np
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        # White image, no edges
        img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        result = det.detect(img, [])
        assert result == []

    def test_detect_arrowhead_small_roi(self):
        import numpy as np
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        img = np.ones((10, 10, 3), dtype=np.uint8) * 255
        # Endpoint at edge, ROI too small
        result = det._detect_arrowhead(img, (0.0, 0.0), (5.0, 5.0))
        # Should not crash
        assert isinstance(result, bool)

    def test_find_nearest_shape_none(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        det = ArrowDetector()
        result = det._find_nearest_shape((50.0, 50.0), [])
        assert result is None


# ---------------------------------------------------------------------------
# cv_pipeline/ocr_with_coords.py
# ---------------------------------------------------------------------------

class TestOCRWithCoords:
    def test_extract_no_paddleocr(self):
        from src.cv_pipeline.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        with patch.dict("sys.modules", {"paddleocr": None}):
            with patch("builtins.__import__", side_effect=ImportError("no paddleocr")):
                result = ocr.extract(b"fake_image_bytes")
                assert result == []

    def test_extract_legacy_format(self):
        from src.cv_pipeline.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        # Test the _extract_legacy method directly
        legacy_result = [[
            [
                [[10, 10], [100, 10], [100, 30], [10, 30]],
                ("hello", 0.95),
            ],
            [
                [[10, 40], [100, 40], [100, 60], [10, 60]],
                ("world", 0.9),
            ],
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert len(boxes) == 2
        assert boxes[0].text == "hello"
        assert boxes[0].confidence == pytest.approx(0.95)

    def test_extract_legacy_empty(self):
        from src.cv_pipeline.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        assert ocr._extract_legacy([]) == []
        assert ocr._extract_legacy([None]) == []

    def test_extract_legacy_bad_format(self):
        from src.cv_pipeline.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        # Bad format lines
        legacy_result = [[
            "not a tuple",
            [[], ("text", 0.5)],  # empty polygon
            [[[10, 10], [20, 20], [30, 30], [40, 40]], None],  # bad text_info
            [[[10, 10], [20, 20], [30, 30], [40, 40]], ("", 0.5)],  # empty text
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert boxes == []

    def test_extract_legacy_exception(self):
        from src.cv_pipeline.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        # Cause an exception in parsing
        boxes = ocr._extract_legacy([Exception("bad")])
        assert boxes == []


# ---------------------------------------------------------------------------
# auth/activity_logger.py
# ---------------------------------------------------------------------------

class TestActivityLogger:
    def _make_logger(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = MagicMock()
        maker = MagicMock(return_value=session)
        from src.auth.activity_logger import ActivityLogger
        return ActivityLogger(maker), session

    def test_log_activity(self):
        al, session = self._make_logger()
        _run(al.log_activity("u-1", "search", "kb", resource_id="r1", kb_id="kb-1",
                             details={"q": "test"}, ip_address="127.0.0.1", user_agent="test"))
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_log_activity_error(self):
        al, session = self._make_logger()
        session.commit = AsyncMock(side_effect=Exception("fail"))
        _run(al.log_activity("u-1", "search", "kb"))  # should not raise

    def test_get_user_activities(self):
        al, session = self._make_logger()
        activity = MagicMock()
        activity.id = "a-1"
        activity.activity_type = "search"
        activity.resource_type = "kb"
        activity.resource_id = None
        activity.kb_id = "kb-1"
        activity.details = {}
        activity.created_at = datetime.now(timezone.utc)
        _mock_scalars_all(session, [activity])
        result = _run(al.get_user_activities("u-1", activity_type="search"))
        assert len(result) == 1

    def test_get_user_activities_no_filter(self):
        al, session = self._make_logger()
        _mock_scalars_all(session, [])
        result = _run(al.get_user_activities("u-1"))
        assert result == []

    def test_get_activity_summary(self):
        al, session = self._make_logger()
        r = MagicMock()
        r.all.return_value = [("search", 10), ("upload", 5)]
        session.execute = AsyncMock(return_value=r)
        result = _run(al.get_activity_summary("u-1", days=7))
        assert result["total"] == 15
        assert result["by_type"]["search"] == 10


# ---------------------------------------------------------------------------
# auth/authenticator.py
# ---------------------------------------------------------------------------

class TestAuthenticator:
    def _make_auth(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = MagicMock()
        maker = MagicMock(return_value=session)
        user_crud = MagicMock()
        user_crud._assign_default_role = AsyncMock()
        from src.auth.authenticator import Authenticator
        return Authenticator(maker, user_crud), session

    def test_authenticate_user_not_found(self):
        auth, session = self._make_auth()
        _mock_scalar_one_or_none(session, None)
        result = _run(auth.authenticate("test@test.com", "password"))
        assert result is None

    def test_authenticate_success(self):
        from src.auth.password import hash_password
        auth, session = self._make_auth()
        user = MagicMock()
        user.id = "u-1"
        user.email = "test@test.com"
        user.display_name = "Test"
        user.department = "IT"
        user.organization_id = "org-1"
        user.password_hash = hash_password("password")
        user.last_login_at = None
        _mock_scalar_one_or_none(session, user)
        result = _run(auth.authenticate("test@test.com", "password"))
        assert result is not None
        assert result["id"] == "u-1"

    def test_authenticate_wrong_password(self):
        from src.auth.password import hash_password
        auth, session = self._make_auth()
        user = MagicMock()
        user.password_hash = hash_password("correct")
        _mock_scalar_one_or_none(session, user)
        result = _run(auth.authenticate("test@test.com", "wrong"))
        assert result is None

    def test_create_user_with_password_exists(self):
        auth, session = self._make_auth()
        _mock_scalar_one_or_none(session, MagicMock())  # user exists
        with pytest.raises(ValueError, match="already exists"):
            _run(auth.create_user_with_password("test@test.com", "pass", "Test"))

    def test_create_user_with_password_success(self):
        auth, session = self._make_auth()
        _mock_scalar_one_or_none(session, None)  # no existing user
        result = _run(auth.create_user_with_password("test@test.com", "pass", "Test"))
        assert result["email"] == "test@test.com"
        session.add.assert_called_once()

    def test_change_password_user_not_found(self):
        auth, session = self._make_auth()
        _mock_scalar_one_or_none(session, None)
        assert _run(auth.change_password("u-1", "old", "new")) is False

    def test_change_password_wrong_old(self):
        from src.auth.password import hash_password
        auth, session = self._make_auth()
        user = MagicMock()
        user.password_hash = hash_password("correct")
        _mock_scalar_one_or_none(session, user)
        assert _run(auth.change_password("u-1", "wrong", "new")) is False

    def test_change_password_success(self):
        from src.auth.password import hash_password
        auth, session = self._make_auth()
        user = MagicMock()
        user.password_hash = hash_password("old")
        _mock_scalar_one_or_none(session, user)
        assert _run(auth.change_password("u-1", "old", "new")) is True


# ---------------------------------------------------------------------------
# auth/user_crud.py
# ---------------------------------------------------------------------------

class TestUserCRUD:
    def _make_crud(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = MagicMock()
        maker = MagicMock(return_value=session)
        from src.auth.user_crud import UserCRUD
        return UserCRUD(maker), session

    def test_update_user_not_found(self):
        crud, session = self._make_crud()
        _mock_scalar_one_or_none(session, None)
        result = _run(crud.update_user("u-1", display_name="New"))
        assert result is None

    def test_update_user_success(self):
        crud, session = self._make_crud()
        user = MagicMock()
        user.id = "u-1"
        user.email = "t@t.com"
        user.display_name = "Old"
        _mock_scalar_one_or_none(session, user)
        result = _run(crud.update_user("u-1", display_name="New", department="IT",
                                        organization_id="org", is_active=False))
        assert result["updated"] is True
        assert user.display_name == "New"
        assert user.is_active is False
        assert user.status == "inactive"

    def test_delete_user_not_found(self):
        crud, session = self._make_crud()
        _mock_scalar_one_or_none(session, None)
        assert _run(crud.delete_user("u-1")) is False

    def test_delete_user_success(self):
        crud, session = self._make_crud()
        user = MagicMock()
        _mock_scalar_one_or_none(session, user)
        assert _run(crud.delete_user("u-1")) is True
        session.delete.assert_awaited_once()

    def test_get_user_not_found(self):
        crud, session = self._make_crud()
        _mock_scalar_one_or_none(session, None)
        assert _run(crud.get_user("u-1")) is None

    def test_get_user_found(self):
        crud, session = self._make_crud()
        user = MagicMock()
        user.id = "u-1"
        user.external_id = "ext-1"
        user.email = "t@t.com"
        user.display_name = "Test"
        user.provider = "local"
        user.department = "IT"
        user.organization_id = "org"
        user.is_active = True
        user.last_login_at = datetime.now(timezone.utc)
        _mock_scalar_one_or_none(session, user)
        result = _run(crud.get_user("u-1"))
        assert result["email"] == "t@t.com"

    def test_list_users(self):
        crud, session = self._make_crud()
        user = MagicMock()
        user.id = "u-1"
        user.email = "t@t.com"
        user.display_name = "T"
        user.provider = "local"
        user.department = "IT"
        user.is_active = True
        _mock_scalars_all(session, [user])
        result = _run(crud.list_users())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# auth/middleware.py
# ---------------------------------------------------------------------------

class TestAuthMiddleware:
    def test_classify_activity_search(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/search")["type"] == "search"

    def test_classify_activity_upload(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/knowledge/file-upload-ingest")["type"] == "upload"

    def test_classify_activity_ingest(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/knowledge/ingest")["type"] == "ingest"

    def test_classify_activity_ask(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/knowledge/ask")["type"] == "query"

    def test_classify_activity_glossary_create(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/glossary")["type"] == "create"

    def test_classify_activity_glossary_edit(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("PUT", "/api/v1/glossary/123")["type"] == "edit"

    def test_classify_activity_feedback(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/feedback")["type"] == "feedback"

    def test_classify_activity_kb_create(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("POST", "/api/v1/kb")["type"] == "create"

    def test_classify_activity_kb_edit(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("PUT", "/api/v1/kb/123")["type"] == "edit"

    def test_classify_activity_none(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        assert mw._classify_activity("GET", "/api/v1/health") is None

    def test_maybe_log_activity_no_user(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        request = MagicMock()
        request.state.auth_user = None
        _run(mw._maybe_log_activity(request, "/search", 10.0))

    def test_maybe_log_activity_anonymous(self):
        from src.auth.middleware import AuthMiddleware
        from src.auth.dependencies import _ANONYMOUS_USER
        mw = AuthMiddleware.__new__(AuthMiddleware)
        request = MagicMock()
        request.state.auth_user = _ANONYMOUS_USER
        _run(mw._maybe_log_activity(request, "/search", 10.0))

    def test_maybe_log_activity_no_match(self):
        from src.auth.middleware import AuthMiddleware
        from src.auth.providers import AuthUser
        mw = AuthMiddleware.__new__(AuthMiddleware)
        request = MagicMock()
        request.state.auth_user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k")
        _run(mw._maybe_log_activity(request, "/health", 10.0))

    def test_maybe_log_activity_with_service(self):
        from src.auth.middleware import AuthMiddleware
        from src.auth.providers import AuthUser
        mw = AuthMiddleware.__new__(AuthMiddleware)
        request = MagicMock()
        request.state.auth_user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k")
        request.method = "POST"
        request.client.host = "127.0.0.1"
        request.headers.get.return_value = "test-agent"
        auth_service = AsyncMock()
        auth_service.log_activity = AsyncMock()
        request.app.state._app_state = {"auth_service": auth_service}
        _run(mw._maybe_log_activity(request, "/api/v1/search", 10.0))
        auth_service.log_activity.assert_awaited_once()

    def test_maybe_log_activity_error(self):
        from src.auth.middleware import AuthMiddleware
        from src.auth.providers import AuthUser
        mw = AuthMiddleware.__new__(AuthMiddleware)
        request = MagicMock()
        request.state.auth_user = AuthUser(sub="u-1", email="t@t.com", display_name="T", provider="k")
        request.method = "POST"
        request.app.state._app_state = None
        # Should not raise
        _run(mw._maybe_log_activity(request, "/api/v1/search", 10.0))
