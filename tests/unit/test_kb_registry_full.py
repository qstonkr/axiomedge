"""Full unit tests for src/database/repositories/kb_registry.py — 160 uncovered lines."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.stores.postgres.repositories.kb_registry import (
    KBRegistryRepository,
    _utc_now,
    _to_naive_utc,
    _to_aware_utc,
)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_utc_now(self):
        dt = _utc_now()
        assert isinstance(dt, datetime)
        assert dt.tzinfo is None  # naive UTC

    def test_to_naive_utc_none(self):
        assert _to_naive_utc(None) is None

    def test_to_naive_utc_naive(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        assert _to_naive_utc(dt) is dt

    def test_to_naive_utc_aware(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _to_naive_utc(dt)
        assert result.tzinfo is None
        assert result.hour == 12

    def test_to_aware_utc_none(self):
        assert _to_aware_utc(None) is None

    def test_to_aware_utc_already_aware(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert _to_aware_utc(dt) is dt

    def test_to_aware_utc_naive(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = _to_aware_utc(dt)
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# KBRegistryRepository init
# ---------------------------------------------------------------------------

class TestInit:
    def test_url_conversion(self):
        repo = KBRegistryRepository("postgresql://host/db")
        assert repo.database_url.startswith("postgresql+asyncpg://")

    def test_asyncpg_url_preserved(self):
        repo = KBRegistryRepository("postgresql+asyncpg://host/db")
        assert repo.database_url == "postgresql+asyncpg://host/db"

    def test_session_maker_none_before_init(self):
        repo = KBRegistryRepository("postgresql+asyncpg://host/db")
        assert repo.session_maker is None


# ---------------------------------------------------------------------------
# Repository methods with mocked session
# ---------------------------------------------------------------------------

def _make_mock_model(**kwargs):
    """Create a mock KBConfigModel."""
    m = MagicMock()
    m.id = kwargs.get("id", "kb1")
    m.name = kwargs.get("name", "Test KB")
    m.description = kwargs.get("description", "")
    m.tier = kwargs.get("tier", "global")
    m.parent_kb_id = kwargs.get("parent_kb_id", None)
    m.organization_id = kwargs.get("organization_id", None)
    m.owner_id = kwargs.get("owner_id", None)
    m.dataset_id = kwargs.get("dataset_id", None)
    m.dataset_ids_by_env = kwargs.get("dataset_ids_by_env", {})
    m.sync_sources = kwargs.get("sync_sources", [])
    m.sync_schedule = kwargs.get("sync_schedule", None)
    m.last_synced_at = kwargs.get("last_synced_at", None)
    m.status = kwargs.get("status", "active")
    m.settings = kwargs.get("settings", {})
    m.created_at = kwargs.get("created_at", datetime(2024, 1, 1))
    m.updated_at = kwargs.get("updated_at", datetime(2024, 1, 1))
    m.document_count = kwargs.get("document_count", 0)
    m.chunk_count = kwargs.get("chunk_count", 0)
    m.last_ingested_at = kwargs.get("last_ingested_at", None)
    m.data_classification = "internal"
    m.storage_backend = "qdrant"
    m.department_id = None
    return m


class _FakeSession:
    """Minimal fake async session for testing."""

    def __init__(self, model=None, models=None, scalar=None):
        self._model = model
        self._models = models or []
        self._scalar = scalar
        self._committed = False
        self._rolled_back = False

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._model)
        result.scalars = MagicMock()
        result.scalars.return_value.all = MagicMock(return_value=self._models)
        result.scalar = MagicMock(return_value=self._scalar)
        result.all = MagicMock(return_value=[])
        return result

    def add(self, obj):
        pass

    async def commit(self):
        self._committed = True

    async def rollback(self):
        self._rolled_back = True

    async def delete(self, obj):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_repo_with_session(model=None, models=None, scalar=None):
    repo = KBRegistryRepository("postgresql+asyncpg://host/db")
    session = _FakeSession(model=model, models=models, scalar=scalar)

    async def _get_session():
        return session

    repo._get_session = _get_session
    repo._session_maker = MagicMock()  # not None
    return repo, session


class TestGetKb:
    def test_found(self):
        model = _make_mock_model(id="kb1", name="Test")
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.get_kb("kb1"))
        assert result is not None
        assert result["kb_id"] == "kb1"
        assert result["name"] == "Test"

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.get_kb("missing"))
        assert result is None


class TestGetKbByName:
    def test_found(self):
        model = _make_mock_model(name="My KB")
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.get_kb_by_name("My KB"))
        assert result is not None


class TestCreateKb:
    def test_success(self):
        repo, session = _make_repo_with_session()
        with patch("src.stores.postgres.repositories.kb_registry.KBConfigModel") as MockModel:
            MockModel.return_value = MagicMock()
            result = _run(repo.create_kb({"id": "kb2", "name": "New KB"}))
            assert result["name"] == "New KB"


class TestUpdateKb:
    def test_found(self):
        model = _make_mock_model()
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.update_kb("kb1", {"name": "Updated"}))
        assert result is not None

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.update_kb("missing", {"name": "X"}))
        assert result is None


class TestDeleteKb:
    def test_found(self):
        model = _make_mock_model()
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.delete_kb("kb1"))
        assert result is True

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.delete_kb("missing"))
        assert result is False


class TestListAll:
    def test_list(self):
        models = [_make_mock_model(id="kb1"), _make_mock_model(id="kb2")]
        repo, _ = _make_repo_with_session(models=models)
        result = _run(repo.list_all())
        assert len(result) == 2


class TestListByTier:
    def test_list(self):
        models = [_make_mock_model(tier="global")]
        repo, _ = _make_repo_with_session(models=models)
        result = _run(repo.list_by_tier("global"))
        assert len(result) == 1


class TestListByStatus:
    def test_list(self):
        models = [_make_mock_model(status="active")]
        repo, _ = _make_repo_with_session(models=models)
        result = _run(repo.list_by_status("active"))
        assert len(result) == 1


class TestCount:
    def test_count_all(self):
        repo, _ = _make_repo_with_session(scalar=10)
        result = _run(repo.count())
        assert result == 10

    def test_count_with_tier(self):
        repo, _ = _make_repo_with_session(scalar=5)
        result = _run(repo.count(tier="global"))
        assert result == 5


class TestUpdateCounts:
    def test_success(self):
        model = _make_mock_model(document_count=10, chunk_count=100)
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.update_counts("kb1", 5, 50))
        assert result is True
        assert model.document_count == 15
        assert model.chunk_count == 150

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.update_counts("missing", 5, 50))
        assert result is False


class TestSyncCounts:
    def test_success(self):
        model = _make_mock_model(chunk_count=100)
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.sync_counts_from_qdrant("kb1", 200))
        assert result is True
        assert model.chunk_count == 200

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.sync_counts_from_qdrant("missing", 200))
        assert result is False


class TestMarkSynced:
    def test_success(self):
        model = _make_mock_model()
        repo, _ = _make_repo_with_session(model=model)
        result = _run(repo.mark_synced("kb1"))
        assert result is True
        assert model.status == "active"

    def test_not_found(self):
        repo, _ = _make_repo_with_session(model=None)
        result = _run(repo.mark_synced("missing"))
        assert result is False


class TestHealthCheck:
    def test_healthy(self):
        repo, _ = _make_repo_with_session(scalar=1)
        result = _run(repo.health_check())
        assert result is True


class TestGetSessionNotInitialized:
    def test_raises(self):
        repo = KBRegistryRepository("postgresql+asyncpg://host/db")
        with pytest.raises(RuntimeError, match="not initialized"):
            _run(repo._get_session())


class TestModelToDict:
    def test_conversion(self):
        model = _make_mock_model(id="kb1", name="Test")
        result = KBRegistryRepository._model_to_dict(model)
        assert result["kb_id"] == "kb1"
        assert result["name"] == "Test"
        assert result["tier"] == "global"
