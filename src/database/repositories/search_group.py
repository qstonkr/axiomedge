"""KB Search Group Repository.

KB 검색 그룹 CRUD 및 조회.
사용자가 BU/팀 단위로 KB를 그룹화하여 스코프 검색 가능.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update

from src.database.models import KBSearchGroupModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class SearchGroupRepository(BaseRepository):
    """KB Search Group repository."""

    async def create(
        self,
        name: str,
        kb_ids: list[str],
        description: str = "",
        is_default: bool = False,
        created_by: str = "",
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            model = KBSearchGroupModel(
                name=name,
                description=description,
                kb_ids=kb_ids,
                is_default=is_default,
                created_by=created_by,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return self._to_dict(model)

    async def get(self, group_id: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(KBSearchGroupModel).where(
                KBSearchGroupModel.id == UUID(group_id)
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(KBSearchGroupModel).where(
                KBSearchGroupModel.name == name
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_default(self) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(KBSearchGroupModel).where(
                KBSearchGroupModel.is_default.is_(True)
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(KBSearchGroupModel).order_by(
                KBSearchGroupModel.is_default.desc(),
                KBSearchGroupModel.name,
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def update(
        self,
        group_id: str,
        name: str | None = None,
        kb_ids: list[str] | None = None,
        description: str | None = None,
        is_default: bool | None = None,
    ) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            values: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
            if name is not None:
                values["name"] = name
            if kb_ids is not None:
                values["kb_ids"] = kb_ids
            if description is not None:
                values["description"] = description
            if is_default is not None:
                values["is_default"] = is_default
                # 다른 그룹의 default를 해제
                if is_default:
                    await session.execute(
                        update(KBSearchGroupModel)
                        .where(KBSearchGroupModel.id != UUID(group_id))
                        .values(is_default=False)
                    )

            stmt = (
                update(KBSearchGroupModel)
                .where(KBSearchGroupModel.id == UUID(group_id))
                .values(**values)
                .returning(KBSearchGroupModel)
            )
            result = await session.execute(stmt)
            await session.commit()
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def delete(self, group_id: str) -> bool:
        async with self._session_maker() as session:
            stmt = delete(KBSearchGroupModel).where(
                KBSearchGroupModel.id == UUID(group_id)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def resolve_kb_ids(self, group_id: str | None = None, group_name: str | None = None) -> list[str]:
        """Resolve a group to its KB IDs.

        If neither group_id nor group_name is provided, returns default group's KBs.
        If no default group exists, returns empty list (search all).
        """
        if group_id:
            group = await self.get(group_id)
        elif group_name:
            group = await self.get_by_name(group_name)
        else:
            group = await self.get_default()

        if group:
            return group.get("kb_ids", [])
        return []

    @staticmethod
    def _to_dict(model: KBSearchGroupModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "name": model.name,
            "description": model.description or "",
            "kb_ids": model.kb_ids or [],
            "is_default": model.is_default,
            "created_by": model.created_by or "",
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
