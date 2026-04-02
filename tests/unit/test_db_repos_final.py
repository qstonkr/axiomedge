"""Final DB repo tests for coverage push: data_source, ingestion_run, trust_score, lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


def _make_session_maker():
    """Create mock session maker. Works for both maker() and _get_session() patterns."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    maker = MagicMock()
    maker.return_value = session  # for maker() pattern
    # For _get_session() pattern: returns an awaitable that returns the session ctx mgr
    maker._get_session = AsyncMock(return_value=session)
    return maker, session


def _make_model(**fields):
    m = MagicMock()
    for k, v in fields.items():
        setattr(m, k, v)
    return m


# ===========================================================================
# DataSourceRepository
# ===========================================================================
class TestDataSourceRepository:
    def test_get(self):
        from src.database.repositories.data_source import DataSourceRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = DataSourceRepository(maker)

        async def _go():
            result = await repo.get("missing")
            assert result is None
        _run(_go())


# ===========================================================================
# IngestionRunRepository
# ===========================================================================
class TestIngestionRunRepository:
    def test_create(self):
        from src.database.repositories.ingestion_run import IngestionRunRepository
        maker, session = _make_session_maker()
        session.add = MagicMock()
        session.commit = AsyncMock()
        repo = IngestionRunRepository(maker)

        async def _go():
            await repo.create({"id": "run1", "kb_id": "kb1", "status": "running"})
        _run(_go())

    def test_get_by_id(self):
        from src.database.repositories.ingestion_run import IngestionRunRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = IngestionRunRepository(maker)

        async def _go():
            result = await repo.get_by_id("missing")
            assert result is None
        _run(_go())

    def test_list_by_kb(self):
        from src.database.repositories.ingestion_run import IngestionRunRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = IngestionRunRepository(maker)

        async def _go():
            results = await repo.list_by_kb("kb1")
            assert results == []
        _run(_go())


# ===========================================================================
# TrustScoreRepository
# ===========================================================================
class TestTrustScoreRepository:
    def test_save(self):
        from src.database.repositories.trust_score import TrustScoreRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()
        session.commit = AsyncMock()
        repo = TrustScoreRepository(maker)

        async def _go():
            await repo.save({
                "id": "ts1", "entry_id": "e1", "kb_id": "kb1",
                "kts_score": 0.85, "confidence_tier": "high",
            })
        _run(_go())

    def test_get_by_kb(self):
        from src.database.repositories.trust_score import TrustScoreRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = TrustScoreRepository(maker)

        async def _go():
            results = await repo.get_by_kb("kb1")
            assert results == []
        _run(_go())


# ===========================================================================
# LifecycleRepository
# ===========================================================================
class TestLifecycleRepository:
    def test_list_by_kb(self):
        from src.database.repositories.lifecycle import DocumentLifecycleRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentLifecycleRepository(maker)

        async def _go():
            results = await repo.list_by_kb("kb1")
            assert results == []
        _run(_go())

    def test_list_by_status(self):
        from src.database.repositories.lifecycle import DocumentLifecycleRepository
        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = DocumentLifecycleRepository(maker)

        async def _go():
            results = await repo.list_by_status("kb1", "active")
            assert results == []
        _run(_go())


# ===========================================================================
# L2 Semantic Cache
# ===========================================================================
class TestL2SemanticCache:
    def test_init(self):
        from src.cache.l2_semantic_cache import L2SemanticCache
        from unittest.mock import patch
        with patch("src.cache.l2_semantic_cache.aioredis") as mock_aioredis:
            mock_aioredis.from_url.return_value = MagicMock()
            cache = L2SemanticCache(redis_url="redis://localhost:6379")
            assert cache is not None

    def test_get_no_result(self):
        from src.cache.l2_semantic_cache import L2SemanticCache
        from unittest.mock import patch
        mock_redis = AsyncMock()
        mock_redis.keys = AsyncMock(return_value=[])
        with patch("src.cache.l2_semantic_cache.aioredis") as mock_aioredis:
            mock_aioredis.from_url.return_value = mock_redis
            cache = L2SemanticCache(redis_url="redis://localhost:6379")

        async def _go():
            result = await cache.get("test_key")
            assert result is None
        _run(_go())

    def test_clear(self):
        from src.cache.l2_semantic_cache import L2SemanticCache
        from unittest.mock import patch
        mock_redis = AsyncMock()
        mock_redis.keys = AsyncMock(return_value=["k1", "k2"])
        mock_redis.delete = AsyncMock(return_value=2)
        with patch("src.cache.l2_semantic_cache.aioredis") as mock_aioredis:
            mock_aioredis.from_url.return_value = mock_redis
            cache = L2SemanticCache(redis_url="redis://localhost:6379")

        async def _go():
            deleted = await cache.clear()
            assert deleted >= 0
        _run(_go())
