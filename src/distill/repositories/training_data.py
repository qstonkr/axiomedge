# pyright: reportGeneralTypeIssues=false
"""Training Data Repository — 학습 데이터 CRUD + 통계."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillTrainingDataModel

logger = logging.getLogger(__name__)


class DistillTrainingDataRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def save_batch(self, entries: list[dict[str, Any]]) -> int:
        if not entries:
            return 0
        async with self._session_maker() as session:
            models = [
                DistillTrainingDataModel(
                    id=entry.get("id", str(uuid.uuid4())),
                    profile_name=entry["profile_name"],
                    question=entry["question"],
                    answer=entry["answer"],
                    source_type=entry.get("source_type", "manual"),
                    source_id=entry.get("source_id"),
                    kb_id=entry.get("kb_id"),
                    status=entry.get("status", "approved"),
                    consistency_score=entry.get("consistency_score"),
                    generality_score=entry.get("generality_score"),
                    augmentation_verified=entry.get("augmentation_verified"),
                    augmented_from=entry.get("augmented_from"),
                    generation_batch_id=entry.get("generation_batch_id"),
                    source_chunk_fp=entry.get("source_chunk_fp"),
                )
                for entry in entries
            ]
            try:
                session.add_all(models)
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save training data: %s", e)
                return 0
            return len(models)

    async def list_data(
        self,
        profile_name: str,
        status: str | None = None,
        source_type: str | None = None,
        batch_id: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
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
            if batch_id:
                stmt = stmt.where(DistillTrainingDataModel.generation_batch_id == batch_id)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            allowed_sorts = {
                "created_at", "consistency_score", "generality_score", "status", "source_type",
            }
            if sort_by not in allowed_sorts:
                sort_by = "created_at"
            sort_col = getattr(DistillTrainingDataModel, sort_by)
            stmt = stmt.order_by(sort_col.asc() if sort_order == "asc" else sort_col.desc())
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = [self._to_dict(r) for r in result.scalars().all()]

            return {"items": items, "total": total}

    async def get_stats(self, profile_name: str) -> dict[str, Any]:
        async with self._session_maker() as session:
            base = DistillTrainingDataModel.profile_name == profile_name
            approved = DistillTrainingDataModel.status == "approved"
            pending = DistillTrainingDataModel.status == "pending"
            is_reformatted = DistillTrainingDataModel.source_type == "reformatted"

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

            # Reformatter 산출물은 status 를 승인/대기 양쪽 다 봐야 함
            # (pending 은 "리뷰 필요" 라서 사용자에게 반드시 노출해야 함)
            stats["reformatted_approved"] = (await session.execute(
                select(func.count()).select_from(DistillTrainingDataModel)
                .where(base, approved, is_reformatted)
            )).scalar() or 0
            stats["reformatted_pending"] = (await session.execute(
                select(func.count()).select_from(DistillTrainingDataModel)
                .where(base, pending, is_reformatted)
            )).scalar() or 0

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

    async def get_batch_stats(self, batch_id: str) -> dict[str, Any]:
        """배치별 통계."""
        async with self._session_maker() as session:
            base = DistillTrainingDataModel.generation_batch_id == batch_id

            total = (await session.execute(
                select(func.count()).select_from(DistillTrainingDataModel).where(base)
            )).scalar() or 0

            stats: dict[str, Any] = {"batch_id": batch_id, "total": total}
            for st in ("pending", "approved", "rejected"):
                count = (await session.execute(
                    select(func.count()).select_from(DistillTrainingDataModel)
                    .where(base, DistillTrainingDataModel.status == st)
                )).scalar() or 0
                stats[st] = count

            # 평균 점수
            for score_col in ("consistency_score", "generality_score"):
                col = getattr(DistillTrainingDataModel, score_col)
                avg = (await session.execute(
                    select(func.avg(col)).where(base)
                )).scalar()
                stats[f"avg_{score_col}"] = round(float(avg), 3) if avg else None

            return stats

    async def bulk_update_with_edit(self, updates: list[dict[str, Any]]) -> int:
        """상태 변경 + Q/A 텍스트 편집 동시 처리."""
        async with self._session_maker() as session:
            count = 0
            for upd in updates:
                item_id = upd.get("id")
                if not item_id:
                    continue
                values: dict[str, Any] = {}
                if "status" in upd:
                    values["status"] = upd["status"]
                if "question" in upd:
                    values["question"] = upd["question"]
                if "answer" in upd:
                    values["answer"] = upd["answer"]
                if "review_comment" in upd:
                    values["review_comment"] = upd["review_comment"]
                if values:
                    values["reviewed_at"] = datetime.now(timezone.utc)
                    stmt = (
                        update(DistillTrainingDataModel)
                        .where(DistillTrainingDataModel.id == item_id)
                        .values(**values)
                    )
                    await session.execute(stmt)
                    count += 1
            await session.commit()
            return count

    async def delete_by_source_type(
        self, profile_name: str, source_type: str,
    ) -> int:
        """특정 source_type의 데이터 일괄 삭제."""
        from sqlalchemy import delete as sa_delete
        async with self._session_maker() as session:
            stmt = (
                sa_delete(DistillTrainingDataModel)
                .where(
                    DistillTrainingDataModel.profile_name == profile_name,
                    DistillTrainingDataModel.source_type == source_type,
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def delete_by_batch(self, batch_id: str) -> int:
        """특정 배치의 데이터 일괄 삭제."""
        from sqlalchemy import delete as sa_delete
        async with self._session_maker() as session:
            stmt = (
                sa_delete(DistillTrainingDataModel)
                .where(DistillTrainingDataModel.generation_batch_id == batch_id)
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
            "consistency_score": model.consistency_score,
            "generality_score": model.generality_score,
            "augmentation_verified": model.augmentation_verified,
            "augmented_from": model.augmented_from,
            "generation_batch_id": model.generation_batch_id,
            "source_chunk_fp": getattr(model, "source_chunk_fp", None),
            "reviewed_at": model.reviewed_at.isoformat() if model.reviewed_at else None,
            "review_comment": model.review_comment,
        }
