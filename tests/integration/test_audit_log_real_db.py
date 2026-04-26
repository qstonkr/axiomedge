"""Real PG round-trip — AuditLogRepository + archive (C1 / P1-3 / P2-5).

write/list_recent/archive_older_than 의 PG 동작 검증. ``idx_audit_created_at``
인덱스 존재도 함께 확인.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text

from src.stores.postgres.models import AuditLogModel
from src.stores.postgres.repositories.audit_log import AuditLogRepository

pytestmark = pytest.mark.requires_postgres


async def _cleanup(pg_session_maker, kb_id: str) -> None:
    async with pg_session_maker() as session:
        await session.execute(
            delete(AuditLogModel).where(
                AuditLogModel.knowledge_id == kb_id,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_write_and_list_round_trip(pg_session_maker):
    repo = AuditLogRepository(pg_session_maker)
    kb_id = f"_test_audit_{datetime.now().timestamp():.0f}"
    try:
        for ev in ("kb.create", "kb.update", "kb.delete"):
            ok = await repo.write(
                knowledge_id=kb_id, event_type=ev, actor="alice",
                details={"changes": [ev]},
            )
            assert ok is True

        rows = await repo.list_recent(knowledge_id=kb_id, limit=100)
        assert len(rows) == 3
        # 최근순 정렬
        assert rows[0]["event_type"] == "kb.delete"
        assert rows[-1]["event_type"] == "kb.create"
        assert all(r["actor"] == "alice" for r in rows)
    finally:
        await _cleanup(pg_session_maker, kb_id)


@pytest.mark.asyncio
async def test_unauth_event_filter(pg_session_maker):
    """P2-5: actor=_system 인 row 의 event_type 이 unauth.* 로 prefix 됨."""
    repo = AuditLogRepository(pg_session_maker)
    kb_id = f"_test_unauth_{datetime.now().timestamp():.0f}"
    try:
        await repo.write(
            knowledge_id=kb_id, event_type="kb.update",
            actor="alice", details={},
        )
        await repo.write(
            knowledge_id=kb_id, event_type="unauth.kb.delete",
            actor="_system", details={"path": "/api/v1/kb/x"},
        )

        unauth_only = await repo.list_recent(
            knowledge_id=kb_id, event_type="unauth.kb.delete",
        )
        assert len(unauth_only) == 1
        assert unauth_only[0]["actor"] == "_system"
    finally:
        await _cleanup(pg_session_maker, kb_id)


@pytest.mark.asyncio
async def test_archive_older_than_deletes(pg_session_maker):
    repo = AuditLogRepository(pg_session_maker)
    kb_id = f"_test_archive_{datetime.now().timestamp():.0f}"
    try:
        # 새 row (보존 대상)
        await repo.write(
            knowledge_id=kb_id, event_type="kb.update",
            actor="alice", details={},
        )

        # 200 일 이전 row 를 직접 INSERT — repo.write 는 항상 now 라
        # session 으로 직접 작성.
        import uuid
        async with pg_session_maker() as session:
            session.add(AuditLogModel(
                id=str(uuid.uuid4()),
                knowledge_id=kb_id, event_type="kb.update",
                actor="alice", details="{}",
                created_at=datetime.now(timezone.utc) - timedelta(days=200),
            ))
            await session.commit()

        deleted = await repo.archive_older_than(days=180)
        assert deleted >= 1

        # 새 row 는 그대로
        rows = await repo.list_recent(knowledge_id=kb_id)
        assert any(
            r["created_at"] >= datetime.now(timezone.utc) - timedelta(hours=1)
            for r in rows
        )
    finally:
        await _cleanup(pg_session_maker, kb_id)


@pytest.mark.asyncio
async def test_idx_audit_created_at_exists(pg_session_maker):
    """P1-2 0012 마이그레이션 — idx_audit_created_at 인덱스 적용 검증."""
    async with pg_session_maker() as session:
        result = await session.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'knowledge_audit_logs' "
            "AND indexname = 'idx_audit_created_at'"
        ))
        rows = result.all()
        assert len(rows) == 1
