"""AuditLogRepository — PR-12 (J).

- write 라운드트립
- DB 에러 시 swallow + False 반환
- archive_older_than rowcount
- list_recent 필터링
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.postgres.repositories.audit_log import AuditLogRepository


def _make_session_maker():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=session), session


class TestWrite:
    @pytest.mark.asyncio
    async def test_persists_row(self):
        maker, session = _make_session_maker()
        repo = AuditLogRepository(maker)

        ok = await repo.write(
            knowledge_id="kb-a", event_type="kb.update",
            actor="user-1", details={"changes": {"name": "x"}},
        )
        assert ok is True
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

        added = session.add.call_args[0][0]
        assert added.knowledge_id == "kb-a"
        assert added.event_type == "kb.update"
        assert added.actor == "user-1"

    @pytest.mark.asyncio
    async def test_swallows_db_error(self):
        from sqlalchemy.exc import SQLAlchemyError
        maker, session = _make_session_maker()
        session.commit = AsyncMock(side_effect=SQLAlchemyError("dead"))
        repo = AuditLogRepository(maker)
        ok = await repo.write(
            knowledge_id="kb", event_type="kb.delete", actor="x",
        )
        assert ok is False
        session.rollback.assert_awaited_once()


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_returns_rowcount(self):
        maker, session = _make_session_maker()
        result_obj = MagicMock(rowcount=42)
        session.execute = AsyncMock(return_value=result_obj)
        repo = AuditLogRepository(maker)

        n = await repo.archive_older_than(days=180)
        assert n == 42

    @pytest.mark.asyncio
    async def test_archive_zero_days_noop(self):
        maker, session = _make_session_maker()
        repo = AuditLogRepository(maker)
        n = await repo.archive_older_than(days=0)
        assert n == 0
        session.execute.assert_not_called()


class TestListRecent:
    @pytest.mark.asyncio
    async def test_returns_serialized_dicts(self):
        maker, session = _make_session_maker()
        m1 = MagicMock(
            id="r1", knowledge_id="kb", event_type="kb.update",
            actor="u", details='{"x":1}',
            created_at=datetime.now(timezone.utc),
        )
        scalars_result = MagicMock()
        scalars_result.all = MagicMock(return_value=[m1])
        result_obj = MagicMock()
        result_obj.scalars = MagicMock(return_value=scalars_result)
        session.execute = AsyncMock(return_value=result_obj)
        repo = AuditLogRepository(maker)

        rows = await repo.list_recent(knowledge_id="kb", limit=10)
        assert len(rows) == 1
        assert rows[0]["details"] == {"x": 1}
