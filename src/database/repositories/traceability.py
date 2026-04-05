"""Provenance / Traceability Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgProvenanceRepository.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.database.models import ProvenanceModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ProvenanceRepository(BaseRepository):
    """PostgreSQL provenance repository."""

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                model_data = dict(data)
                if "extraction_metadata" in model_data and isinstance(model_data["extraction_metadata"], dict):
                    model_data["extraction_metadata"] = json.dumps(model_data["extraction_metadata"])
                if "contributors" in model_data and isinstance(model_data["contributors"], list):
                    model_data["contributors"] = json.dumps(model_data["contributors"])
                if "id" not in model_data:
                    model_data["id"] = str(uuid.uuid4())
                model = ProvenanceModel(**model_data)
                session.add(model)
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save provenance: %s", e)
                raise

    async def upsert(self, data: dict[str, Any]) -> str | None:
        """Upsert provenance. Returns previous content_hash if existed."""
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(ProvenanceModel).where(
                        ProvenanceModel.knowledge_id == data["knowledge_id"],
                        ProvenanceModel.kb_id == data["kb_id"],
                    )
                )
                existing = result.scalar_one_or_none()
                previous_hash: str | None = None

                if existing:
                    previous_hash = existing.content_hash
                    for key, value in data.items():
                        if key in ("extraction_metadata",) and isinstance(value, dict):
                            value = json.dumps(value)
                        if key in ("contributors",) and isinstance(value, list):
                            value = json.dumps(value)
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    model_data = dict(data)
                    if "extraction_metadata" in model_data and isinstance(model_data["extraction_metadata"], dict):
                        model_data["extraction_metadata"] = json.dumps(model_data["extraction_metadata"])
                    if "contributors" in model_data and isinstance(model_data["contributors"], list):
                        model_data["contributors"] = json.dumps(model_data["contributors"])
                    if "id" not in model_data:
                        model_data["id"] = str(uuid.uuid4())
                    model = ProvenanceModel(**model_data)
                    session.add(model)

                await session.commit()
                return previous_hash
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_knowledge_id(self, knowledge_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            result = await session.execute(
                select(ProvenanceModel).where(ProvenanceModel.knowledge_id == knowledge_id)
            )
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_knowledge_and_kb(self, knowledge_id: str, kb_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            result = await session.execute(
                select(ProvenanceModel).where(
                    ProvenanceModel.knowledge_id == knowledge_id,
                    ProvenanceModel.kb_id == kb_id,
                )
            )
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_source(self, source_type: str, source_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            result = await session.execute(
                select(ProvenanceModel)
                .where(
                    ProvenanceModel.source_type == source_type,
                    ProvenanceModel.source_id == source_id,
                )
                .limit(1000)
            )
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_by_run_id(self, ingestion_run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            result = await session.execute(
                select(ProvenanceModel)
                .where(ProvenanceModel.ingestion_run_id == ingestion_run_id)
                .limit(limit)
            )
            return [self._to_dict(m) for m in result.scalars().all()]

    @staticmethod
    def _to_dict(model: ProvenanceModel) -> dict[str, Any]:
        extraction_meta = None
        if model.extraction_metadata:
            try:
                extraction_meta = json.loads(model.extraction_metadata)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("Failed to parse extraction_metadata JSON: %s", e)

        contributors: list[str] = []
        if model.contributors:
            try:
                contributors = json.loads(model.contributors)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("Failed to parse contributors JSON: %s", e)

        return {
            "id": model.id,
            "knowledge_id": model.knowledge_id,
            "kb_id": model.kb_id,
            "ingestion_run_id": model.ingestion_run_id,
            "source_type": model.source_type,
            "source_url": model.source_url,
            "source_id": model.source_id,
            "source_system": model.source_system,
            "crawled_at": model.crawled_at,
            "crawled_by": model.crawled_by,
            "extraction_metadata": extraction_meta,
            "original_author": model.original_author,
            "original_created_at": model.original_created_at,
            "original_modified_at": model.original_modified_at,
            "contributors": contributors,
            "verification_status": model.verification_status,
            "quality_score": model.quality_score,
            "content_hash": model.content_hash,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
