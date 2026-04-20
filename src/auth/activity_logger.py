"""User activity logging — log actions and query activity history."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class ActivityLogger:
    """User activity logging and querying."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    async def log_activity(
        self,
        user_id: str,
        activity_type: str,
        resource_type: str,
        resource_id: str | None = None,
        kb_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log a user activity (non-blocking best-effort)."""
        from src.auth.models import UserActivityLogModel

        try:
            async with self._session() as session:
                session.add(UserActivityLogModel(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    activity_type=activity_type,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    kb_id=kb_id,
                    details=details or {},
                    ip_address=ip_address,
                    user_agent=user_agent,
                ))
                await session.commit()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Activity log failed: %s", e)

    async def log_secret_event(
        self,
        actor_user_id: str,
        action: str,
        source_id: str,
        organization_id: str,
        success: bool,
        error: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Connector secret 의 create/update/delete/access 감사 로그.

        token value 자체는 절대 details 에 들어가지 않음. action 은
        ``secret_create`` / ``secret_update`` / ``secret_delete`` /
        ``secret_access`` / ``secret_rotate`` 중 하나.
        """
        await self.log_activity(
            user_id=actor_user_id,
            activity_type=action,
            resource_type="data_source_secret",
            resource_id=source_id,
            kb_id=None,
            details={
                "organization_id": organization_id,
                "success": success,
                "error": error,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def get_user_activities(
        self,
        user_id: str,
        activity_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get user's activity history."""
        from src.auth.models import UserActivityLogModel

        async with self._session() as session:
            q = select(UserActivityLogModel).where(
                UserActivityLogModel.user_id == user_id
            )
            if activity_type:
                q = q.where(UserActivityLogModel.activity_type == activity_type)

            q = q.order_by(UserActivityLogModel.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(q)
            return [
                {
                    "id": a.id,
                    "activity_type": a.activity_type,
                    "resource_type": a.resource_type,
                    "resource_id": a.resource_id,
                    "kb_id": a.kb_id,
                    "details": a.details,
                    "created_at": str(a.created_at),
                }
                for a in result.scalars().all()
            ]

    async def get_activity_summary(self, user_id: str, days: int = 30) -> dict:
        """Get activity summary for dashboard."""
        from src.auth.models import UserActivityLogModel

        async with self._session() as session:
            cutoff = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=days)

            result = await session.execute(
                select(
                    UserActivityLogModel.activity_type,
                    func.count(UserActivityLogModel.id),
                )
                .where(
                    UserActivityLogModel.user_id == user_id,
                    UserActivityLogModel.created_at >= cutoff,
                )
                .group_by(UserActivityLogModel.activity_type)
            )
            counts = {row[0]: row[1] for row in result.all()}
            return {
                "period_days": days,
                "total": sum(counts.values()),
                "by_type": counts,
            }
