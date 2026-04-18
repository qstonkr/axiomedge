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

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logger.info(
            "Arq worker starting — max_jobs=%s max_tries=%s job_timeout=%ss",
            WorkerSettings.max_jobs,
            WorkerSettings.max_tries,
            WorkerSettings.job_timeout,
        )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("Arq worker shutting down")
