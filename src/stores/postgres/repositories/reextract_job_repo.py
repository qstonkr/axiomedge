"""ReextractJobRepo — graph_schema_reextract_jobs lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import ReextractJobModel
from src.stores.postgres.repositories.base import BaseRepository


class ReextractJobRepo(BaseRepository):
    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)

    async def queue(
        self,
        *,
        kb_id: str,
        triggered_by_user: str,
        schema_version_from: int,
        schema_version_to: int,
    ) -> UUID:
        async with self._session_maker() as session:
            async with session.begin():
                job = ReextractJobModel(
                    kb_id=kb_id,
                    triggered_by_user=triggered_by_user,
                    schema_version_from=schema_version_from,
                    schema_version_to=schema_version_to,
                    status="queued",
                )
                session.add(job)
                await session.flush()
                return job.id

    async def start(self, job_id: UUID, *, docs_total: int) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        status="running",
                        docs_total=docs_total,
                        started_at=datetime.now(UTC),
                    ),
                )

    async def progress(
        self,
        job_id: UUID,
        *,
        docs_processed: int,
        docs_failed: int,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        docs_processed=docs_processed,
                        docs_failed=docs_failed,
                    ),
                )

    async def complete(
        self,
        job_id: UUID,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        status=status,
                        error_message=error_message,
                        completed_at=datetime.now(UTC),
                    ),
                )

    async def has_active(self, kb_id: str) -> bool:
        async with self._session_maker() as session:
            row = await session.scalar(
                select(ReextractJobModel.id).where(
                    ReextractJobModel.kb_id == kb_id,
                    ReextractJobModel.status.in_(["queued", "running"]),
                ).limit(1),
            )
            return row is not None


__all__ = ["ReextractJobRepo"]
