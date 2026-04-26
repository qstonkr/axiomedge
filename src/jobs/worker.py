"""Arq worker entry point.

Run:
    uv run arq src.jobs.worker.WorkerSettings

Or via Makefile:
    make worker

Settings drive concurrency, retry policy, redis connection.
"""

from __future__ import annotations

import asyncio
import logging
import os

from arq.cron import cron

from src.core.logging import configure_logging
from src.jobs.audit_log_archive import audit_log_archive_sweep
from src.jobs.distill_jobs import distill_sweep_post_train, distill_sweep_training
from src.jobs.ingestion_alerts import ingestion_failure_alert_sweep
from src.jobs.queue import redis_settings_from_env
from src.jobs.schema_alerts import schema_alerts_sweep
from src.jobs.schema_bootstrap_jobs import schema_bootstrap_cleanup
from src.jobs.tasks import REGISTERED_TASKS
from src.jobs.upload_jobs import cleanup_orphan_uploads

# P1-W2 — JSON 로그 + trace_id ContextVar 통일.
configure_logging(service="axiomedge-worker")
logger = logging.getLogger(__name__)


class WorkerSettings:
    """Arq worker configuration (loaded by ``arq`` CLI)."""

    functions = REGISTERED_TASKS
    redis_settings = redis_settings_from_env()

    # Concurrency: how many jobs run simultaneously per worker process
    max_jobs = int(os.getenv("ARQ_MAX_JOBS", "10"))

    # Retry policy
    max_tries = int(os.getenv("ARQ_MAX_TRIES", "3"))
    job_timeout = int(os.getenv("ARQ_JOB_TIMEOUT_SECONDS", "300"))  # 5 min default
    keep_result = int(os.getenv("ARQ_KEEP_RESULT_SECONDS", "3600"))  # 1 hour result TTL

    # Health
    health_check_interval = 30  # seconds

    # Cron — distill_sweep_training: 매 분 실행 (status='training' 빌드 스캔).
    # 동일 worker 가 다중 환경이면 arq 가 자체적으로 cron lock 보장.
    cron_jobs = [
        # status='training' 빌드 스캔 — 매 분 실행.
        cron(distill_sweep_training, minute=set(range(60))),
        # post-train (quantize/evaluate/deploy) worker crash 탐지 — 매 5분.
        cron(distill_sweep_post_train, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Bulk upload orphan 정리 — 매일 03:00 UTC 1회 (오프피크).
        cron(cleanup_orphan_uploads, hour={3}, minute={0}),
        # Graph schema bootstrap stale lock 정리 — 매일 03:05 UTC (Phase 3).
        cron(schema_bootstrap_cleanup, hour={3}, minute={5}),
        # Graph schema ops alerts sweep — 매 30분 (Phase 5b).
        cron(schema_alerts_sweep, minute={0, 30}),
        # Ingestion failure alert sweep — 매 30분 (PR-6 E). schema_alerts 와
        # 15분 offset 으로 부하 분산.
        cron(ingestion_failure_alert_sweep, minute={15, 45}),
        # Audit log archive — 매일 03:10 UTC (P1-2). retention=180d (env override).
        cron(audit_log_archive_sweep, hour={3}, minute={10}),
    ]

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        # P1-W2 — accurate cron list in startup log.
        cron_names = [
            "distill_sweep_training (every 1m)",
            "distill_sweep_post_train (every 5m)",
            "cleanup_orphan_uploads (daily 03:00)",
            "schema_bootstrap_cleanup (daily 03:05)",
            "schema_alerts_sweep (every 30m)",
            "ingestion_failure_alert_sweep (every 30m, +15m offset)",
            "audit_log_archive_sweep (daily 03:10)",
        ]
        logger.info(
            "Arq worker starting — max_jobs=%s max_tries=%s job_timeout=%ss "
            "cron=[%s]",
            WorkerSettings.max_jobs,
            WorkerSettings.max_tries,
            WorkerSettings.job_timeout,
            ", ".join(cron_names),
        )

        # P0-W2 + N3 — FeatureFlag invalidation listener spawn.
        ctx["_ff_listener_task"] = None
        try:
            from src.core.feature_flags import invalidation_listener
            redis = ctx.get("redis")
            if redis is not None:
                ctx["_ff_listener_task"] = asyncio.create_task(
                    invalidation_listener(redis),
                    name="feature_flag_invalidation_listener",
                )
                logger.info("FeatureFlag invalidation listener spawned (worker)")
            else:
                logger.warning(
                    "FeatureFlag listener (worker): redis None in ctx — "
                    "multi-worker invalidation 60s TTL only."
                )
        except Exception as e:  # noqa: BLE001 — N3: ConnectionError 광범위 catch
            logger.error(
                "FeatureFlag listener spawn FAILED in worker: %s",
                e, exc_info=True,
            )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        # Graceful cancel of FF listener task.
        ff_task = ctx.get("_ff_listener_task")
        if ff_task and not ff_task.done():
            ff_task.cancel()
            try:
                await asyncio.wait_for(ff_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        logger.info("Arq worker shutting down")
