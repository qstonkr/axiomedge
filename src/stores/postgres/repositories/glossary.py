"""Glossary Repository - PostgreSQL backed.

Extracted from oreo-ecosystem PgGlossaryRepository.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import GlossaryTermModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GlossaryRepository(BaseRepository):
    """Async SQLAlchemy glossary repository."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)
        self._search_available = True

    def _scope_filter(self, kb_id: str) -> Any:
        if kb_id and kb_id.lower() != "all":
            return or_(
                GlossaryTermModel.scope == "global",
                GlossaryTermModel.kb_id == kb_id,
            )
        return None

    @staticmethod
    def _json_list_fields() -> tuple[str, ...]:
        return ("synonyms", "abbreviations", "related_terms", "source_kb_ids")

    @classmethod
    def _update_existing_term(
        cls, existing: GlossaryTermModel, term_data: dict[str, Any], now: datetime,
    ) -> None:
        """Apply term_data fields to an existing model instance."""
        for key, value in term_data.items():
            if key in cls._json_list_fields():
                setattr(existing, key, json.dumps(value if isinstance(value, list) else []))
            elif hasattr(existing, key):
                setattr(existing, key, value)
        existing.updated_at = now

    @classmethod
    def _build_new_term(
        cls, term_data: dict[str, Any], now: datetime,
    ) -> GlossaryTermModel:
        """Create a new GlossaryTermModel from term_data."""
        model_data = dict(term_data)
        for field in cls._json_list_fields():
            if field in model_data:
                val = model_data[field]
                model_data[field] = json.dumps(val if isinstance(val, list) else [])
        model_data.setdefault("created_at", now)
        model_data.setdefault("updated_at", now)
        return GlossaryTermModel(**model_data)

    async def save(self, term_data: dict[str, Any]) -> None:
        """Save a term (upsert by kb_id + term, case-insensitive)."""
        async with await self._get_session() as session:
            try:
                stmt = select(GlossaryTermModel).where(
                    and_(
                        GlossaryTermModel.kb_id == term_data["kb_id"],
                        func.lower(GlossaryTermModel.term) == term_data["term"].lower(),
                    )
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                now = _utc_now()

                if existing:
                    self._update_existing_term(existing, term_data, now)
                else:
                    session.add(self._build_new_term(term_data, now))

                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Database error saving glossary term", extra={"error": str(e)})
                raise

    @staticmethod
    def _prepare_batch_row(item: dict[str, Any], columns: list[str], now) -> tuple:
        """Normalize a single glossary item into a tuple for bulk insert."""
        row = dict(item)
        for field in ("synonyms", "abbreviations", "related_terms", "source_kb_ids"):
            val = row.get(field)
            if val:
                row[field] = json.dumps(val if isinstance(val, list) else [])
            else:
                row[field] = "[]"
        row.setdefault("related_terms", "[]")
        row.setdefault("source_kb_ids", "[]")
        row.setdefault("confidence_score", 0)
        row.setdefault("occurrence_count", 0)
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        for k in columns:
            row.setdefault(k, None)
        return tuple(row.get(c) for c in columns)

    async def save_batch(self, items: list[dict[str, Any]]) -> int:
        """Bulk INSERT glossary terms via raw asyncpg connection.

        Bypasses SQLAlchemy ORM entirely for maximum performance.
        ~78K rows in seconds.
        """
        if not items:
            return 0

        now = _utc_now()
        columns = [
            "id", "kb_id", "term", "term_ko", "definition",
            "synonyms", "abbreviations", "related_terms", "source_kb_ids",
            "source", "status", "term_type", "scope",
            "physical_meaning", "composition_info", "domain_name",
            "confidence_score", "occurrence_count",
            "created_at", "updated_at",
        ]

        tuples = [self._prepare_batch_row(item, columns, now) for item in items]

        if not tuples:
            return 0

        col_names = ", ".join(columns)
        placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
        sql = f"INSERT INTO glossary_terms ({col_names}) VALUES ({placeholders})"

        # Direct asyncpg connection (bypass SQLAlchemy for bulk performance)
        import asyncpg
        import os

        # Get URL from session factory's bind if available
        bind = self._session_maker.kw.get("bind")
        if bind:
            pg_url = str(bind.url).replace("postgresql+asyncpg://", "postgresql://")
        else:
            from src.stores.postgres.init_db import DEFAULT_DATABASE_URL
            db_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
            pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        try:
            conn = await asyncpg.connect(pg_url)
            try:
                await conn.executemany(sql, tuples)
                return len(tuples)
            finally:
                await conn.close()
        except Exception as e:
            logger.error("Batch insert failed: %s", e)
            raise

    async def get_by_id(self, term_id: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            stmt = select(GlossaryTermModel).where(GlossaryTermModel.id == term_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._model_to_dict(model) if model else None

    async def get_by_term(self, kb_id: str, term: str) -> dict[str, Any] | None:
        async with await self._get_session() as session:
            conditions = [func.lower(GlossaryTermModel.term) == term.lower()]
            scope_cond = self._scope_filter(kb_id)
            if scope_cond is not None:
                conditions.append(scope_cond)
            stmt = (
                select(GlossaryTermModel)
                .where(and_(*conditions))
                .order_by(GlossaryTermModel.scope.asc())
                .limit(1)
            )
            result = await session.execute(stmt)
            model = result.scalars().first()
            return self._model_to_dict(model) if model else None

    async def list_by_kb(
        self,
        kb_id: str,
        status: str | None = None,
        source: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        offset: int = 0,
        term_type: str | None = None,
    ) -> list[dict[str, Any]]:
        async with await self._get_session() as session:
            stmt = select(GlossaryTermModel)

            if scope is not None:
                stmt = stmt.where(GlossaryTermModel.scope == scope)
                if scope != "global" and kb_id and kb_id.lower() != "all":
                    stmt = stmt.where(GlossaryTermModel.kb_id == kb_id)
            else:
                scope_cond = self._scope_filter(kb_id)
                if scope_cond is not None:
                    stmt = stmt.where(scope_cond)

            if status is not None:
                stmt = stmt.where(GlossaryTermModel.status == status)
            if source is not None:
                stmt = stmt.where(GlossaryTermModel.source == source)
            if term_type is not None:
                stmt = stmt.where(GlossaryTermModel.term_type == term_type)

            stmt = stmt.order_by(GlossaryTermModel.term).limit(limit).offset(offset)
            result = await session.execute(stmt)
            models = result.scalars().all()
            return [self._model_to_dict(m) for m in models]

    async def count_by_kb(
        self,
        kb_id: str,
        status: str | None = None,
        scope: str | None = None,
        term_type: str | None = None,
    ) -> int:
        async with await self._get_session() as session:
            stmt = select(func.count()).select_from(GlossaryTermModel)

            if scope is not None:
                stmt = stmt.where(GlossaryTermModel.scope == scope)
                if scope != "global" and kb_id and kb_id.lower() != "all":
                    stmt = stmt.where(GlossaryTermModel.kb_id == kb_id)
            else:
                scope_cond = self._scope_filter(kb_id)
                if scope_cond is not None:
                    stmt = stmt.where(scope_cond)

            if status is not None:
                stmt = stmt.where(GlossaryTermModel.status == status)
            if term_type is not None:
                stmt = stmt.where(GlossaryTermModel.term_type == term_type)

            result = await session.execute(stmt)
            return result.scalar() or 0

    async def search(self, kb_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_lower = query.lower().strip()
        if not query_lower or not self._search_available:
            return []

        async with await self._get_session() as session:
            try:
                conditions = [
                    GlossaryTermModel.status == "approved",
                    or_(
                        func.lower(GlossaryTermModel.term).contains(query_lower),
                        func.lower(func.coalesce(GlossaryTermModel.term_ko, "")).contains(query_lower),
                        func.lower(func.coalesce(GlossaryTermModel.physical_meaning, "")).contains(query_lower),
                        func.lower(GlossaryTermModel.synonyms).contains(query_lower),
                        func.lower(GlossaryTermModel.abbreviations).contains(query_lower),
                    ),
                ]
                scope_cond = self._scope_filter(kb_id)
                if scope_cond is not None:
                    conditions.insert(0, scope_cond)

                stmt = (
                    select(GlossaryTermModel)
                    .where(and_(*conditions))
                    .order_by(GlossaryTermModel.term)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                models = result.scalars().all()
                return [self._model_to_dict(m) for m in models]
            except SQLAlchemyError:
                return []

    async def delete(self, term_id: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = select(GlossaryTermModel).where(GlossaryTermModel.id == term_id)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False

                await session.delete(model)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Database error deleting glossary term", extra={"error": str(e)})
                raise

    async def bulk_delete(self, term_ids: list[str]) -> int:
        if not term_ids:
            return 0

        async with await self._get_session() as session:
            try:
                stmt = delete(GlossaryTermModel).where(GlossaryTermModel.id.in_(term_ids))
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount
            except SQLAlchemyError:
                await session.rollback()
                raise

    @staticmethod
    def _model_to_dict(model: GlossaryTermModel) -> dict[str, Any]:
        def _load_json_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return value
            try:
                return json.loads(value)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
                return []

        return {
            "id": str(model.id),
            "term_id": str(model.id),
            "kb_id": model.kb_id,
            "term": model.term,
            "term_ko": model.term_ko,
            "definition": model.definition,
            "synonyms": _load_json_list(model.synonyms),
            "abbreviations": _load_json_list(model.abbreviations),
            "related_terms": _load_json_list(model.related_terms),
            "source": model.source,
            "confidence_score": model.confidence_score,
            "status": model.status,
            "occurrence_count": model.occurrence_count or 0,
            "category": getattr(model, "category", None),
            "created_by": getattr(model, "created_by", None),
            "approved_by": getattr(model, "approved_by", None),
            "approved_at": getattr(model, "approved_at", None),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "scope": getattr(model, "scope", "kb"),
            "source_kb_ids": _load_json_list(getattr(model, "source_kb_ids", None)),
            "physical_meaning": getattr(model, "physical_meaning", None),
            "composition_info": getattr(model, "composition_info", None),
            "domain_name": getattr(model, "domain_name", None),
            "term_type": getattr(model, "term_type", "term"),
        }
