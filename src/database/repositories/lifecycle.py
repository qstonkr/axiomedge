"""Document Lifecycle Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgDocumentLifecycleRepository.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import DocumentLifecycleModel, LifecycleTransitionModel
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class DocumentLifecycleRepository(BaseRepository):
    """PostgreSQL document lifecycle repository."""

    async def save(self, data: dict[str, Any]) -> None:
        transitions = data.pop("transitions", [])
        async with await self._get_session() as session:
            try:
                stmt = select(DocumentLifecycleModel).where(
                    DocumentLifecycleModel.document_id == data["document_id"],
                    DocumentLifecycleModel.kb_id == data["kb_id"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                    lifecycle_id = str(existing.id)
                else:
                    model = DocumentLifecycleModel(**data)
                    session.add(model)
                    await session.flush()
                    lifecycle_id = data["id"]

                # Append new transitions
                if transitions:
                    count_stmt = select(func.count(LifecycleTransitionModel.id)).where(
                        LifecycleTransitionModel.lifecycle_id == lifecycle_id,
                    )
                    count_result = await session.execute(count_stmt)
                    persisted_count = count_result.scalar_one() or 0

                    for t in transitions[persisted_count:]:
                        t_model = LifecycleTransitionModel(
                            id=str(uuid.uuid4()),
                            lifecycle_id=lifecycle_id,
                            **t,
                        )
                        session.add(t_model)

                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_document(self, document_id: str, kb_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(DocumentLifecycleModel).where(
                DocumentLifecycleModel.document_id == document_id,
                DocumentLifecycleModel.kb_id == kb_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if not model:
                return None
            transitions = await self._load_transitions(session, str(model.id))
            d = self._to_dict(model)
            d["transitions"] = transitions
            return d

    async def list_by_kb(self, kb_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(DocumentLifecycleModel)
                .where(DocumentLifecycleModel.kb_id == kb_id)
                .order_by(DocumentLifecycleModel.created_at.desc())
            )
            result = await session.execute(stmt)
            models = result.scalars().all()

            results = []
            for m in models:
                transitions = await self._load_transitions(session, str(m.id))
                d = self._to_dict(m)
                d["transitions"] = transitions
                results.append(d)
            return results

    async def list_by_status(self, kb_id: str, status: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(DocumentLifecycleModel)
                .where(
                    DocumentLifecycleModel.kb_id == kb_id,
                    DocumentLifecycleModel.status == status,
                )
                .order_by(DocumentLifecycleModel.created_at.desc())
            )
            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._to_dict(m) for m in models]

    async def _load_transitions(self, session: AsyncSession, lifecycle_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(LifecycleTransitionModel)
            .where(LifecycleTransitionModel.lifecycle_id == lifecycle_id)
            .order_by(LifecycleTransitionModel.transitioned_at)
        )
        result = await session.execute(stmt)
        return [
            {
                "from_status": m.from_status,
                "to_status": m.to_status,
                "transitioned_by": m.transitioned_by,
                "transitioned_at": m.transitioned_at,
                "reason": m.reason,
            }
            for m in result.scalars().all()
        ]

    @staticmethod
    def _to_dict(model: DocumentLifecycleModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "document_id": model.document_id,
            "kb_id": model.kb_id,
            "status": model.status,
            "previous_status": model.previous_status,
            "status_changed_at": model.status_changed_at,
            "status_changed_by": model.status_changed_by,
            "auto_archive_at": model.auto_archive_at,
            "deletion_scheduled_at": model.deletion_scheduled_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
