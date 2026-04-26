# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""AuditLogRepository — write/list/archive (PR-12 J).

Best-effort write — 실패해도 비즈니스 흐름이 멈춰선 안 됨. Middleware 가
fire-and-forget 으로 호출하므로 logger.warning 만 남기고 swallow.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import AuditLogModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class AuditLogRepository(BaseRepository):
    async def write(
        self,
        *,
        knowledge_id: str,
        event_type: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """1행 audit log 영속화 — best-effort."""
        async with await self._get_session() as session:
            try:
                model = AuditLogModel(
                    id=str(_uuid.uuid4()),
                    knowledge_id=(knowledge_id or "_unknown")[:255],
                    event_type=(event_type or "unknown")[:50],
                    actor=(actor or "_system")[:100],
                    details=json.dumps(details or {}, ensure_ascii=False),
                    created_at=datetime.now(timezone.utc),
                )
                session.add(model)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning("Audit log write failed: %s", e)
                return False

    async def list_recent(
        self,
        *,
        knowledge_id: str | None = None,
        event_type: str | None = None,
        event_type_prefix: str | None = None,
        before: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent audit rows with optional filters.

        Args:
            knowledge_id: Exact match.
            event_type: Exact match (e.g. ``kb.update``).
            event_type_prefix: ``LIKE 'prefix%'`` match (e.g. ``unauth.``)
                — P0-W4: Streamlit ``unauth. only`` 토글 동작에 필요.
            before: Filter ``created_at < before``.
            limit: 1..1000.
        """
        async with await self._get_session() as session:
            try:
                stmt = select(AuditLogModel)
                if knowledge_id:
                    stmt = stmt.where(AuditLogModel.knowledge_id == knowledge_id)
                if event_type:
                    stmt = stmt.where(AuditLogModel.event_type == event_type)
                if event_type_prefix:
                    # PG escape — caller 가 ``%`` 를 그대로 넣을 위험은 낮지만
                    # 정확한 prefix 매칭만 허용하려 ``f"{prefix}%"`` 그대로 사용.
                    stmt = stmt.where(
                        AuditLogModel.event_type.like(f"{event_type_prefix}%"),
                    )
                if before:
                    stmt = stmt.where(AuditLogModel.created_at < before)
                stmt = stmt.order_by(AuditLogModel.created_at.desc()).limit(
                    max(1, min(limit, 1000))
                )
                result = await session.execute(stmt)
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError as e:
                logger.warning("Audit log list_recent failed: %s", e)
                return []

    async def archive_older_than(self, days: int) -> int:
        """N 일보다 오래된 row 삭제 — 별도 archive bucket 미설정 시 행 보존."""
        if days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    delete(AuditLogModel).where(
                        AuditLogModel.created_at < cutoff,
                    )
                )
                await session.commit()
                return int(result.rowcount or 0)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning("Audit log archive failed: %s", e)
                return 0

    @staticmethod
    def _to_dict(model: AuditLogModel) -> dict[str, Any]:
        try:
            details = json.loads(model.details) if model.details else {}
        except (json.JSONDecodeError, TypeError):
            details = {}
        return {
            "id": model.id,
            "knowledge_id": model.knowledge_id,
            "event_type": model.event_type,
            "actor": model.actor,
            "details": details,
            "created_at": model.created_at,
        }
