"""Training Data Repository — 학습 데이터 CRUD + 통계."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillTrainingDataModel

logger = logging.getLogger(__name__)


class DistillTrainingDataRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def save_batch(self, entries: list[dict[str, Any]]) -> int:
        async with self._session_maker() as session:
            count = 0
            for entry in entries:
                model = DistillTrainingDataModel(
                    id=entry.get("id", str(uuid.uuid4())),
                    profile_name=entry["profile_name"],
                    question=entry["question"],
                    answer=entry["answer"],
                    source_type=entry.get("source_type", "manual"),
                    source_id=entry.get("source_id"),
                    kb_id=entry.get("kb_id"),
                    status=entry.get("status", "approved"),
                )
                session.add(model)
                count += 1
            try:
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save training data: %s", e)
                return 0
            return count

    async def list_data(
        self,
        profile_name: str,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            stmt = select(DistillTrainingDataModel).where(
                DistillTrainingDataModel.profile_name == profile_name
            )
            if status:
                stmt = stmt.where(DistillTrainingDataModel.status == status)
            if source_type:
                stmt = stmt.where(DistillTrainingDataModel.source_type == source_type)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.order_by(DistillTrainingDataModel.created_at.desc())
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = [self._to_dict(r) for r in result.scalars().all()]

            return {"items": items, "total": total}

    async def get_stats(self, profile_name: str) -> dict[str, Any]:
        async with self._session_maker() as session:
            base = DistillTrainingDataModel.profile_name == profile_name
            approved = DistillTrainingDataModel.status == "approved"

            total = (await session.execute(
                select(func.count()).select_from(DistillTrainingDataModel).where(base, approved)
            )).scalar() or 0

            stats: dict[str, Any] = {"total": total}
            for src_type in ("chunk_qa", "usage_log", "retrain", "manual"):
                count = (await session.execute(
                    select(func.count()).select_from(DistillTrainingDataModel)
                    .where(base, approved, DistillTrainingDataModel.source_type == src_type)
                )).scalar() or 0
                stats[src_type] = count

            return stats

    async def update_status(self, ids: list[str], status: str) -> int:
        async with self._session_maker() as session:
            stmt = (
                update(DistillTrainingDataModel)
                .where(DistillTrainingDataModel.id.in_(ids))
                .values(status=status)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    @staticmethod
    def _to_dict(model: DistillTrainingDataModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "profile_name": model.profile_name,
            "question": model.question,
            "answer": model.answer,
            "source_type": model.source_type,
            "source_id": model.source_id,
            "kb_id": model.kb_id,
            "status": model.status,
            "used_in_build": model.used_in_build,
            "created_at": model.created_at.isoformat() if model.created_at else None,
        }
