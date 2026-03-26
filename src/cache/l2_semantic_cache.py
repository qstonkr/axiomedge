"""L2 Redis Semantic Cache.

Redis-backed semantic cache using embedding cosine similarity.
On cache check: embed query, scan stored entries, return best match
above the domain-specific threshold.

Characteristics:
- <10ms latency (Redis + in-memory similarity)
- Configurable max entries (default 50,000)
- TTL 6 hours
- Cosine similarity threshold from config
- Expected hit rate: ~20%

Simplified from oreo-ecosystem L2 (no GPTCache dependency).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

from .cache_types import (
    CacheDomain,
    CacheEntry,
    DOMAIN_THRESHOLDS,
    ICacheLayer,
    _utc_now,
)

logger = logging.getLogger(__name__)


class L2SemanticCache(ICacheLayer):
    """L2: Redis-backed semantic cache with cosine similarity matching.

    Stores query embeddings alongside cached responses in Redis.
    On lookup, embeds the query and compares against stored embeddings
    using cosine similarity. Returns the best match above the
    domain-specific threshold.

    Falls back to exact-match if no embedding provider is available.
    """

    DEFAULT_MAX_ENTRIES = 50_000
    DEFAULT_TTL_SECONDS = 21600  # 6 hours

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        embedding_provider: Any | None = None,
        similarity_threshold: float = 0.92,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        prefix: str = "knowledge:l2cache",
    ) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._embedding_provider = embedding_provider
        self._similarity_threshold = similarity_threshold
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._prefix = prefix

    async def get(
        self,
        key: str,
        query: str | None = None,
        domain: CacheDomain = CacheDomain.GENERAL,
        **kwargs: Any,
    ) -> CacheEntry | None:
        """Semantic similarity-based lookup.

        First tries exact key match. If query and embedding_provider
        are available, scans stored entries for cosine similarity match.
        """
        threshold = DOMAIN_THRESHOLDS.get(domain, self._similarity_threshold)

        # Policy domain: exact match only
        if threshold >= 1.0:
            return await self._exact_match(key)

        # No embedding provider or query: exact match fallback
        if not self._embedding_provider or not query:
            return await self._exact_match(key)

        # Embed query and search for similar entries
        try:
            query_embedding = await self._embedding_provider.embed(query)
            if not query_embedding:
                return await self._exact_match(key)
            return await self._semantic_search(query_embedding, threshold)
        except Exception as e:
            logger.warning("L2 semantic search error, falling back to exact match: %s", e)
            return await self._exact_match(key)

    async def _exact_match(self, key: str) -> CacheEntry | None:
        """Exact key lookup in Redis."""
        redis_key = f"{self._prefix}:{key}"
        try:
            data = await self._redis.get(redis_key)
            if not data:
                return None
            stored = json.loads(data)
            entry = CacheEntry(
                key=key,
                query=stored.get("query", ""),
                response=stored.get("response"),
                embedding=stored.get("embedding"),
                domain=CacheDomain(stored.get("domain", "general")),
                metadata=stored.get("metadata", {}),
            )
            entry.hit_count += 1
            entry.last_accessed_at = _utc_now()
            return entry
        except Exception as e:
            logger.warning("L2 exact match error: %s", e)
            return None

    async def _semantic_search(
        self, query_embedding: list[float], threshold: float
    ) -> CacheEntry | None:
        """Scan stored entries and find best cosine similarity match."""
        best_entry: CacheEntry | None = None
        best_sim: float = 0.0

        try:
            # Scan all L2 entries (bounded by max_entries)
            cursor = 0
            scanned = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._prefix}:*", count=200
                )
                for redis_key in keys:
                    if scanned >= self._max_entries:
                        break
                    scanned += 1
                    try:
                        data = await self._redis.get(redis_key)
                        if not data:
                            continue
                        stored = json.loads(data)
                        emb = stored.get("embedding")
                        if not emb:
                            continue
                        sim = _cosine_similarity(query_embedding, emb)
                        if sim >= threshold and sim > best_sim:
                            best_sim = sim
                            best_entry = CacheEntry(
                                key=redis_key.replace(f"{self._prefix}:", ""),
                                query=stored.get("query", ""),
                                response=stored.get("response"),
                                embedding=emb,
                                similarity=sim,
                                domain=CacheDomain(stored.get("domain", "general")),
                                metadata=stored.get("metadata", {}),
                            )
                    except Exception:
                        continue
                if cursor == 0 or scanned >= self._max_entries:
                    break

            if best_entry:
                best_entry.similarity = best_sim
                best_entry.hit_count += 1
                best_entry.last_accessed_at = _utc_now()
            return best_entry
        except Exception as e:
            logger.warning("L2 semantic search scan error: %s", e)
            return None

    async def set(self, entry: CacheEntry, ttl_seconds: int | None = None) -> None:
        """Store entry with embedding in Redis."""
        ttl = ttl_seconds or self._ttl_seconds
        redis_key = f"{self._prefix}:{entry.key}"

        # Generate embedding if provider available and not already present
        if self._embedding_provider and not entry.embedding:
            try:
                entry.embedding = await self._embedding_provider.embed(entry.query)
            except Exception as e:
                logger.warning("L2 embedding generation failed: %s", e)

        stored = {
            "query": entry.query,
            "response": entry.response,
            "embedding": entry.embedding,
            "domain": entry.domain.value,
            "metadata": entry.metadata,
        }

        try:
            await self._redis.setex(
                redis_key,
                ttl,
                json.dumps(stored, ensure_ascii=False, default=str),
            )
        except Exception as e:
            logger.warning("L2 cache set error: %s", e)

    async def delete(self, key: str) -> bool:
        redis_key = f"{self._prefix}:{key}"
        try:
            deleted = await self._redis.delete(redis_key)
            return deleted > 0
        except Exception as e:
            logger.warning("L2 cache delete error: %s", e)
            return False

    async def invalidate_by_metadata_value(self, meta_key: str, meta_value: str) -> int:
        """Scan and delete entries whose metadata[meta_key] matches."""
        deleted_count = 0
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._prefix}:*", count=200
                )
                for redis_key in keys:
                    try:
                        data = await self._redis.get(redis_key)
                        if not data:
                            continue
                        stored = json.loads(data)
                        val = stored.get("metadata", {}).get(meta_key)
                        if val == meta_value or (
                            isinstance(val, (list, tuple)) and meta_value in val
                        ):
                            await self._redis.delete(redis_key)
                            deleted_count += 1
                    except Exception:
                        continue
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("L2 metadata invalidation error: %s", e)
        return deleted_count

    async def clear(self) -> int:
        try:
            keys = []
            async for key in self._redis.scan_iter(match=f"{self._prefix}:*"):
                keys.append(key)
            if keys:
                return await self._redis.delete(*keys)
            return 0
        except Exception as e:
            logger.warning("L2 cache clear error: %s", e)
            return 0

    def stats(self) -> dict[str, Any]:
        return {
            "prefix": self._prefix,
            "threshold": self._similarity_threshold,
            "ttl_seconds": self._ttl_seconds,
            "has_embedding_provider": self._embedding_provider is not None,
        }

    async def close(self) -> None:
        await self._redis.aclose()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
