# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""FeatureFlag repository — PR-11 (N).

scope precedence 는 ``src.core.feature_flags.get_flag`` 에서 처리.
본 repo 는 단일 (name, scope) 행의 CRUD 만 담당.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import FeatureFlagModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class FeatureFlagRepository(BaseRepository):
    async def get(
        self, *, name: str, scope: str = "_global",
    ) -> dict[str, Any] | None:
        """Return single flag row as dict or None."""
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(FeatureFlagModel).where(
                        FeatureFlagModel.name == name,
                        FeatureFlagModel.scope == scope,
                    )
                )
                model = result.scalar_one_or_none()
                if model is None:
                    return None
                return self._to_dict(model)
            except SQLAlchemyError as e:
                logger.warning(
                    "FeatureFlag get failed (%s/%s): %s", name, scope, e,
                )
                return None

    async def list_all(self) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            try:
                result = await session.execute(select(FeatureFlagModel))
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError as e:
                logger.warning("FeatureFlag list_all failed: %s", e)
                return []

    async def upsert(
        self, *, name: str, scope: str = "_global",
        enabled: bool, payload: dict[str, Any] | None = None,
        updated_by: str | None = None,
    ) -> bool:
        """Atomic INSERT … ON CONFLICT DO UPDATE (PG dialect)."""
        async with await self._get_session() as session:
            try:
                stmt = pg_insert(FeatureFlagModel).values(
                    name=name, scope=scope, enabled=enabled,
                    payload=json.dumps(payload or {}),
                    updated_at=datetime.now(timezone.utc),
                    updated_by=(updated_by or "")[:100],
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["name", "scope"],
                    set_={
                        "enabled": stmt.excluded.enabled,
                        "payload": stmt.excluded.payload,
                        "updated_at": stmt.excluded.updated_at,
                        "updated_by": stmt.excluded.updated_by,
                    },
                )
                await session.execute(stmt)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning(
                    "FeatureFlag upsert failed (%s/%s): %s", name, scope, e,
                )
                return False

    async def delete_one(self, *, name: str, scope: str) -> int:
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    delete(FeatureFlagModel).where(
                        FeatureFlagModel.name == name,
                        FeatureFlagModel.scope == scope,
                    )
                )
                await session.commit()
                return int(result.rowcount or 0)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning(
                    "FeatureFlag delete failed (%s/%s): %s", name, scope, e,
                )
                return 0

    @staticmethod
    def _to_dict(model: FeatureFlagModel) -> dict[str, Any]:
        try:
            payload = json.loads(model.payload) if model.payload else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return {
            "name": model.name,
            "scope": model.scope,
            "enabled": bool(model.enabled),
            "payload": payload,
            "updated_at": model.updated_at,
            "updated_by": model.updated_by,
        }
