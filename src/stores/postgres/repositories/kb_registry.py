# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""KB Registry Repository - PostgreSQL backed.

Manages KB configuration persistence with CRUD operations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.stores.postgres.models import KBConfigModel, RegistryBase

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


class KBRegistryRepository:
    """PostgreSQL KB registry repository.

    Provides persistent KB configuration storage with:
    - ACID transaction guarantees
    - Efficient indexing for tier/organization queries
    - Connection pooling for performance
    """

    def __init__(
        self,
        database_url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        echo: bool = False,
    ) -> None:
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.database_url = database_url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self.echo = echo
        self._engine = None
        self._session_maker: async_sessionmaker | None = None

    async def initialize(self) -> None:
        """Initialize async engine and session maker."""
        self._engine = create_async_engine(
            self.database_url,
            echo=self.echo,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_pre_ping=True,
        )

        self._session_maker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with self._engine.begin() as conn:
            await conn.run_sync(RegistryBase.metadata.create_all)

        logger.info("PostgreSQL KB registry initialized")

    async def shutdown(self) -> None:
        if self._engine:
            await self._engine.dispose()
            logger.info("PostgreSQL KB registry shut down")

    @property
    def session_maker(self) -> async_sessionmaker | None:
        return self._session_maker

    async def _get_session(self) -> AsyncSession:
        if not self._session_maker:
            raise RuntimeError("Repository not initialized")
        await asyncio.sleep(0)
        return self._session_maker()

    async def create_kb(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new KB configuration."""
        async with await self._get_session() as session:
            try:
                model = KBConfigModel(**data)
                session.add(model)
                await session.commit()
                logger.info("Created KB: %s (id=%s)", data.get("name"), data.get("id"))
                return data
            except IntegrityError as e:
                await session.rollback()
                raise ValueError(f"KB already exists: {e}") from e
            except SQLAlchemyError as e:
                await session.rollback()
                raise RuntimeError(f"Database error: {e}") from e

    async def get_kb(
        self, kb_id: str, organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get KB by ID, optionally scoped to an organization.

        ``organization_id`` is the multi-tenant gate (B-0 Day 3): when set,
        rows from other orgs return None (the route maps that to a 404 so
        the existence of foreign KBs is not leaked). System code (background
        sync, health checks) may pass None to read across orgs — use sparingly.
        """
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                return self._model_to_dict(model) if model else None
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def get_kb_by_name(
        self, name: str, organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get KB by name, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.name == name)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                return self._model_to_dict(model) if model else None
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def update_kb(
        self, kb_id: str, data: dict[str, Any],
        organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Update KB configuration, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return None

                for key, value in data.items():
                    if hasattr(model, key):
                        setattr(model, key, value)
                model.updated_at = _utc_now()

                await session.commit()
                logger.info("Updated KB: %s", kb_id)
                return self._model_to_dict(model)
            except SQLAlchemyError as e:
                await session.rollback()
                raise RuntimeError(f"Database error: {e}") from e

    async def delete_kb(
        self, kb_id: str, organization_id: str | None = None,
    ) -> bool:
        """Delete KB configuration, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False

                await session.delete(model)
                await session.commit()
                logger.info("Deleted KB: %s", kb_id)
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                raise RuntimeError(f"Database error: {e}") from e

    async def list_all(
        self, limit: int = 100, offset: int = 0,
        organization_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List KB configurations, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                stmt = (
                    stmt.order_by(KBConfigModel.tier, KBConfigModel.name)
                    .limit(limit)
                    .offset(offset)
                )
                result = await session.execute(stmt)
                models = result.scalars().all()
                return [self._model_to_dict(m) for m in models]
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def list_by_tier(
        self, tier: str, organization_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List KB by tier, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.tier == tier)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                stmt = stmt.order_by(KBConfigModel.name).limit(500)
                result = await session.execute(stmt)
                models = result.scalars().all()
                return [self._model_to_dict(m) for m in models]
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def list_by_status(
        self, status: str, limit: int = 500,
        organization_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List KB by status, optionally scoped to an organization."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.status == status)
                if organization_id is not None:
                    stmt = stmt.where(KBConfigModel.organization_id == organization_id)
                stmt = stmt.order_by(KBConfigModel.name).limit(limit)
                result = await session.execute(stmt)
                models = result.scalars().all()
                return [self._model_to_dict(m) for m in models]
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def count(self, tier: str | None = None) -> int:
        """Count KBs."""
        async with await self._get_session() as session:
            try:
                stmt = select(func.count()).select_from(KBConfigModel)
                if tier:
                    stmt = stmt.where(KBConfigModel.tier == tier)
                result = await session.execute(stmt)
                return result.scalar() or 0
            except SQLAlchemyError as e:
                raise RuntimeError(f"Database error: {e}") from e

    async def update_counts(self, kb_id: str, documents_added: int, chunks_added: int) -> bool:
        """Increment document/chunk counts after ingestion."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False

                model.document_count = (model.document_count or 0) + documents_added
                model.chunk_count = (model.chunk_count or 0) + chunks_added
                model.last_ingested_at = _utc_now()
                model.updated_at = _utc_now()
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning("Failed to update counts for %s: %s", kb_id, e)
                return False

    async def sync_counts_from_qdrant(self, kb_id: str, chunk_count: int) -> bool:
        """Sync chunk count from Qdrant (overwrite, not increment)."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False

                model.chunk_count = chunk_count
                model.updated_at = _utc_now()
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning("Failed to sync counts for %s: %s", kb_id, e)
                return False

    async def mark_synced(self, kb_id: str) -> bool:
        """Mark KB as synced."""
        async with await self._get_session() as session:
            try:
                stmt = select(KBConfigModel).where(KBConfigModel.id == kb_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False

                model.last_synced_at = _utc_now()
                model.status = "active"
                model.updated_at = _utc_now()
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                raise RuntimeError(f"Database error: {e}") from e

    async def health_check(self) -> bool:
        try:
            async with await self._get_session() as session:
                await session.execute(select(func.count()).select_from(KBConfigModel))
                return True
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return False

    @staticmethod
    def _model_to_dict(model: KBConfigModel) -> dict[str, Any]:
        return {
            "kb_id": str(model.id),
            "id": str(model.id),
            "name": model.name,
            "description": model.description,
            "tier": model.tier,
            "parent_kb_id": str(model.parent_kb_id) if model.parent_kb_id else None,
            "organization_id": model.organization_id,
            "department_id": getattr(model, "department_id", None),
            "owner_id": model.owner_id,
            "data_classification": getattr(model, "data_classification", "internal"),
            "dataset_id": model.dataset_id,
            "dataset_ids_by_env": model.dataset_ids_by_env or {},
            "storage_backend": getattr(model, "storage_backend", "qdrant"),
            "sync_sources": model.sync_sources or [],
            "sync_schedule": model.sync_schedule,
            "last_synced_at": _to_aware_utc(model.last_synced_at),
            "status": model.status,
            "settings": model.settings or {},
            "created_at": _to_aware_utc(model.created_at),
            "updated_at": _to_aware_utc(model.updated_at),
            "document_count": getattr(model, "document_count", 0) or 0,
            "chunk_count": getattr(model, "chunk_count", 0) or 0,
            "last_ingested_at": _to_aware_utc(getattr(model, "last_ingested_at", None)),
        }
