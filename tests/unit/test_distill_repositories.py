"""Coverage backfill — distill repositories (profile, build, training_data, edge_server, edge_log, base_model).

All repos follow the same pattern: async CRUD with SQLAlchemy session.
Tests use mocked session_maker for isolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==========================================================================
# Helpers
# ==========================================================================

def _mock_session_maker():
    """Create a mock async session_maker."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    maker = MagicMock()
    maker.return_value = session
    return maker, session


def _mock_model(**fields):
    """Create a mock SQLAlchemy model with given fields."""
    m = MagicMock()
    for k, v in fields.items():
        setattr(m, k, v)
    m.__dict__.update(fields)
    return m


# ==========================================================================
# ProfileRepository
# ==========================================================================

class TestProfileRepository:
    def _make_repo(self):
        from src.distill.repositories.profile import DistillProfileRepository
        maker, session = _mock_session_maker()
        return DistillProfileRepository(maker), session

    async def test_list_all(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        result = await repo.list_all()
        assert result == []

    async def test_get_found(self):
        repo, session = self._make_repo()
        model = _mock_model(name="test", search_group="sg", config="{}", enabled=True,
                           created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        session.execute.return_value = mock_result
        result = await repo.get("test")
        assert result is not None

    async def test_get_not_found(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        result = await repo.get("nonexistent")
        assert result is None

    async def test_delete_found(self):
        repo, session = self._make_repo()
        model = _mock_model(name="test")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        session.execute.return_value = mock_result
        result = await repo.delete("test")
        assert result is True


# ==========================================================================
# BuildRepository
# ==========================================================================

class TestBuildRepository:
    def _make_repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        maker, session = _mock_session_maker()
        return DistillBuildRepository(maker), session

    async def test_create(self):
        repo, session = self._make_repo()
        model = _mock_model(
            build_id="b1", profile_name="p1", status="pending",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            steps=None, error_message=None, error_step=None,
            s3_uri=None, sha256=None, deployed_at=None,
            training_loss=None, eval_loss=None, eval_faithfulness=None,
            eval_relevancy=None,
        )
        session.refresh = AsyncMock()
        # patch the model creation
        with patch("src.distill.repositories.build.DistillBuildModel", return_value=model):
            result = await repo.create(build_id="b1", profile_name="p1", status="pending")
        assert result is not None

    async def test_list(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        result = await repo.list_all(limit=10)
        assert result == []


# ==========================================================================
# TrainingDataRepository
# ==========================================================================

class TestTrainingDataRepository:
    def _make_repo(self):
        from src.distill.repositories.training_data import DistillTrainingDataRepository
        maker, session = _mock_session_maker()
        return DistillTrainingDataRepository(maker), session

    async def test_list(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        mock_count = MagicMock()
        mock_count.scalar.return_value = 0
        session.execute.side_effect = [mock_count, mock_result]
        result = await repo.list_data(profile_name="p1")
        assert "items" in result or isinstance(result, dict)

    async def test_count(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        session.execute.return_value = mock_result
        count = await repo.get_stats(profile_name="p1")
        assert count is not None


# ==========================================================================
# EdgeServerRepository
# ==========================================================================

class TestEdgeServerRepository:
    def _make_repo(self):
        from src.distill.repositories.edge_server import DistillEdgeServerRepository
        maker, session = _mock_session_maker()
        return DistillEdgeServerRepository(maker), session

    async def test_list(self):
        repo, session = self._make_repo()
        # list_servers does an UPDATE first (mark stale offline), then SELECT
        mock_update = MagicMock()
        mock_update.rowcount = 0
        mock_select = MagicMock()
        mock_select.scalars.return_value.all.return_value = []
        session.execute.side_effect = [mock_update, mock_select]
        result = await repo.list_servers(profile_name="p1")
        assert result is not None

    async def test_get(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        result = await repo.get_server("store-1")
        assert result is None


# ==========================================================================
# EdgeLogRepository
# ==========================================================================

class TestEdgeLogRepository:
    def _make_repo(self):
        from src.distill.repositories.edge_log import DistillEdgeLogRepository
        maker, session = _mock_session_maker()
        return DistillEdgeLogRepository(maker), session

    async def test_list(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        result = await repo.list_logs(profile_name="p1", limit=20)
        assert result is not None


# ==========================================================================
# BaseModelRepository
# ==========================================================================

class TestBaseModelRepository:
    def _make_repo(self):
        from src.distill.repositories.base_model import DistillBaseModelRepository
        maker, session = _mock_session_maker()
        return DistillBaseModelRepository(maker), session

    async def test_list(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result
        result = await repo.list_all(enabled_only=False)
        assert result == []

    async def test_get(self):
        repo, session = self._make_repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        result = await repo.get("model-id")
        assert result is None
