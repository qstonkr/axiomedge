"""Arq worker entry point.

Run:
    uv run arq src.jobs.worker.WorkerSettings

Or via Makefile:
    make worker

Settings drive concurrency, retry policy, redis connection.
"""

from __future__ import annotations

import logging
import os

from arq.cron import cron

from src.jobs.distill_jobs import distill_sweep_training
from src.jobs.queue import redis_settings_from_env
from src.jobs.tasks import REGISTERED_TASKS

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
        cron(distill_sweep_training, minute=set(range(60))),
    ]

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logger.info(
            "Arq worker starting — max_jobs=%s max_tries=%s job_timeout=%ss "
            "cron=distill_sweep_training(매 60s)",
            WorkerSettings.max_jobs,
            WorkerSettings.max_tries,
            WorkerSettings.job_timeout,
        )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("Arq worker shutting down")
