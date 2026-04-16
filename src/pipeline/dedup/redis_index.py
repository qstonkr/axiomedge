"""RedisDedupIndex - Redis SET based cross-run content hash index.

DedupPipeline is in-memory only, so all Bloom/LSH/SemHash state is lost on
process restart. This index persists SHA-256 content hashes to Redis SETs per KB,
providing cross-run exact duplicate detection.

Redis key structure:
- dedup:content_hashes:{kb_id}  (SET of SHA-256[:32] strings, chunk level)
- dedup:doc_hashes:{kb_id}      (SET of SHA-256[:32] strings, document level)

Document-level index allows skipping entire documents before pipeline entry,
preventing unnecessary parsing/chunking/embedding after restart.

Memory estimate: ~4.8MB/KB (50K chunks x 96B per entry)
TTL: 90 days auto-expiry (set on first add, not refreshed)

Pattern: lazy init, fire-and-forget, graceful degradation.

Adapted from oreo-ecosystem infrastructure/dedup/redis_dedup_index.py.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEDUP_HASH_KEY_PREFIX = "dedup:content_hashes:"
DEDUP_DOC_HASH_KEY_PREFIX = "dedup:doc_hashes:"
DEDUP_HASH_TTL_DAYS = 90


class RedisDedupIndex:
    """Redis SET based cross-run content hash index.

    Stores content hashes per KB to detect chunks already ingested in previous runs.

    Graceful degradation: all methods return False/0 on Redis failure,
    never blocking ingestion.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._enabled = redis_client is not None

    @property
    def enabled(self) -> bool:
        return self._enabled and self._redis is not None

    def _key(self, kb_id: str) -> str:
        return f"{DEDUP_HASH_KEY_PREFIX}{kb_id}"

    # ------------------------------------------------------------------
    # Chunk-level index
    # ------------------------------------------------------------------

    async def contains(self, kb_id: str, content_hash: str) -> bool:
        """Check if content hash exists in the KB index (SISMEMBER O(1))."""
        if not self.enabled:
            return False
        try:
            return bool(await self._redis.sismember(self._key(kb_id), content_hash))
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.contains_failed: %s", e)
            return False

    async def add(self, kb_id: str, content_hash: str) -> bool:
        """Add content hash to KB index (SADD + conditional EXPIRE)."""
        if not self.enabled:
            return False
        try:
            key = self._key(kb_id)
            added = await self._redis.sadd(key, content_hash)
            if added:
                ttl = await self._redis.ttl(key)
                if ttl == -1:  # no TTL set
                    await self._redis.expire(key, DEDUP_HASH_TTL_DAYS * 86400)
            return bool(added)
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.add_failed: %s", e)
            return False

    async def add_batch(self, kb_id: str, hashes: list[str]) -> int:
        """Add multiple content hashes via SADD batch."""
        if not self.enabled or not hashes:
            return 0
        try:
            key = self._key(kb_id)
            added = await self._redis.sadd(key, *hashes)
            ttl = await self._redis.ttl(key)
            if ttl == -1:
                await self._redis.expire(key, DEDUP_HASH_TTL_DAYS * 86400)
            return int(added)
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.add_batch_failed: %s", e)
            return 0

    async def clear(self, kb_id: str) -> bool:
        """Delete KB index (for KB deletion or re-indexing)."""
        if not self.enabled:
            return False
        try:
            await self._redis.delete(self._key(kb_id))
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.clear_failed: %s", e)
            return False

    async def size(self, kb_id: str) -> int:
        """Get number of hashes in KB index (SCARD)."""
        if not self.enabled:
            return 0
        try:
            return int(await self._redis.scard(self._key(kb_id)))
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.size_failed: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Document-level hash index (pre-pipeline skip)
    # ------------------------------------------------------------------

    def _doc_key(self, kb_id: str) -> str:
        return f"{DEDUP_DOC_HASH_KEY_PREFIX}{kb_id}"

    async def contains_doc(self, kb_id: str, doc_content_hash: str) -> bool:
        """Check if document content hash was already ingested (SISMEMBER O(1))."""
        if not self.enabled:
            return False
        try:
            return bool(await self._redis.sismember(self._doc_key(kb_id), doc_content_hash))
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.contains_doc_failed: %s", e)
            return False

    async def add_doc(self, kb_id: str, doc_content_hash: str) -> bool:
        """Register document content hash after successful ingestion."""
        if not self.enabled:
            return False
        try:
            key = self._doc_key(kb_id)
            added = await self._redis.sadd(key, doc_content_hash)
            if added:
                ttl = await self._redis.ttl(key)
                if ttl == -1:
                    await self._redis.expire(key, DEDUP_HASH_TTL_DAYS * 86400)
            return bool(added)
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.add_doc_failed: %s", e)
            return False

    async def add_doc_batch(self, kb_id: str, hashes: list[str]) -> int:
        """Register multiple document content hashes after successful ingestion."""
        if not self.enabled or not hashes:
            return 0
        try:
            key = self._doc_key(kb_id)
            added = await self._redis.sadd(key, *hashes)
            ttl = await self._redis.ttl(key)
            if ttl == -1:
                await self._redis.expire(key, DEDUP_HASH_TTL_DAYS * 86400)
            return int(added)
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.add_doc_batch_failed: %s", e)
            return 0

    async def clear_docs(self, kb_id: str) -> bool:
        """Delete document-level index (for force re-sync)."""
        if not self.enabled:
            return False
        try:
            await self._redis.delete(self._doc_key(kb_id))
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("redis_dedup_index.clear_docs_failed: %s", e)
            return False
