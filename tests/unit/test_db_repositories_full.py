"""Comprehensive tests for src/database/repositories/ — mocked AsyncSession."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_session_maker():
    """Create a mock async_sessionmaker that produces mock sessions."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock()
    maker.return_value = session
    maker.kw = {"bind": MagicMock(url="postgresql+asyncpg://localhost/test")}
    return maker, session


def _make_scalars_result(models):
    """Build a mock result that supports .scalars().all() and .scalars().first()."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = models
    scalars.first.return_value = models[0] if models else None
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = models[0] if models else None
    result.scalar.return_value = len(models)
    return result


# ===========================================================================
# GlossaryRepository
# ===========================================================================

class TestGlossaryRepository:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_new_term(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        await repo.save({"kb_id": "kb1", "term": "API", "definition": "test"})
        self.session.add.assert_called_once()
        self.session.commit.assert_awaited_once()

    async def test_save_update_existing(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        existing = MagicMock()
        existing.synonyms = "[]"
        self.session.execute.return_value = _make_scalars_result([existing])
        repo = GlossaryRepository(self.maker)

        await repo.save({"kb_id": "kb1", "term": "API", "definition": "updated"})
        self.session.commit.assert_awaited_once()

    async def test_get_by_id(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        model = MagicMock()
        model.id = "t1"
        model.kb_id = "kb1"
        model.term = "API"
        model.term_ko = None
        model.definition = "test"
        model.synonyms = "[]"
        model.abbreviations = "[]"
        model.related_terms = "[]"
        model.source = "manual"
        model.confidence_score = 1.0
        model.status = "approved"
        model.occurrence_count = 5
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)

        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        result = await repo.get_by_id("t1")
        assert result is not None
        assert result["term"] == "API"

    async def test_get_by_id_not_found(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        result = await repo.get_by_id("nonexistent")
        assert result is None

    async def test_delete_found(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        model = MagicMock()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = GlossaryRepository(self.maker)

        result = await repo.delete("t1")
        assert result is True
        self.session.delete.assert_awaited_once_with(model)

    async def test_delete_not_found(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = GlossaryRepository(self.maker)

        result = await repo.delete("nonexistent")
        assert result is False

    async def test_count_by_kb(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        result_mock = MagicMock()
        result_mock.scalar.return_value = 42
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.count_by_kb("kb1")
        assert count == 42

    async def test_bulk_delete(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        result_mock = MagicMock()
        result_mock.rowcount = 5
        self.session.execute.return_value = result_mock
        repo = GlossaryRepository(self.maker)

        count = await repo.bulk_delete(["t1", "t2", "t3", "t4", "t5"])
        assert count == 5

    async def test_bulk_delete_empty(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        repo = GlossaryRepository(self.maker)
        count = await repo.bulk_delete([])
        assert count == 0

    async def test_scope_filter_all(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        repo = GlossaryRepository(self.maker)
        result = repo._scope_filter("all")
        assert result is None

    async def test_scope_filter_specific_kb(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        repo = GlossaryRepository(self.maker)
        result = repo._scope_filter("kb1")
        assert result is not None


# ===========================================================================
# FeedbackRepository
# ===========================================================================

class TestFeedbackRepository:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_new(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = FeedbackRepository(self.maker)

        await repo.save({"id": "f1", "entry_id": "e1", "kb_id": "kb1", "feedback_type": "upvote"})
        self.session.add.assert_called_once()

    async def test_get_by_id(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository

        model = MagicMock()
        model.id = "f1"
        model.entry_id = "e1"
        model.kb_id = "kb1"
        model.user_id = "u1"
        model.feedback_type = "upvote"
        model.status = "pending"
        model.error_category = None
        model.description = None
        model.suggested_content = None
        model.reviewer_id = None
        model.review_note = None
        model.reviewed_at = None
        model.kts_impact = None
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)

        self.session.execute.return_value = _make_scalars_result([model])
        repo = FeedbackRepository(self.maker)

        result = await repo.get_by_id("f1")
        assert result is not None
        assert result["feedback_type"] == "upvote"

    async def test_delete_found(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository

        model = MagicMock()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = FeedbackRepository(self.maker)

        result = await repo.delete("f1")
        assert result is True

    async def test_count(self):
        from src.stores.postgres.repositories.feedback import FeedbackRepository

        result_mock = MagicMock()
        result_mock.scalar.return_value = 10
        self.session.execute.return_value = result_mock
        repo = FeedbackRepository(self.maker)

        count = await repo.count(status="pending")
        assert count == 10


# ===========================================================================
# DocumentOwnerRepository
# ===========================================================================

class TestDocumentOwnerRepository:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_new(self):
        from src.stores.postgres.repositories.ownership import DocumentOwnerRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = DocumentOwnerRepository(self.maker)

        await repo.save({"document_id": "d1", "kb_id": "kb1", "owner_user_id": "u1"})
        self.session.add.assert_called_once()

    async def test_get_by_document(self):
        from src.stores.postgres.repositories.ownership import DocumentOwnerRepository

        model = MagicMock()
        model.id = "o1"
        model.document_id = "d1"
        model.kb_id = "kb1"
        model.owner_user_id = "u1"
        model.backup_owner_user_id = None
        model.ownership_type = "primary"
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)

        self.session.execute.return_value = _make_scalars_result([model])
        repo = DocumentOwnerRepository(self.maker)

        result = await repo.get_by_document("d1", "kb1")
        assert result is not None
        assert result["owner_user_id"] == "u1"

    async def test_delete(self):
        from src.stores.postgres.repositories.ownership import DocumentOwnerRepository

        model = MagicMock()
        self.session.execute.return_value = _make_scalars_result([model])
        repo = DocumentOwnerRepository(self.maker)

        result = await repo.delete("d1", "kb1")
        assert result is True


# ===========================================================================
# TopicOwnerRepository
# ===========================================================================

class TestTopicOwnerRepository:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_new(self):
        from src.stores.postgres.repositories.ownership import TopicOwnerRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = TopicOwnerRepository(self.maker)

        await repo.save({
            "kb_id": "kb1",
            "topic_name": "K8s",
            "sme_user_id": "u1",
            "topic_keywords": ["kubernetes"],
            "escalation_chain": ["u2"],
        })
        self.session.add.assert_called_once()

    async def test_delete_not_found(self):
        from src.stores.postgres.repositories.ownership import TopicOwnerRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = TopicOwnerRepository(self.maker)

        result = await repo.delete("kb1", "nonexistent")
        assert result is False


# ===========================================================================
# ErrorReportRepository
# ===========================================================================

class TestErrorReportRepository:
    def setup_method(self):
        self.maker, self.session = _make_session_maker()

    async def test_save_new(self):
        from src.stores.postgres.repositories.ownership import ErrorReportRepository

        self.session.execute.return_value = _make_scalars_result([])
        repo = ErrorReportRepository(self.maker)

        await repo.save({
            "id": "r1",
            "document_id": "d1",
            "kb_id": "kb1",
            "error_type": "typo",
            "description": "Test error",
        })
        self.session.add.assert_called_once()

    async def test_get_by_id(self):
        from src.stores.postgres.repositories.ownership import ErrorReportRepository

        model = MagicMock()
        model.id = "r1"
        model.document_id = "d1"
        model.kb_id = "kb1"
        model.error_type = "typo"
        model.description = "Test"
        model.reporter_user_id = "u1"
        model.assigned_to = None
        model.status = "pending"
        model.priority = "medium"
        model.resolution_note = None
        model.resolved_at = None
        model.created_at = datetime.now(timezone.utc)
        model.updated_at = datetime.now(timezone.utc)

        self.session.execute.return_value = _make_scalars_result([model])
        repo = ErrorReportRepository(self.maker)

        result = await repo.get_by_id("r1")
        assert result is not None
        assert result["error_type"] == "typo"


# ===========================================================================
# KBRegistryRepository (uses own engine)
# ===========================================================================

class TestKBRegistryRepository:
    def test_url_normalization(self):
        from src.stores.postgres.repositories.kb_registry import KBRegistryRepository

        repo = KBRegistryRepository("postgresql://localhost/test")
        assert repo.database_url.startswith("postgresql+asyncpg://")

    def test_url_already_async(self):
        from src.stores.postgres.repositories.kb_registry import KBRegistryRepository

        repo = KBRegistryRepository("postgresql+asyncpg://localhost/test")
        assert repo.database_url == "postgresql+asyncpg://localhost/test"

    def test_model_to_dict(self):
        from src.stores.postgres.repositories.kb_registry import KBRegistryRepository

        model = MagicMock()
        model.id = "kb1"
        model.name = "Test KB"
        model.description = "A test"
        model.tier = "tier1"
        model.parent_kb_id = None
        model.organization_id = "org1"
        model.owner_id = "u1"
        model.dataset_id = None
        model.dataset_ids_by_env = {}
        model.sync_sources = []
        model.sync_schedule = None
        model.last_synced_at = None
        model.status = "active"
        model.settings = {}
        model.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        model.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = KBRegistryRepository._model_to_dict(model)
        assert result["name"] == "Test KB"
        assert result["kb_id"] == "kb1"

    def test_utc_helpers(self):
        from src.stores.postgres.repositories.kb_registry import _to_naive_utc, _to_aware_utc

        now_aware = datetime.now(timezone.utc)
        naive = _to_naive_utc(now_aware)
        assert naive.tzinfo is None

        assert _to_naive_utc(None) is None

        now_naive = datetime(2024, 1, 1)
        aware = _to_aware_utc(now_naive)
        assert aware.tzinfo is not None

        assert _to_aware_utc(None) is None

        # Already aware
        assert _to_aware_utc(now_aware) is now_aware
