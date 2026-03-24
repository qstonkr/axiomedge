"""Trust Score Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgTrustScoreRepository.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.models import TrustScoreModel

logger = logging.getLogger(__name__)


class TrustScoreRepository:
    """PostgreSQL trust score repository."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def _get_session(self) -> AsyncSession:
        return self._session_maker()

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                stmt = select(TrustScoreModel).where(
                    TrustScoreModel.entry_id == data["entry_id"],
                    TrustScoreModel.kb_id == data["kb_id"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    model = TrustScoreModel(**data)
                    session.add(model)

                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                raise

    async def get_by_entry(self, entry_id: str, kb_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(TrustScoreModel).where(
                TrustScoreModel.entry_id == entry_id,
                TrustScoreModel.kb_id == kb_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_kb(
        self,
        kb_id: str,
        min_score: float = 0.0,
        limit: int = 100,
        offset: int = 0,
        sort: str = "trending",
    ) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            try:
                stmt = (
                    select(TrustScoreModel)
                    .where(
                        TrustScoreModel.kb_id == kb_id,
                        TrustScoreModel.kts_score >= min_score,
                    )
                    .order_by(self._sort_expression(sort))
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError:
                raise

    async def get_stale_entries(self, kb_id: str, max_freshness: float = 0.3) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(TrustScoreModel)
                .where(
                    TrustScoreModel.kb_id == kb_id,
                    TrustScoreModel.freshness_score <= max_freshness,
                )
                .order_by(TrustScoreModel.freshness_score.asc())
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_needs_review(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            conditions = [
                or_(
                    TrustScoreModel.open_error_reports > 0,
                    TrustScoreModel.confidence_tier == "uncertain",
                )
            ]
            if kb_id:
                conditions.append(TrustScoreModel.kb_id == kb_id)

            stmt = (
                select(TrustScoreModel)
                .where(*conditions)
                .order_by(TrustScoreModel.kts_score.asc())
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def delete(self, entry_id: str, kb_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(TrustScoreModel).where(
                    TrustScoreModel.entry_id == entry_id,
                    TrustScoreModel.kb_id == kb_id,
                )
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
    def _sort_expression(sort: str):
        if sort == "top":
            return TrustScoreModel.kts_score.desc()
        if sort == "recent":
            return TrustScoreModel.updated_at.desc()
        # trending
        normalized_kts = case(
            (TrustScoreModel.kts_score > 1.0, TrustScoreModel.kts_score / 100.0),
            else_=TrustScoreModel.kts_score,
        )
        return ((0.7 * normalized_kts) + (0.3 * TrustScoreModel.freshness_score)).desc()

    @staticmethod
    def _to_dict(model: TrustScoreModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "entry_id": model.entry_id,
            "kb_id": model.kb_id,
            "kts_score": model.kts_score,
            "confidence_tier": model.confidence_tier,
            "source_credibility": model.source_credibility,
            "freshness_score": model.freshness_score,
            "user_validation_score": model.user_validation_score,
            "usage_score": model.usage_score,
            "hallucination_score": model.hallucination_score,
            "consistency_score": model.consistency_score,
            "source_type": model.source_type,
            "freshness_domain": model.freshness_domain,
            "upvotes": model.upvotes,
            "downvotes": model.downvotes,
            "expert_reviews": model.expert_reviews,
            "open_error_reports": model.open_error_reports,
            "view_count": model.view_count,
            "citation_count": model.citation_count,
            "bookmark_count": model.bookmark_count,
            "last_evaluated_at": model.last_evaluated_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
