"""BootstrapRunRepo — manage graph_schema_bootstrap_runs lifecycle.

Spec §6.5 (concurrent safety).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import BootstrapRunModel
from src.stores.postgres.repositories.base import BaseRepository


class BootstrapRunRepo(BaseRepository):
    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)

    async def create(
        self,
        *,
        kb_id: str,
        triggered_by: str,
        sample_size: int,
        sample_strategy: str,
        triggered_by_user: str | None = None,
    ) -> UUID:
        async with self._session_maker() as session:
            async with session.begin():
                run = BootstrapRunModel(
                    kb_id=kb_id,
                    triggered_by=triggered_by,
                    sample_size=sample_size,
                    sample_strategy=sample_strategy,
                    triggered_by_user=triggered_by_user,
                    status="running",
                )
                session.add(run)
                await session.flush()
                return run.id

    async def complete(
        self,
        run_id: UUID,
        *,
        status: str,
        docs_scanned: int = 0,
        candidates_found: int = 0,
        llm_calls: int = 0,
        error_message: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(BootstrapRunModel).where(
                        BootstrapRunModel.id == run_id,
                    ).values(
                        status=status,
                        docs_scanned=docs_scanned,
                        candidates_found=candidates_found,
                        llm_calls=llm_calls,
                        error_message=error_message,
                        completed_at=datetime.now(UTC),
                    ),
                )

    async def has_running(self, kb_id: str) -> bool:
        """Return True iff there's a non-stale 'running' row for this KB.

        Stale = started_at older than 1h (assumes crashed worker).
        """
        threshold = datetime.now(UTC) - timedelta(hours=1)
        async with self._session_maker() as session:
            row = await session.scalar(
                select(BootstrapRunModel.id).where(
                    BootstrapRunModel.kb_id == kb_id,
                    BootstrapRunModel.status == "running",
                    BootstrapRunModel.started_at > threshold,
                ).limit(1),
            )
            return row is not None

    async def cleanup_stale(self) -> int:
        """Mark stale 'running' rows (>1h) as 'failed'. Run daily."""
        threshold = datetime.now(UTC) - timedelta(hours=1)
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(BootstrapRunModel).where(
                        BootstrapRunModel.status == "running",
                        BootstrapRunModel.started_at <= threshold,
                    ).values(
                        status="failed",
                        error_message="stale — exceeded 1h timeout",
                        completed_at=datetime.now(UTC),
                    ),
                )
                return result.rowcount or 0

    async def recent_failure_streak(
        self, *, window_hours: int = 24,
    ) -> dict[str, int]:
        """Return {kb_id: consecutive_recent_failures} for the given window.

        Counts only the trailing run of failures — a success anywhere
        in the window resets that KB's streak to 0. Rows without a
        ``completed_at`` are ignored.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        async with self._session_maker() as session:
            stmt = select(
                BootstrapRunModel.kb_id,
                BootstrapRunModel.status,
                BootstrapRunModel.completed_at,
            ).where(
                BootstrapRunModel.completed_at.is_not(None),
                BootstrapRunModel.completed_at >= cutoff,
            ).order_by(
                BootstrapRunModel.kb_id,
                BootstrapRunModel.completed_at.desc(),
            )
            rows = (await session.execute(stmt)).all()

        streak: dict[str, int] = {}
        closed: set[str] = set()
        for kb_id, status, _ in rows:
            if kb_id in closed:
                continue
            if status == "failed":
                streak[kb_id] = streak.get(kb_id, 0) + 1
            else:
                # First non-failure row (most recent → older) closes the streak.
                streak.setdefault(kb_id, 0)
                closed.add(kb_id)
        return streak


__all__ = ["BootstrapRunRepo"]
