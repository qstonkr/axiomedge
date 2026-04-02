"""Document Ownership Repositories - PostgreSQL backed.

Provides DocumentOwner, TopicOwner, ErrorReport persistence.
Extracted from oreo-ecosystem pg_document_ownership_repository.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.database.models import (
    DocumentErrorReportModel,
    DocumentOwnerModel,
    TopicOwnerModel,
)
from src.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class DocumentOwnerRepository(BaseRepository):
    """PostgreSQL document owner repository."""

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                stmt = select(DocumentOwnerModel).where(
                    DocumentOwnerModel.document_id == data["document_id"],
                    DocumentOwnerModel.kb_id == data["kb_id"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    if "id" not in data or not data["id"]:
                        data["id"] = str(uuid.uuid4())
                    model = DocumentOwnerModel(**data)
                    session.add(model)

                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_document(self, document_id: str, kb_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(DocumentOwnerModel).where(
                DocumentOwnerModel.document_id == document_id,
                DocumentOwnerModel.kb_id == kb_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(DocumentOwnerModel)
                .where(DocumentOwnerModel.owner_user_id == owner_user_id)
                .order_by(DocumentOwnerModel.created_at.desc())
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_by_kb(self, kb_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(DocumentOwnerModel)
                .where(DocumentOwnerModel.kb_id == kb_id)
                .order_by(DocumentOwnerModel.document_id)
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def delete(self, document_id: str, kb_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(DocumentOwnerModel).where(
                    DocumentOwnerModel.document_id == document_id,
                    DocumentOwnerModel.kb_id == kb_id,
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
    def _to_dict(model: DocumentOwnerModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "document_id": model.document_id,
            "kb_id": model.kb_id,
            "owner_user_id": model.owner_user_id,
            "backup_owner_user_id": model.backup_owner_user_id,
            "ownership_type": model.ownership_type,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }


class TopicOwnerRepository(BaseRepository):
    """PostgreSQL topic owner repository."""

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                stmt = select(TopicOwnerModel).where(
                    TopicOwnerModel.kb_id == data["kb_id"],
                    TopicOwnerModel.topic_name == data["topic_name"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    if "topic_keywords" in data:
                        existing.topic_keywords = json.dumps(data["topic_keywords"])
                    if "sme_user_id" in data:
                        existing.sme_user_id = data["sme_user_id"]
                    if "escalation_chain" in data:
                        existing.escalation_chain = json.dumps(data["escalation_chain"])
                else:
                    model_data = dict(data)
                    if "id" not in model_data or not model_data["id"]:
                        model_data["id"] = str(uuid.uuid4())
                    if "topic_keywords" in model_data:
                        model_data["topic_keywords"] = json.dumps(model_data["topic_keywords"])
                    if "escalation_chain" in model_data:
                        model_data["escalation_chain"] = json.dumps(model_data["escalation_chain"])
                    model = TopicOwnerModel(**model_data)
                    session.add(model)

                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_kb(self, kb_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(TopicOwnerModel)
                .where(TopicOwnerModel.kb_id == kb_id)
                .order_by(TopicOwnerModel.topic_name)
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def delete(self, kb_id: str, topic_name: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(TopicOwnerModel).where(
                    TopicOwnerModel.kb_id == kb_id,
                    TopicOwnerModel.topic_name == topic_name,
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
    def _to_dict(model: TopicOwnerModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "kb_id": model.kb_id,
            "topic_name": model.topic_name,
            "topic_keywords": json.loads(model.topic_keywords) if model.topic_keywords else [],
            "sme_user_id": model.sme_user_id,
            "escalation_chain": json.loads(model.escalation_chain) if model.escalation_chain else [],
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }


class ErrorReportRepository(BaseRepository):
    """PostgreSQL error report repository."""

    async def save(self, data: dict[str, Any]) -> None:
        async with await self._get_session() as session:
            try:
                stmt = select(DocumentErrorReportModel).where(
                    DocumentErrorReportModel.id == data["id"],
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    model = DocumentErrorReportModel(**data)
                    session.add(model)

                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get_by_id(self, report_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(DocumentErrorReportModel).where(DocumentErrorReportModel.id == report_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_document(self, document_id: str, kb_id: str) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = (
                select(DocumentErrorReportModel)
                .where(
                    DocumentErrorReportModel.document_id == document_id,
                    DocumentErrorReportModel.kb_id == kb_id,
                )
                .order_by(DocumentErrorReportModel.created_at.desc())
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def get_open_reports(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            open_statuses = ["pending", "in_progress", "escalated"]
            stmt = select(DocumentErrorReportModel).where(
                DocumentErrorReportModel.status.in_(open_statuses),
            )
            if kb_id:
                stmt = stmt.where(DocumentErrorReportModel.kb_id == kb_id)
            stmt = stmt.order_by(DocumentErrorReportModel.created_at.desc())
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def delete(self, report_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(DocumentErrorReportModel).where(DocumentErrorReportModel.id == report_id)
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
    def _to_dict(model: DocumentErrorReportModel) -> dict[str, Any]:
        return {
            "id": str(model.id),
            "report_id": str(model.id),
            "document_id": model.document_id,
            "kb_id": model.kb_id,
            "error_type": model.error_type,
            "description": model.description,
            "reporter_user_id": model.reporter_user_id,
            "assigned_to": model.assigned_to,
            "status": model.status,
            "priority": model.priority,
            "resolution_note": model.resolution_note,
            "resolved_at": model.resolved_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
