"""Usage Log Repository - PostgreSQL backed.

Tracks search and usage analytics for knowledge system.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.database.models import UsageLogModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class UsageLogRepository(BaseRepository):
    """PostgreSQL usage log repository."""

    async def log_search(
        self,
        knowledge_id: str,
        kb_id: str,
        user_id: str | None = None,
        usage_type: str = "hub_search",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Save a usage log entry."""
        async with self._session_maker() as session:
            try:
                model = UsageLogModel(
                    id=str(uuid.uuid4()),
                    knowledge_id=knowledge_id,
                    kb_id=kb_id,
                    user_id=user_id,
                    usage_type=usage_type,
                    context=json.dumps(context or {}, ensure_ascii=False, default=str),
                )
                session.add(model)
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning("Failed to log search usage: %s", e)

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return recent searches with pagination."""
        async with self._session_maker() as session:
            # Total count
            count_stmt = select(func.count()).select_from(UsageLogModel)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch rows
            stmt = (
                select(UsageLogModel)
                .order_by(UsageLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            return {
                "searches": [self._to_dict(r) for r in rows],
                "total": total,
            }

    async def get_analytics(self, days: int = 30) -> dict[str, Any]:
        """Aggregated analytics for the given period."""
        async with self._session_maker() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            base_filter = UsageLogModel.created_at >= cutoff

            # Total searches
            total_stmt = (
                select(func.count())
                .select_from(UsageLogModel)
                .where(base_filter)
            )
            total_searches = (await session.execute(total_stmt)).scalar() or 0

            # Unique users
            unique_users_stmt = (
                select(func.count(func.distinct(UsageLogModel.user_id)))
                .select_from(UsageLogModel)
                .where(base_filter)
            )
            unique_users = (await session.execute(unique_users_stmt)).scalar() or 0

            # Top queries (by knowledge_id which stores the query text)
            top_queries_stmt = (
                select(
                    UsageLogModel.knowledge_id,
                    func.count().label("count"),
                )
                .where(base_filter)
                .group_by(UsageLogModel.knowledge_id)
                .order_by(func.count().desc())
                .limit(20)
            )
            top_queries_result = await session.execute(top_queries_stmt)
            top_queries = [
                {"query": row.knowledge_id, "count": row.count}
                for row in top_queries_result
            ]

            # Top KBs
            top_kbs_stmt = (
                select(
                    UsageLogModel.kb_id,
                    func.count().label("count"),
                )
                .where(base_filter)
                .group_by(UsageLogModel.kb_id)
                .order_by(func.count().desc())
                .limit(20)
            )
            top_kbs_result = await session.execute(top_kbs_stmt)
            top_kbs = [
                {"kb_id": row.kb_id, "count": row.count}
                for row in top_kbs_result
            ]

            # Average results per query - parse from context JSON
            # We compute this in Python to avoid DB-specific JSON functions
            avg_results = 0.0
            avg_time_ms = 0.0
            if total_searches > 0:
                sample_stmt = (
                    select(UsageLogModel.context)
                    .where(base_filter)
                    .limit(1000)
                )
                sample_result = await session.execute(sample_stmt)
                contexts = sample_result.scalars().all()

                total_chunks_sum = 0
                time_sum = 0.0
                parsed_count = 0
                for ctx_str in contexts:
                    try:
                        ctx = json.loads(ctx_str) if isinstance(ctx_str, str) else {}
                        if "total_chunks" in ctx:
                            total_chunks_sum += ctx["total_chunks"]
                            parsed_count += 1
                        if "search_time_ms" in ctx:
                            time_sum += ctx["search_time_ms"]
                    except (json.JSONDecodeError, TypeError):
                        continue

                if parsed_count > 0:
                    avg_results = round(total_chunks_sum / parsed_count, 1)
                    avg_time_ms = round(time_sum / parsed_count, 1)

            return {
                "total_searches": total_searches,
                "unique_users": unique_users,
                "top_queries": top_queries,
                "top_kbs": top_kbs,
                "avg_results_per_query": avg_results,
                "avg_response_time_ms": avg_time_ms,
                "period_days": days,
            }

    async def get_by_user(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get search history for a specific user."""
        async with self._session_maker() as session:
            stmt = (
                select(UsageLogModel)
                .where(UsageLogModel.user_id == user_id)
                .order_by(UsageLogModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(model: UsageLogModel) -> dict[str, Any]:
        ctx = {}
        if model.context:
            try:
                ctx = json.loads(model.context) if isinstance(model.context, str) else {}
            except (json.JSONDecodeError, TypeError):
                ctx = {}

        return {
            "id": model.id,
            "knowledge_id": model.knowledge_id,
            "kb_id": model.kb_id,
            "usage_type": model.usage_type,
            "user_id": model.user_id,
            "session_id": model.session_id,
            "context": ctx,
            "created_at": model.created_at.isoformat() if model.created_at else None,
        }
