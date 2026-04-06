"""Edge Log Repository — 엣지 서버 사용 로그 CRUD + 통계."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillEdgeLogModel

logger = logging.getLogger(__name__)


class DistillEdgeLogRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def save_batch(self, logs: list[dict[str, Any]]) -> int:
        async with self._session_maker() as session:
            count = 0
            for log in logs:
                model = DistillEdgeLogModel(
                    id=log.get("id", str(uuid.uuid4())),
                    profile_name=log["profile_name"],
                    store_id=log["store_id"],
                    query=log["query"],
                    answer=log.get("answer"),
                    confidence=log.get("confidence"),
                    latency_ms=log.get("latency_ms"),
                    success=log.get("success", True),
                    model_version=log.get("model_version"),
                    edge_timestamp=log.get("edge_timestamp", datetime.now(timezone.utc)),
                )
                session.add(model)
                count += 1
            try:
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save edge logs: %s", e)
                return 0
            return count

    async def list_logs(
        self,
        profile_name: str,
        store_id: str | None = None,
        success: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            stmt = select(DistillEdgeLogModel).where(
                DistillEdgeLogModel.profile_name == profile_name
            )
            if store_id:
                stmt = stmt.where(DistillEdgeLogModel.store_id == store_id)
            if success is not None:
                stmt = stmt.where(DistillEdgeLogModel.success == success)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.order_by(DistillEdgeLogModel.edge_timestamp.desc())
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = [self._to_dict(r) for r in result.scalars().all()]

            return {"items": items, "total": total}

    async def get_analytics(self, profile_name: str, days: int = 7) -> dict[str, Any]:
        async with self._session_maker() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            base = DistillEdgeLogModel.profile_name == profile_name
            time_filter = DistillEdgeLogModel.edge_timestamp >= cutoff

            total = (await session.execute(
                select(func.count()).select_from(DistillEdgeLogModel).where(base, time_filter)
            )).scalar() or 0

            success_count = (await session.execute(
                select(func.count()).select_from(DistillEdgeLogModel)
                .where(base, time_filter, DistillEdgeLogModel.success.is_(True))
            )).scalar() or 0

            avg_latency = (await session.execute(
                select(func.avg(DistillEdgeLogModel.latency_ms)).where(base, time_filter)
            )).scalar() or 0

            store_count = (await session.execute(
                select(func.count(func.distinct(DistillEdgeLogModel.store_id)))
                .where(base, time_filter)
            )).scalar() or 0

            return {
                "total_queries": total,
                "success_count": success_count,
                "success_rate": success_count / total if total else 0,
                "avg_latency_ms": round(float(avg_latency), 1),
                "store_count": store_count,
                "period_days": days,
            }

    async def list_failed(self, profile_name: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = (
                select(DistillEdgeLogModel)
                .where(
                    DistillEdgeLogModel.profile_name == profile_name,
                    DistillEdgeLogModel.success.is_(False),
                )
                .order_by(DistillEdgeLogModel.edge_timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    @staticmethod
    def _to_dict(model: DistillEdgeLogModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "profile_name": model.profile_name,
            "store_id": model.store_id,
            "query": model.query,
            "answer": model.answer,
            "confidence": model.confidence,
            "latency_ms": model.latency_ms,
            "success": model.success,
            "model_version": model.model_version,
            "edge_timestamp": model.edge_timestamp.isoformat() if model.edge_timestamp else None,
            "collected_at": model.collected_at.isoformat() if model.collected_at else None,
        }
