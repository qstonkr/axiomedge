"""Arq queue helpers — pool acquisition + enqueue with sensible defaults."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


def redis_settings_from_env() -> RedisSettings:
    """Build RedisSettings from ARQ_REDIS_URL or REDIS_URL env."""
    url = os.getenv("ARQ_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379")
    parsed = urlparse(url)
    db = 0
    if parsed.path and parsed.path.startswith("/"):
        try:
            db = int(parsed.path[1:])
        except ValueError:
            db = 0
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        database=db,
    )


async def get_pool() -> ArqRedis:
    """Lazy singleton — single connection pool reused across enqueues."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings_from_env())
        logger.info("Arq pool initialized")
    return _pool


async def enqueue_job(
    function_name: str,
    *args: Any,
    _job_id: str | None = None,
    _defer_seconds: float | None = None,
    **kwargs: Any,
) -> Any:
    """Enqueue a job. Returns the Job handle (or None if pool unavailable).

    Wraps the Arq pool API so callers don't need to know about pool lifecycle.
    """
    pool = await get_pool()
    return await pool.enqueue_job(
        function_name,
        *args,
        _job_id=_job_id,
        _defer_by=_defer_seconds,
        **kwargs,
    )
