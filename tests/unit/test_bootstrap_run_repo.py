"""BootstrapRunRepo — create / complete / has_running / cleanup_stale."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.stores.postgres.repositories.bootstrap_run_repo import BootstrapRunRepo


def _make_session_maker():
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_ctx)

    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=session), session


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_adds_row_and_returns_id(self):
        maker, session = _make_session_maker()
        repo = BootstrapRunRepo(maker)

        # Simulate flush() populating .id on the added object
        fake_id = uuid4()

        def _capture(obj):
            obj.id = fake_id

        session.add = MagicMock(side_effect=_capture)

        run_id = await repo.create(
            kb_id="test", triggered_by="cron",
            sample_size=50, sample_strategy="stratified",
        )
        assert run_id == fake_id
        session.add.assert_called_once()
        session.flush.assert_awaited_once()


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_invokes_update(self):
        maker, session = _make_session_maker()
        repo = BootstrapRunRepo(maker)

        await repo.complete(
            uuid4(),
            status="completed",
            docs_scanned=50, candidates_found=3, llm_calls=5,
        )
        session.execute.assert_awaited_once()


class TestHasRunning:
    @pytest.mark.asyncio
    async def test_has_running_true_when_row_returned(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=uuid4())  # row id found
        repo = BootstrapRunRepo(maker)
        assert await repo.has_running("test") is True

    @pytest.mark.asyncio
    async def test_has_running_false_when_none(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=None)
        repo = BootstrapRunRepo(maker)
        assert await repo.has_running("test") is False


class TestCleanupStale:
    @pytest.mark.asyncio
    async def test_cleanup_stale_returns_rowcount(self):
        maker, session = _make_session_maker()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        session.execute = AsyncMock(return_value=mock_result)

        repo = BootstrapRunRepo(maker)
        cleared = await repo.cleanup_stale()
        assert cleared == 3
