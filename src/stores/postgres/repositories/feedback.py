"""Knowledge Feedback Repository - PostgreSQL backed.

"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import KnowledgeFeedbackModel
from src.stores.postgres.repositories.base import BaseRepository
from src.core.models import FeedbackType

logger = logging.getLogger(__name__)


class FeedbackRepository(BaseRepository):
    """PostgreSQL knowledge feedback repository."""

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                stmt = select(KnowledgeFeedbackModel).where(KnowledgeFeedbackModel.id == data["id"])
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    model = KnowledgeFeedbackModel(**data)
                    session.add(model)

                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_id(self, feedback_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(KnowledgeFeedbackModel).where(KnowledgeFeedbackModel.id == feedback_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_entry(self, entry_id: str, kb_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(KnowledgeFeedbackModel)
                .where(
                    KnowledgeFeedbackModel.entry_id == entry_id,
                    KnowledgeFeedbackModel.kb_id == kb_id,
                )
                .order_by(KnowledgeFeedbackModel.created_at.desc())
                .limit(500)
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_by_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(KnowledgeFeedbackModel)
                .where(KnowledgeFeedbackModel.user_id == user_id)
                .order_by(KnowledgeFeedbackModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_pending_reviews(
        self, kb_id: str | None = None, limit: int = 500,
    ) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = select(KnowledgeFeedbackModel).where(
                KnowledgeFeedbackModel.status == "pending",
            )
            if kb_id:
                stmt = stmt.where(KnowledgeFeedbackModel.kb_id == kb_id)
            stmt = stmt.order_by(KnowledgeFeedbackModel.created_at.asc()).limit(limit)
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_votes_for_entry(self, entry_id: str, kb_id: str) -> tuple[int, int]:
        async with await self._get_session() as session:
            upvote_stmt = (
                select(func.count())
                .select_from(KnowledgeFeedbackModel)
                .where(
                    KnowledgeFeedbackModel.entry_id == entry_id,
                    KnowledgeFeedbackModel.kb_id == kb_id,
                    KnowledgeFeedbackModel.feedback_type == FeedbackType.UPVOTE,
                )
            )
            upvotes = (await session.execute(upvote_stmt)).scalar() or 0

            downvote_stmt = (
                select(func.count())
                .select_from(KnowledgeFeedbackModel)
                .where(
                    KnowledgeFeedbackModel.entry_id == entry_id,
                    KnowledgeFeedbackModel.kb_id == kb_id,
                    KnowledgeFeedbackModel.feedback_type == FeedbackType.DOWNVOTE,
                )
            )
            downvotes = (await session.execute(downvote_stmt)).scalar() or 0

            return (upvotes, downvotes)

    async def list_all(
        self,
        status: str | None = None,
        feedback_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = select(KnowledgeFeedbackModel)
            if status:
                stmt = stmt.where(KnowledgeFeedbackModel.status == status)
            if feedback_type:
                stmt = stmt.where(KnowledgeFeedbackModel.feedback_type == feedback_type)
            stmt = stmt.order_by(KnowledgeFeedbackModel.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def count(self, status: str | None = None, feedback_type: str | None = None) -> int:
        async with await self._get_session() as session:
            stmt = select(func.count()).select_from(KnowledgeFeedbackModel)
            if status:
                stmt = stmt.where(KnowledgeFeedbackModel.status == status)
            if feedback_type:
                stmt = stmt.where(KnowledgeFeedbackModel.feedback_type == feedback_type)
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def delete(self, feedback_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(KnowledgeFeedbackModel).where(KnowledgeFeedbackModel.id == feedback_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False
                await session.delete(model)
                await session.commit()
                return True
            except SQLAlchemyError:
                await session.rollback()
                raise

    @staticmethod
    def _to_dict(model: KnowledgeFeedbackModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "feedback_id": str(model.id),
            "entry_id": model.entry_id,
            "kb_id": model.kb_id,
            "user_id": model.user_id,
            "feedback_type": model.feedback_type,
            "status": model.status,
            "error_category": model.error_category,
            "description": model.description,
            "suggested_content": model.suggested_content,
            "reviewer_id": model.reviewer_id,
            "review_note": model.review_note,
            "reviewed_at": model.reviewed_at,
            "kts_impact": model.kts_impact,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
