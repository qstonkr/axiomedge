"""Backfill tests for ownership repositories — covers branches missed by
test_db_repositories_full.

Targets: DocumentOwnerRepository (save update, get_by_owner, get_by_kb,
delete not_found, delete error), TopicOwnerRepository (save update,
get_by_kb, delete found, _update_existing, _build_new_model),
ErrorReportRepository (save update, get_by_document, get_open_reports,
delete found/not_found/error).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_maker():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock()
    maker.return_value = session
    maker.kw = {}
    return maker, session


def _make_scalars_result(models):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = models
    scalars.first.return_value = models[0] if models else None
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = (
        models[0] if models else None
    )
    result.scalar.return_value = len(models)
    return result


def _doc_owner_model(**overrides):
    defaults = {
        "id": "o1",
        "document_id": "d1",
        "kb_id": "kb1",
        "owner_user_id": "u1",
        "backup_owner_user_id": "u2",
        "ownership_type": "primary",
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _topic_owner_model(**overrides):
    defaults = {
        "id": "to1",
        "kb_id": "kb1",
        "topic_name": "K8s",
        "topic_keywords": '["kubernetes","k8s"]',
        "sme_user_id": "u1",
        "escalation_chain": '["u2","u3"]',
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _error_report_model(**overrides):
    defaults = {
        "id": "r1",
        "document_id": "d1",
        "kb_id": "kb1",
        "error_type": "typo",
        "description": "Wrong spelling",
        "reporter_user_id": "u1",
        "assigned_to": None,
        "status": "pending",
        "priority": "medium",
        "resolution_note": None,
        "resolved_at": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


# ===========================================================================
# DocumentOwnerRepository
# ===========================================================================

class TestDocumentOwnerSaveUpdate:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_update_existing(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        existing = _doc_owner_model()
        self.session.execute.return_value = _make_scalars_result([existing])
        repo = DocumentOwnerRepository(self.maker)

        await repo.save({
            "document_id": "d1",
            "kb_id": "kb1",
            "owner_user_id": "u-new",
        })
        assert existing.owner_user_id == "u-new"
        self.session.commit.assert_awaited_once()
        # No add call — update only
        self.session.add.assert_not_called()

    async def test_save_new_auto_id(self):
        """New record without id gets a generated uuid."""
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        await repo.save({
            "document_id": "d2",
            "kb_id": "kb1",
            "owner_user_id": "u1",
        })
        self.session.add.assert_called_once()

    async def test_save_new_with_empty_id(self):
        """Empty string id also triggers auto-generation."""
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        await repo.save({
            "id": "",
            "document_id": "d3",
            "kb_id": "kb1",
            "owner_user_id": "u1",
        })
        self.session.add.assert_called_once()

    async def test_save_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("fail")
        repo = DocumentOwnerRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.save({
                "document_id": "d1",
                "kb_id": "kb1",
                "owner_user_id": "u1",
            })
        self.session.rollback.assert_awaited_once()


class TestDocumentOwnerGetByOwner:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_by_owner_returns_list(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        m1 = _doc_owner_model(id="o1", document_id="d1")
        m2 = _doc_owner_model(id="o2", document_id="d2")
        self.session.execute.return_value = _make_scalars_result([m1, m2])
        repo = DocumentOwnerRepository(self.maker)

        results = await repo.get_by_owner("u1")
        assert len(results) == 2
        assert results[0]["document_id"] == "d1"
        assert results[1]["document_id"] == "d2"

    async def test_get_by_owner_empty(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        results = await repo.get_by_owner("nobody")
        assert results == []


class TestDocumentOwnerGetByKb:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_by_kb_returns_list(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        m1 = _doc_owner_model()
        self.session.execute.return_value = _make_scalars_result([m1])
        repo = DocumentOwnerRepository(self.maker)

        results = await repo.get_by_kb("kb1")
        assert len(results) == 1
        assert results[0]["kb_id"] == "kb1"

    async def test_get_by_kb_empty(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        results = await repo.get_by_kb("empty-kb")
        assert results == []


class TestDocumentOwnerDeleteBranches:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_delete_not_found(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        result = await repo.delete("nonexistent", "kb1")
        assert result is False

    async def test_delete_found(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        model = _doc_owner_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = DocumentOwnerRepository(self.maker)

        result = await repo.delete("d1", "kb1")
        assert result is True
        self.session.delete.assert_awaited_once_with(model)
        self.session.commit.assert_awaited_once()

    async def test_delete_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        model = _doc_owner_model()
        self.session.execute.return_value = _make_scalars_result([model])
        self.session.delete.side_effect = SQLAlchemyError("fail")
        repo = DocumentOwnerRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.delete("d1", "kb1")
        self.session.rollback.assert_awaited_once()


class TestDocumentOwnerToDict:
    def test_to_dict(self):
        from src.stores.postgres.repositories.ownership import (
            DocumentOwnerRepository,
        )
        model = _doc_owner_model()
        d = DocumentOwnerRepository._to_dict(model)
        assert d["id"] == "o1"
        assert d["owner_user_id"] == "u1"
        assert d["backup_owner_user_id"] == "u2"


# ===========================================================================
# TopicOwnerRepository
# ===========================================================================

class TestTopicOwnerSaveUpdate:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_update_existing(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        existing = _topic_owner_model()
        self.session.execute.return_value = _make_scalars_result([existing])
        repo = TopicOwnerRepository(self.maker)

        await repo.save({
            "kb_id": "kb1",
            "topic_name": "K8s",
            "sme_user_id": "u-new",
            "topic_keywords": ["kubernetes", "container"],
            "escalation_chain": ["u4"],
        })
        assert existing.sme_user_id == "u-new"
        assert existing.topic_keywords == json.dumps(
            ["kubernetes", "container"],
        )
        assert existing.escalation_chain == json.dumps(["u4"])
        self.session.commit.assert_awaited_once()

    async def test_save_update_partial_fields(self):
        """Only update fields present in data dict."""
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        existing = _topic_owner_model()
        orig_keywords = existing.topic_keywords
        self.session.execute.return_value = _make_scalars_result([existing])
        repo = TopicOwnerRepository(self.maker)

        await repo.save({
            "kb_id": "kb1",
            "topic_name": "K8s",
            "sme_user_id": "u-updated",
        })
        assert existing.sme_user_id == "u-updated"
        # topic_keywords not in data => unchanged
        assert existing.topic_keywords == orig_keywords

    async def test_save_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("fail")
        repo = TopicOwnerRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.save({
                "kb_id": "kb1",
                "topic_name": "T",
                "sme_user_id": "u1",
            })
        self.session.rollback.assert_awaited_once()


class TestTopicOwnerGetByKb:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_by_kb_returns_list(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        m1 = _topic_owner_model()
        self.session.execute.return_value = _make_scalars_result([m1])
        repo = TopicOwnerRepository(self.maker)

        results = await repo.get_by_kb("kb1")
        assert len(results) == 1
        assert results[0]["topic_name"] == "K8s"
        assert results[0]["topic_keywords"] == ["kubernetes", "k8s"]

    async def test_get_by_kb_empty(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = TopicOwnerRepository(self.maker)

        results = await repo.get_by_kb("empty-kb")
        assert results == []


class TestTopicOwnerDelete:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_delete_found(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        model = _topic_owner_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = TopicOwnerRepository(self.maker)

        result = await repo.delete("kb1", "K8s")
        assert result is True
        self.session.delete.assert_awaited_once_with(model)

    async def test_delete_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        model = _topic_owner_model()
        self.session.execute.return_value = _make_scalars_result([model])
        self.session.delete.side_effect = SQLAlchemyError("fail")
        repo = TopicOwnerRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.delete("kb1", "K8s")
        self.session.rollback.assert_awaited_once()


class TestTopicOwnerToDict:
    def test_to_dict_with_json_strings(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        model = _topic_owner_model()
        d = TopicOwnerRepository._to_dict(model)
        assert d["topic_keywords"] == ["kubernetes", "k8s"]
        assert d["escalation_chain"] == ["u2", "u3"]

    def test_to_dict_with_none_json_fields(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        model = _topic_owner_model(
            topic_keywords=None,
            escalation_chain=None,
        )
        d = TopicOwnerRepository._to_dict(model)
        assert d["topic_keywords"] == []
        assert d["escalation_chain"] == []


class TestTopicOwnerBuildNewModel:
    def test_build_new_model_with_all_fields(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        from unittest.mock import patch

        with patch(
            "src.stores.postgres.repositories.ownership.TopicOwnerModel",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            TopicOwnerRepository._build_new_model({
                "kb_id": "kb1",
                "topic_name": "Docker",
                "topic_keywords": ["docker", "container"],
                "escalation_chain": ["u1"],
                "sme_user_id": "u1",
            })
            call_kw = MockModel.call_args[1]
            assert call_kw["topic_keywords"] == json.dumps(
                ["docker", "container"],
            )
            assert call_kw["escalation_chain"] == json.dumps(["u1"])

    def test_build_new_model_auto_id(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        from unittest.mock import patch

        with patch(
            "src.stores.postgres.repositories.ownership.TopicOwnerModel",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            TopicOwnerRepository._build_new_model({
                "kb_id": "kb1",
                "topic_name": "Docker",
                "sme_user_id": "u1",
            })
            call_kw = MockModel.call_args[1]
            assert "id" in call_kw
            assert call_kw["id"]  # non-empty

    def test_build_new_model_empty_id(self):
        from src.stores.postgres.repositories.ownership import (
            TopicOwnerRepository,
        )
        from unittest.mock import patch

        with patch(
            "src.stores.postgres.repositories.ownership.TopicOwnerModel",
        ) as MockModel:
            MockModel.return_value = MagicMock()
            TopicOwnerRepository._build_new_model({
                "id": "",
                "kb_id": "kb1",
                "topic_name": "T",
                "sme_user_id": "u1",
            })
            call_kw = MockModel.call_args[1]
            assert call_kw["id"]  # replaced with uuid


# ===========================================================================
# ErrorReportRepository
# ===========================================================================

class TestErrorReportSaveUpdate:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_update_existing(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        existing = _error_report_model()
        self.session.execute.return_value = _make_scalars_result([existing])
        repo = ErrorReportRepository(self.maker)

        await repo.save({
            "id": "r1",
            "status": "resolved",
            "resolution_note": "Fixed",
        })
        assert existing.status == "resolved"
        assert existing.resolution_note == "Fixed"
        self.session.commit.assert_awaited_once()

    async def test_save_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        self.session.execute.side_effect = SQLAlchemyError("fail")
        repo = ErrorReportRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.save({"id": "r1", "error_type": "typo"})
        self.session.rollback.assert_awaited_once()


class TestErrorReportGetByDocument:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_by_document_returns_list(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        m1 = _error_report_model(id="r1")
        m2 = _error_report_model(id="r2", error_type="content")
        self.session.execute.return_value = _make_scalars_result([m1, m2])
        repo = ErrorReportRepository(self.maker)

        results = await repo.get_by_document("d1", "kb1")
        assert len(results) == 2
        assert results[0]["id"] == "r1"
        assert results[1]["error_type"] == "content"

    async def test_get_by_document_empty(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = ErrorReportRepository(self.maker)

        results = await repo.get_by_document("d-none", "kb1")
        assert results == []


class TestErrorReportGetOpenReports:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_get_open_reports_no_kb_filter(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        m1 = _error_report_model(status="pending")
        self.session.execute.return_value = _make_scalars_result([m1])
        repo = ErrorReportRepository(self.maker)

        results = await repo.get_open_reports()
        assert len(results) == 1
        assert results[0]["status"] == "pending"

    async def test_get_open_reports_with_kb_filter(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        m1 = _error_report_model(status="in_progress")
        self.session.execute.return_value = _make_scalars_result([m1])
        repo = ErrorReportRepository(self.maker)

        results = await repo.get_open_reports(kb_id="kb1")
        assert len(results) == 1

    async def test_get_open_reports_empty(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = ErrorReportRepository(self.maker)

        results = await repo.get_open_reports()
        assert results == []


class TestErrorReportDelete:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_delete_found(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        model = _error_report_model()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = ErrorReportRepository(self.maker)

        result = await repo.delete("r1")
        assert result is True
        self.session.delete.assert_awaited_once_with(model)
        self.session.commit.assert_awaited_once()

    async def test_delete_not_found(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        self.session.execute.return_value = _make_scalars_result([])
        repo = ErrorReportRepository(self.maker)

        result = await repo.delete("nonexistent")
        assert result is False

    async def test_delete_rollback_on_error(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        model = _error_report_model()
        self.session.execute.return_value = _make_scalars_result([model])
        self.session.delete.side_effect = SQLAlchemyError("fail")
        repo = ErrorReportRepository(self.maker)

        with pytest.raises(SQLAlchemyError):
            await repo.delete("r1")
        self.session.rollback.assert_awaited_once()


class TestErrorReportToDict:
    def test_to_dict(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        model = _error_report_model()
        d = ErrorReportRepository._to_dict(model)
        assert d["id"] == "r1"
        assert d["report_id"] == "r1"
        assert d["error_type"] == "typo"
        assert d["status"] == "pending"

    async def test_get_by_id_not_found(self):
        from src.stores.postgres.repositories.ownership import (
            ErrorReportRepository,
        )
        maker, session = _make_session_maker()
        session.execute.return_value = _make_scalars_result([])
        repo = ErrorReportRepository(maker)

        result = await repo.get_by_id("nonexistent")
        assert result is None
