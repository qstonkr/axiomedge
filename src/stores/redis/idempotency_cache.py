"""Idempotency Cache for request dedup.

Prevents duplicate request processing using a short-lived Redis key.
Uses SETNX for atomic check-and-set.

Key format: {prefix}:{request_hash}
Default TTL: 60 seconds.

Adapted from oreo-ecosystem infrastructure/cache/idempotency_cache.py.
Simplified: in-memory fallback when Redis is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def request_hash(query: str, kb_ids: list[str] | None = None, **kwargs: Any) -> str:
    """Generate a hash from request parameters for dedup."""
    raw = query.lower().strip()
    if kb_ids:
        raw += "::" + ",".join(sorted(kb_ids))
    for k, v in sorted(kwargs.items()):
        raw += f"::{k}={v}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class IdempotencyCache:
    """Request dedup cache.

    Supports Redis (async) and in-memory (sync fallback) backends.

    Usage::

        cache = IdempotencyCache(redis_client=redis)
        req_hash = request_hash(query, kb_ids)
        if not await cache.check_and_set(req_hash):
            return "Duplicate request"
        # ... process request ...
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        ttl_seconds: int = 60,
        prefix: str = "knowledge:idempotency",
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._prefix = prefix
        # In-memory fallback
        self._memory: dict[str, float] = {}
        self._lock = threading.Lock()

    async def check_and_set(self, req_hash: str) -> bool:
        """Atomically check if request is new and mark it.

        Returns:
            True if this is a new request (proceed with processing).
            False if this is a duplicate (skip processing).
        """
        if self._redis is not None:
            return await self._check_and_set_redis(req_hash)
        return self._check_and_set_memory(req_hash)

    async def _check_and_set_redis(self, req_hash: str) -> bool:
        key = f"{self._prefix}:{req_hash}"
        try:
            result = await self._redis.set(key, "1", nx=True, ex=self._ttl)
            return result is not None
        except OSError as e:
            logger.warning("Idempotency cache Redis error (allowing): %s", e)
            return True  # Allow on Redis failure (at-least-once)

    def _check_and_set_memory(self, req_hash: str) -> bool:
        """In-memory fallback with TTL eviction."""
        now = time.time()
        with self._lock:
            # Evict expired entries
            expired = [k for k, exp in self._memory.items() if now > exp]
            for k in expired:
                del self._memory[k]

            if req_hash in self._memory:
                return False  # Duplicate
            self._memory[req_hash] = now + self._ttl
            return True  # New request

    async def remove(self, req_hash: str) -> bool:
        """Remove a request hash (e.g. on processing failure for retry)."""
        if self._redis is not None:
            try:
                key = f"{self._prefix}:{req_hash}"
                deleted = await self._redis.delete(key)
                return deleted > 0
            except OSError:
                return False
        with self._lock:
            return self._memory.pop(req_hash, None) is not None
