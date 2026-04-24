"""Unit tests for SchemaCandidateRepo — mock-based (no real DB).

Tests focus on: correct query construction, parameter propagation,
upsert aggregation math. Full DB behavior belongs in integration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.postgres.repositories.schema_candidate_repo import (
    SchemaCandidateRepo,
)


def _make_session_maker(scalar_return=None, execute_return=None):
    """Build a session_maker that returns an AsyncMock session.

    session.__aenter__ / __aexit__ / begin mimic ``async with`` semantics.
    """
    session = MagicMock()
    session.scalar = AsyncMock(return_value=scalar_return)
    session.add = MagicMock()
    session.execute = AsyncMock(return_value=execute_return)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    # async with session.begin() as tx:
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_ctx)

    # async with session_maker() as session:
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    maker = MagicMock(return_value=session)
    return maker, session


class TestUpsertInsert:
    @pytest.mark.asyncio
    async def test_inserts_new_candidate_when_not_found(self):
        maker, session = _make_session_maker(scalar_return=None)
        repo = SchemaCandidateRepo(maker)

        await repo.upsert(
            kb_id="test", candidate_type="node", label="Meeting",
            confidence=0.9, examples=[{"sample": "ok"}],
        )

        # New row added
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert added.label == "Meeting"
        assert added.kb_id == "test"
        assert added.frequency == 1
        assert added.confidence_avg == 0.9
        assert added.confidence_min == 0.9
        assert added.confidence_max == 0.9
        assert added.status == "pending"


class TestUpsertUpdate:
    @pytest.mark.asyncio
    async def test_increments_frequency_on_existing(self):
        existing = MagicMock()
        existing.frequency = 2
        existing.confidence_avg = 0.80
        existing.confidence_min = 0.75
        existing.confidence_max = 0.85
        existing.examples = [{"sample": "old"}]

        maker, session = _make_session_maker(scalar_return=existing)
        repo = SchemaCandidateRepo(maker)

        await repo.upsert(
            kb_id="test", candidate_type="node", label="Meeting",
            confidence=0.90, examples=[{"sample": "new"}],
        )

        # No add call (row exists), but in-place update
        session.add.assert_not_called()
        assert existing.frequency == 3
        # new_avg = (0.80 * 2 + 0.90) / 3 = 0.833...
        assert abs(existing.confidence_avg - 0.8333) < 0.01
        assert existing.confidence_min == 0.75  # unchanged
        assert existing.confidence_max == 0.90  # bumped


class TestListPending:
    @pytest.mark.asyncio
    async def test_returns_result_scalars_all(self):
        maker, session = _make_session_maker()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=["a", "b"])
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        session.execute = AsyncMock(return_value=mock_result)

        repo = SchemaCandidateRepo(maker)
        rows = await repo.list_pending("test")
        assert rows == ["a", "b"]
        session.execute.assert_awaited_once()


class TestListApprovedLabels:
    @pytest.mark.asyncio
    async def test_extracts_label_column(self):
        maker, session = _make_session_maker()
        mock_result = MagicMock()
        mock_result.all = MagicMock(
            return_value=[("Meeting",), ("Event",)],
        )
        session.execute = AsyncMock(return_value=mock_result)

        repo = SchemaCandidateRepo(maker)
        labels = await repo.list_approved_labels("test", "node")
        assert labels == ["Meeting", "Event"]


class TestDecide:
    @pytest.mark.asyncio
    async def test_decide_invokes_update(self):
        maker, session = _make_session_maker()
        repo = SchemaCandidateRepo(maker)

        await repo.decide(
            kb_id="test", candidate_type="node", label="Meeting",
            status="approved", decided_by="admin@test",
        )
        # update() called, and session committed via begin() context
        session.execute.assert_awaited_once()
