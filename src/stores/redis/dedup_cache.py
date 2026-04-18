"""Redis-based dedup cache for ingestion.

Tracks content hashes per KB to skip re-ingesting duplicate documents.
Uses Redis SET per KB for O(1) membership checks.

Simplified: no LSH, no semantic dedup, no LLM conflict detection.

Key format: {prefix}:{kb_id}  (Redis SET containing content hashes)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


def content_hash(text: str) -> str:
    """SHA256 hash of normalized content."""
    normalized = text.lower().strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


class DedupCache:
    """Async Redis dedup cache using SET per KB.

    Usage::

        dedup = DedupCache(redis_url="redis://localhost:6379")
        h = content_hash(document_text)
        if await dedup.exists("my-kb", h):
            print("Duplicate, skipping")
        else:
            # ... ingest ...
            await dedup.add("my-kb", h)
    """

    def __init__(
        self,
        redis_url: str = "",
        prefix: str = "knowledge:dedup",
    ) -> None:
        from src.config import get_settings
        self._redis = aioredis.from_url(
            redis_url or get_settings().redis.url, decode_responses=True,
        )
        self._prefix = prefix

    async def exists(self, kb_id: str, content_hash: str) -> bool:
        """Check if content hash already exists for this KB."""
        key = f"{self._prefix}:{kb_id}"
        try:
            return bool(await self._redis.sismember(key, content_hash))
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dedup cache exists error: %s", e)
            return False

    async def add(self, kb_id: str, content_hash: str) -> None:
        """Register a content hash for this KB."""
        key = f"{self._prefix}:{kb_id}"
        try:
            await self._redis.sadd(key, content_hash)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dedup cache add error: %s", e)

    async def add_batch(self, kb_id: str, hashes: list[str]) -> None:
        """Register multiple content hashes at once."""
        if not hashes:
            return
        key = f"{self._prefix}:{kb_id}"
        try:
            await self._redis.sadd(key, *hashes)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dedup cache add_batch error: %s", e)

    async def clear(self, kb_id: str) -> None:
        """Clear all dedup hashes for a KB (e.g. before force rebuild)."""
        key = f"{self._prefix}:{kb_id}"
        try:
            await self._redis.delete(key)
            logger.info("Dedup cache cleared for kb_id=%s", kb_id)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dedup cache clear error: %s", e)

    async def count(self, kb_id: str) -> int:
        """Return number of tracked hashes for a KB."""
        key = f"{self._prefix}:{kb_id}"
        try:
            return await self._redis.scard(key)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dedup cache count error: %s", e)
            return 0

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()

    async def stats(self) -> dict[str, Any]:
        """Return stats across all KBs."""
        try:
            result: dict[str, int] = {}
            async for key in self._redis.scan_iter(match=f"{self._prefix}:*"):
                kb_id = key.replace(f"{self._prefix}:", "")
                result[kb_id] = await self._redis.scard(key)
            return {"kbs": result, "total_kbs": len(result)}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            return {"kbs": {}, "error": str(e)}
