"""arq cron: ingestion failure Slack alert sweep (PR-6 E).

Run 30분 주기로 IngestionRunRepository.recent_failure_streak() 를 스캔하여
threshold 초과 KB 에 Slack 알림. Redis SET NX EX 기반 dedup 으로 같은 KB
중복 알림 방지 (default 2h).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def run_ingestion_alerts(
    *, run_repo: Any, failure_repo: Any, redis: Any,
) -> dict[str, int]:
    """Pure function — repo/redis 의존성 주입형. 테스트 가능성 높음."""
    from src.config import get_settings
    from src.notifications.slack import notify_ingestion_failure_streak

    n = get_settings().notifications

    streaks = await run_repo.recent_failure_streak(
        window_hours=n.ingestion_failure_window_hours,
    )
    fired = 0
    threshold = max(1, int(n.ingestion_failure_streak))
    dedup_ttl = max(60, int(n.ingestion_alert_dedup_minutes) * 60)

    for kb_id, count in streaks.items():
        if count < threshold:
            continue
        # Redis dedup — 같은 KB 의 알림이 dedup_ttl 초 내 중복 발사되지 않게.
        if redis is not None:
            key = f"alert:ingest:{kb_id}"
            try:
                ok = await redis.set(
                    key, str(int(time.time())), ex=dedup_ttl, nx=True,
                )
                if not ok:
                    continue
            except (RuntimeError, OSError, AttributeError) as e:
                logger.warning("Redis dedup check failed for %s: %s", kb_id, e)
                # 실패 시에도 알림은 발송 (cron 30분 자체가 약한 dedup)

        samples: list[dict[str, Any]] = []
        if failure_repo is not None:
            try:
                samples = await failure_repo.list_by_kb(
                    kb_id,
                    since_hours=n.ingestion_failure_window_hours,
                    limit=3,
                )
            except (RuntimeError, OSError, AttributeError) as e:
                logger.warning(
                    "Failed to fetch sample failures for %s: %s", kb_id, e,
                )

        await notify_ingestion_failure_streak(
            kb_id=kb_id, count=count, sample_failures=samples,
        )
        fired += 1
        logger.info(
            "Ingestion alert fired: kb=%s streak=%d", kb_id, count,
        )

    return {"fired": fired, "checked_kbs": len(streaks)}


async def ingestion_failure_alert_sweep(
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """arq entrypoint — DB / redis 를 ctx 또는 ENV 에서 해소."""
    try:
        from src.stores.postgres.session import get_knowledge_session_maker
        from src.stores.postgres.repositories.ingestion_run import (
            IngestionRunRepository,
        )
        from src.stores.postgres.repositories.ingestion_failures import (
            IngestionFailureRepository,
        )
    except ImportError as e:
        logger.warning("ingestion_failure_alert_sweep skipped: %s", e)
        return {"status": "skipped", "reason": str(e)}

    session_maker = get_knowledge_session_maker()
    if session_maker is None:
        logger.info("DATABASE_URL not set — skipping ingestion alert sweep")
        return {"status": "skipped", "reason": "no_database_url"}

    run_repo = IngestionRunRepository(session_maker)
    failure_repo = IngestionFailureRepository(session_maker)

    redis = ctx.get("redis") if isinstance(ctx, dict) else None

    return await run_ingestion_alerts(
        run_repo=run_repo, failure_repo=failure_repo, redis=redis,
    )


__all__ = ["ingestion_failure_alert_sweep", "run_ingestion_alerts"]
