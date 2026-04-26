"""Real PG round-trip — IngestionRunRepository (C1 / P1-3).

`recent_failure_streak` 의 실제 시맨틱이 PG 의 정렬·NULL·timezone 처리와
일치하는지 검증. PG 미가용 시 자동 skip.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from src.stores.postgres.models import IngestionRunModel
from src.stores.postgres.repositories.ingestion_run import (
    IngestionRunRepository,
)

pytestmark = pytest.mark.requires_postgres


async def _seed_run(
    pg_session_maker, *, kb_id: str, status: str, completed_at: datetime,
) -> str:
    import uuid
    rid = str(uuid.uuid4())
    async with pg_session_maker() as session:
        session.add(IngestionRunModel(
            id=rid, kb_id=kb_id,
            source_type="test", source_name="streak",
            status=status,
            started_at=completed_at - timedelta(minutes=1),
            completed_at=completed_at,
        ))
        await session.commit()
    return rid


async def _cleanup_kb(pg_session_maker, kb_id: str) -> None:
    async with pg_session_maker() as session:
        await session.execute(
            delete(IngestionRunModel).where(IngestionRunModel.kb_id == kb_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_recent_failure_streak_counts_consecutive_failed(
    pg_session_maker,
):
    """KB 의 가장 최근 → 과거 순 스캔, 첫 success 시 streak 종료."""
    repo = IngestionRunRepository(pg_session_maker)
    now = datetime.now(timezone.utc)
    kb_id = f"_test_streak_{now.timestamp():.0f}"

    # 시간순 (오래된 → 최근): success, failed, failed, failed
    # 따라서 최근 → 과거 순 스캔 시 failed 3 회 연속 → streak=3
    try:
        for i, status in enumerate(["completed", "failed", "failed", "failed"]):
            await _seed_run(
                pg_session_maker,
                kb_id=kb_id, status=status,
                completed_at=now - timedelta(hours=4 - i),
            )

        streaks = await repo.recent_failure_streak(window_hours=24)
        assert streaks.get(kb_id) == 3
    finally:
        await _cleanup_kb(pg_session_maker, kb_id)


@pytest.mark.asyncio
async def test_recent_failure_streak_breaks_on_success(pg_session_maker):
    """failed, failed, completed, failed → streak=1 (최근만)."""
    repo = IngestionRunRepository(pg_session_maker)
    now = datetime.now(timezone.utc)
    kb_id = f"_test_break_{now.timestamp():.0f}"

    try:
        for i, status in enumerate(
            ["failed", "failed", "completed", "failed"],
        ):
            await _seed_run(
                pg_session_maker,
                kb_id=kb_id, status=status,
                completed_at=now - timedelta(hours=4 - i),
            )

        streaks = await repo.recent_failure_streak(window_hours=24)
        # 가장 최근=failed, 그 다음=completed → streak 종료, count=1
        assert streaks.get(kb_id) == 1
    finally:
        await _cleanup_kb(pg_session_maker, kb_id)


@pytest.mark.asyncio
async def test_recent_failure_streak_window_hours_filters_old(
    pg_session_maker,
):
    """48시간 전 failed 는 window=24 에서 제외."""
    repo = IngestionRunRepository(pg_session_maker)
    now = datetime.now(timezone.utc)
    kb_id = f"_test_window_{now.timestamp():.0f}"

    try:
        # 48h 전 failed 1건
        await _seed_run(
            pg_session_maker, kb_id=kb_id, status="failed",
            completed_at=now - timedelta(hours=48),
        )
        # 23h 전 failed 1건
        await _seed_run(
            pg_session_maker, kb_id=kb_id, status="failed",
            completed_at=now - timedelta(hours=23),
        )

        # window=24 → 23h 전 1건만
        streaks_24 = await repo.recent_failure_streak(window_hours=24)
        assert streaks_24.get(kb_id) == 1
        # window=72 → 둘 다
        streaks_72 = await repo.recent_failure_streak(window_hours=72)
        assert streaks_72.get(kb_id) == 2
    finally:
        await _cleanup_kb(pg_session_maker, kb_id)
