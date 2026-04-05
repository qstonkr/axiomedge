"""Ingestion Run Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgIngestionRunRepository.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.database.models import IngestionRunModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class IngestionRunRepository(BaseRepository):
    """PostgreSQL ingestion run repository."""

    async def create(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                # Serialize JSON fields
                if "errors" in data and isinstance(data["errors"], list):
                    data["errors"] = json.dumps(data["errors"][:10])
                if "metadata" in data and isinstance(data["metadata"], dict):
                    data["run_metadata"] = json.dumps(data.pop("metadata"))
                model = IngestionRunModel(**data)
                session.add(model)
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to create ingestion run: %s", e)
                raise

    async def complete(self, run_id: str, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                values = dict(data)
                if "errors" in values and isinstance(values["errors"], list):
                    values["errors"] = json.dumps(values["errors"][:10])
                if "metadata" in values and isinstance(values["metadata"], dict):
                    values["run_metadata"] = json.dumps(values.pop("metadata"))
                if "completed_at" not in values:
                    values["completed_at"] = datetime.now(timezone.utc)

                await session.execute(
                    update(IngestionRunModel)
                    .where(IngestionRunModel.id == run_id)
                    .values(**values)
                )
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to complete ingestion run: %s", e)
                raise

    async def get_by_id(self, run_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(IngestionRunModel).where(IngestionRunModel.id == run_id)
                )
                model = result.scalar_one_or_none()
                return self._to_dict(model) if model else None
            except SQLAlchemyError:
                return None

    async def list_by_kb(self, kb_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(IngestionRunModel)
                    .where(IngestionRunModel.kb_id == kb_id)
                    .order_by(IngestionRunModel.started_at.desc())
                    .limit(limit)
                )
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError:
                return []

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(IngestionRunModel)
                    .order_by(IngestionRunModel.started_at.desc())
                    .limit(limit)
                )
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError:
                return []

    @staticmethod
    def _to_dict(model: IngestionRunModel) -> dict[str, Any]:
        errors: list[str] = []
        if model.errors:
            try:
                errors = json.loads(model.errors)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("Failed to parse ingestion run errors JSON: %s", e)

        metadata: dict[str, Any] = {}
        if model.run_metadata:
            try:
                metadata = json.loads(model.run_metadata)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("Failed to parse ingestion run metadata JSON: %s", e)

        return {
            "id": model.id,
            "run_id": model.id,
            "kb_id": model.kb_id,
            "source_type": model.source_type,
            "source_name": model.source_name,
            "status": model.status or "running",
            "version_fingerprint": model.version_fingerprint,
            "documents_fetched": model.documents_fetched or 0,
            "documents_ingested": model.documents_ingested or 0,
            "documents_held": model.documents_held or 0,
            "documents_rejected": model.documents_rejected or 0,
            "chunks_stored": model.chunks_stored or 0,
            "chunks_deduped": model.chunks_deduped or 0,
            "errors": errors,
            "metadata": metadata,
            "started_at": model.started_at,
            "completed_at": model.completed_at,
            "created_at": model.created_at,
        }
