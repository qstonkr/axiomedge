"""Redis-based search cache.

Stores query -> search results in Redis with TTL.
L1: exact hash match (query string SHA256 -> cached result).
L2: Redis persistence across restarts.

Extracted from oreo-ecosystem MultiLayerCache (L1 + L3 layers).
Skips GPTCache/VSS semantic matching for local simplicity.

Key format: {prefix}:{sha256_hex[:16]}
Default TTL: 1 hour.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class SearchCache:
    """Async Redis search cache with exact-match hashing.

    Usage::

        cache = SearchCache(redis_url="redis://localhost:6379")
        cached = await cache.get(query, kb_ids)
        if cached:
            return cached
        # ... compute result ...
        await cache.set(query, kb_ids, result)
    """

    def __init__(
        self,
        redis_url: str = "",
        ttl: int = 3600,
        prefix: str = "knowledge:search",
    ) -> None:
        from src.config import get_settings
        self._redis = aioredis.from_url(
            redis_url or get_settings().redis.url, decode_responses=True,
        )
        self._ttl = ttl
        self._prefix = prefix

    async def get(self, query: str, kb_ids: list[str], top_k: int = 0) -> dict[str, Any] | None:
        """Look up cached search result by exact query + kb_ids hash."""
        key = self._make_key(query, kb_ids, top_k)
        try:
            data = await self._redis.get(key)
            if data:
                logger.debug("Search cache HIT: %s", key)
                return json.loads(data)
        except Exception as e:
            logger.warning("Search cache get error: %s", e)
        return None

    async def set(self, query: str, kb_ids: list[str], result: dict[str, Any], top_k: int = 0) -> None:
        """Store search result with TTL."""
        key = self._make_key(query, kb_ids, top_k)
        try:
            await self._redis.setex(
                key,
                self._ttl,
                json.dumps(result, ensure_ascii=False, default=str),
            )
            logger.debug("Search cache SET: %s (ttl=%ds)", key, self._ttl)
        except Exception as e:
            logger.warning("Search cache set error: %s", e)

    async def clear(self) -> int:
        """Delete all keys under this prefix. Returns count of deleted keys."""
        try:
            keys = []
            async for key in self._redis.scan_iter(match=f"{self._prefix}:*"):
                keys.append(key)
            if keys:
                deleted = await self._redis.delete(*keys)
                logger.info("Search cache cleared: %d keys", deleted)
                return deleted
            return 0
        except Exception as e:
            logger.warning("Search cache clear error: %s", e)
            return 0

    async def stats(self) -> dict[str, Any]:
        """Return basic cache stats."""
        try:
            count = 0
            async for _ in self._redis.scan_iter(match=f"{self._prefix}:*"):
                count += 1
            return {"prefix": self._prefix, "key_count": count, "ttl_seconds": self._ttl}
        except Exception as e:
            return {"prefix": self._prefix, "key_count": 0, "error": str(e)}

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()

    def _make_key(self, query: str, kb_ids: list[str], top_k: int = 0) -> str:
        raw = f"{query}::{','.join(sorted(kb_ids))}::top_k={top_k}"
        h = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"{self._prefix}:{h}"
