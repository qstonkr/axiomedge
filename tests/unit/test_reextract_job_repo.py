"""ReextractJobRepo — queue/start/progress/complete/has_active."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.stores.postgres.repositories.reextract_job_repo import ReextractJobRepo


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


class TestQueue:
    @pytest.mark.asyncio
    async def test_queue_adds_row_and_returns_id(self):
        maker, session = _make_session_maker()
        fake_id = uuid4()

        def _capture(obj):
            obj.id = fake_id

        session.add = MagicMock(side_effect=_capture)
        repo = ReextractJobRepo(maker)
        job_id = await repo.queue(
            kb_id="test",
            triggered_by_user="admin@test",
            schema_version_from=1,
            schema_version_to=2,
        )
        assert job_id == fake_id


class TestStartAndProgress:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.start(uuid4(), docs_total=42)
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_progress_updates_counters(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.progress(uuid4(), docs_processed=10, docs_failed=1)
        session.execute.assert_awaited_once()


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_marks_done(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.complete(uuid4(), status="completed")
        session.execute.assert_awaited_once()


class TestHasActive:
    @pytest.mark.asyncio
    async def test_has_active_true_when_row_found(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=uuid4())
        repo = ReextractJobRepo(maker)
        assert await repo.has_active("test") is True

    @pytest.mark.asyncio
    async def test_has_active_false_when_none(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=None)
        repo = ReextractJobRepo(maker)
        assert await repo.has_active("test") is False
