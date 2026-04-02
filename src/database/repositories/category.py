"""Category Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgCategoryRepository.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.database.models import KnowledgeCategoryModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class CategoryRepository(BaseRepository):
    """PostgreSQL repository for knowledge categories."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        super().__init__(session_factory)
        self._l1_cache: list[dict[str, Any]] | None = None

    async def get_l1_categories(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        if use_cache and self._l1_cache is not None:
            return self._l1_cache

        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(KnowledgeCategoryModel)
                    .where(KnowledgeCategoryModel.level == 1, KnowledgeCategoryModel.is_active.is_(True))
                    .order_by(KnowledgeCategoryModel.sort_order)
                )
                rows = result.scalars().all()
                categories = [
                    {
                        "id": str(row.id),
                        "name": row.name,
                        "description": row.description or "",
                        "keywords": row.keywords if isinstance(row.keywords, list) else [],
                        "sort_order": row.sort_order,
                    }
                    for row in rows
                ]
                self._l1_cache = categories
                return categories
        except SQLAlchemyError:
            logger.exception("Failed to load L1 categories from DB")
            return []

    def invalidate_cache(self) -> None:
        self._l1_cache = None

    async def get_all_categories(self) -> list[dict[str, Any]]:
        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(KnowledgeCategoryModel)
                    .where(KnowledgeCategoryModel.is_active.is_(True))
                    .order_by(KnowledgeCategoryModel.level, KnowledgeCategoryModel.sort_order)
                )
                rows = result.scalars().all()
                return [
                    {
                        "id": str(row.id),
                        "level": row.level,
                        "name": row.name,
                        "name_ko": row.name_ko,
                        "description": row.description or "",
                        "keywords": row.keywords if isinstance(row.keywords, list) else [],
                        "parent_id": str(row.parent_id) if row.parent_id else None,
                        "sort_order": row.sort_order,
                        "is_active": row.is_active,
                    }
                    for row in rows
                ]
        except SQLAlchemyError:
            logger.exception("Failed to load categories from DB")
            return []

    async def create_category(self, data: dict[str, Any]) -> dict[str, Any] | None:
        try:
            async with self._session_maker() as session:
                orm = KnowledgeCategoryModel(**data)
                session.add(orm)
                await session.commit()
                await session.refresh(orm)
                self.invalidate_cache()
                return {"id": str(orm.id), "name": orm.name, "level": orm.level}
        except SQLAlchemyError:
            logger.exception("Failed to create category")
            return None

    async def update_category(self, category_id: UUID, data: dict[str, Any]) -> bool:
        try:
            async with self._session_maker() as session:
                from sqlalchemy import func as sa_func
                data["updated_at"] = sa_func.now()
                await session.execute(
                    update(KnowledgeCategoryModel)
                    .where(KnowledgeCategoryModel.id == category_id)
                    .values(**data)
                )
                await session.commit()
                self.invalidate_cache()
                return True
        except SQLAlchemyError:
            logger.exception("Failed to update category %s", category_id)
            return False

    async def soft_delete_category(self, category_id: UUID) -> bool:
        return await self.update_category(category_id, {"is_active": False})
