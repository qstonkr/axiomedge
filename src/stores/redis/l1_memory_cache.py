"""L1 In-Memory LRU Cache.

Exact-match in-memory LRU cache with TTL.
Thread-safe via threading.Lock (sync) for use from both sync and async contexts.

Characteristics:
- <1ms latency
- Configurable max entries (default 10,000)
- TTL per entry (default 300s / 5 min)
- Expected hit rate: ~15%

Adapted from oreo-ecosystem infrastructure/cache/l1_memory_cache.py.
Uses threading.Lock instead of asyncio.Lock for broader compatibility.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from typing import Any

from .cache_types import CacheEntry, ICacheLayer, _utc_now


class L1InMemoryCache(ICacheLayer):
    """L1: In-Memory LRU cache with TTL and thread safety."""

    DEFAULT_MAX_SIZE = 10_000
    DEFAULT_TTL_SECONDS = 300  # 5 min

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[CacheEntry, float]] = OrderedDict()
        self._lock = threading.Lock()

    async def get(self, key: str, **kwargs: Any) -> CacheEntry | None:
        """Exact-match lookup with TTL check and LRU promotion."""
        await asyncio.sleep(0)
        with self._lock:
            if key not in self._cache:
                return None

            entry, expire_time = self._cache[key]

            if time.time() > expire_time:
                del self._cache[key]
                return None

            # LRU: move to end
            self._cache.move_to_end(key)
            entry.hit_count += 1
            entry.last_accessed_at = _utc_now()
            return entry

    async def set(self, entry: CacheEntry, ttl_seconds: int | None = None) -> None:
        """Store entry with TTL, evicting LRU if at capacity."""
        await asyncio.sleep(0)
        ttl = ttl_seconds or self._ttl_seconds
        with self._lock:
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            expire_time = time.time() + ttl
            self._cache[entry.key] = (entry, expire_time)

    async def delete(self, key: str) -> bool:
        await asyncio.sleep(0)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def delete_by_prefix(self, prefix: str) -> int:
        """Delete all entries whose key starts with prefix."""
        await asyncio.sleep(0)
        with self._lock:
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                del self._cache[k]
            return len(keys)

    async def invalidate_by_metadata_value(self, meta_key: str, meta_value: str) -> int:
        """Delete entries whose metadata[meta_key] matches meta_value."""
        await asyncio.sleep(0)
        with self._lock:
            now = time.time()
            to_delete: list[str] = []
            for key, (entry, expire_time) in self._cache.items():
                if now > expire_time:
                    to_delete.append(key)
                    continue
                val = (entry.metadata or {}).get(meta_key)
                if val == meta_value or (
                    isinstance(val, (list, set, tuple)) and meta_value in val
                ):
                    to_delete.append(key)
            for key in to_delete:
                del self._cache[key]
            return len(to_delete)

    async def clear(self) -> int:
        await asyncio.sleep(0)
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
            }

    def __len__(self) -> int:
        return len(self._cache)
