# pyright: reportAttributeAccessIssue=false
"""Data Source Repository - PostgreSQL backed.

"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import DataSourceModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_json_loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


class DataSourceRepository(BaseRepository):
    """Async PostgreSQL repository for data source registry."""

    async def register(self, data: dict[str, Any]) -> dict[str, Any]:
        async with await self._get_session() as session:
            try:
                model_data = dict(data)
                for field in ("crawl_config", "pipeline_config", "last_sync_result"):
                    if field in model_data and isinstance(model_data[field], dict):
                        model_data[field] = json.dumps(model_data[field])
                if "metadata" in model_data:
                    model_data["metadata_"] = json.dumps(model_data.pop("metadata"))
                model = DataSourceModel(**model_data)
                session.add(model)
                await session.commit()
                return data
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get(self, source_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(DataSourceModel).where(DataSourceModel.id == source_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(DataSourceModel).where(DataSourceModel.name == name)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def list(
        self,
        source_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = select(DataSourceModel)
            if source_type:
                stmt = stmt.where(DataSourceModel.source_type == source_type)
            if status:
                stmt = stmt.where(DataSourceModel.status == status)
            stmt = stmt.order_by(DataSourceModel.created_at.desc()).limit(1000)
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def update_status(self, source_id: str, status: str, error_message: str | None = None) -> None:
        async with await self._get_session() as session:
            try:
                stmt = (
                    update(DataSourceModel)
                    .where(DataSourceModel.id == source_id)
                    .values(status=status, error_message=error_message, updated_at=_utc_now())
                )
                await session.execute(stmt)
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def complete_sync(
        self,
        source_id: str,
        status: str,
        sync_result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update data source after sync completion (success or failure)."""
        async with await self._get_session() as session:
            try:
                now = _utc_now()
                values: dict[str, Any] = {
                    "status": status,
                    "error_message": error_message,
                    "updated_at": now,
                    "last_sync_at": now,
                }
                if sync_result is not None:
                    values["last_sync_result"] = json.dumps(sync_result)
                stmt = (
                    update(DataSourceModel)
                    .where(DataSourceModel.id == source_id)
                    .values(**values)
                )
                await session.execute(stmt)
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def delete(self, source_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = delete(DataSourceModel).where(DataSourceModel.id == source_id)
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    @staticmethod
    def _to_dict(model: DataSourceModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "name": model.name,
            "source_type": model.source_type,
            "kb_id": model.kb_id,
            "crawl_config": _safe_json_loads(model.crawl_config),
            "pipeline_config": _safe_json_loads(model.pipeline_config),
            "schedule": model.schedule,
            "status": model.status,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "last_sync_at": model.last_sync_at,
            "last_sync_result": _safe_json_loads(model.last_sync_result),
            "error_message": model.error_message,
            "metadata": _safe_json_loads(model.metadata_),
        }
