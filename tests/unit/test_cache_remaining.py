"""Comprehensive tests for src/cache/ — redis_cache, dedup_cache, cache_key_builder."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# SearchCache
# ===========================================================================

class TestSearchCache:
    async def test_get_hit(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.get.return_value = json.dumps({"results": [1, 2, 3]})
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            result = await cache.get("test query", ["kb1"])
            assert result is not None
            assert result["results"] == [1, 2, 3]

    async def test_get_miss(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.get.return_value = None
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            result = await cache.get("test query", ["kb1"])
            assert result is None

    async def test_get_error(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.get.side_effect = Exception("Redis down")
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            result = await cache.get("test", ["kb1"])
            assert result is None

    async def test_set(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379", ttl=300)
            await cache.set("test query", ["kb1"], {"results": [1]})
            redis.setex.assert_awaited_once()

    async def test_set_error(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.setex.side_effect = Exception("Redis error")
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            await cache.set("test", ["kb1"], {"r": []})  # Should not raise

    async def test_clear(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()

            async def fake_scan(*args, **kwargs):
                for key in ["knowledge:search:abc", "knowledge:search:def"]:
                    yield key

            redis.scan_iter = fake_scan
            redis.delete.return_value = 2
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            count = await cache.clear()
            assert count == 2

    async def test_stats(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()

            async def fake_scan(*args, **kwargs):
                for key in ["knowledge:search:abc"]:
                    yield key

            redis.scan_iter = fake_scan
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            stats = await cache.stats()
            assert stats["key_count"] == 1

    async def test_close(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = SearchCache(redis_url="redis://localhost:6379")
            await cache.close()
            redis.aclose.assert_awaited_once()

    def test_make_key_deterministic(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            mock_redis.from_url.return_value = AsyncMock()
            cache = SearchCache()

        key1 = cache._make_key("query", ["kb1", "kb2"])
        key2 = cache._make_key("query", ["kb2", "kb1"])
        assert key1 == key2  # Sorted kb_ids

    def test_make_key_with_top_k(self):
        from src.cache.redis_cache import SearchCache

        with patch("src.cache.redis_cache.aioredis") as mock_redis:
            mock_redis.from_url.return_value = AsyncMock()
            cache = SearchCache()

        key1 = cache._make_key("query", ["kb1"], top_k=5)
        key2 = cache._make_key("query", ["kb1"], top_k=10)
        assert key1 != key2


# ===========================================================================
# DedupCache
# ===========================================================================

class TestDedupCache:
    async def test_exists_true(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.sismember.return_value = True
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            result = await cache.exists("kb1", "hash123")
            assert result is True

    async def test_exists_false(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.sismember.return_value = False
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            result = await cache.exists("kb1", "hash123")
            assert result is False

    async def test_exists_error(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.sismember.side_effect = Exception("fail")
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            result = await cache.exists("kb1", "hash123")
            assert result is False

    async def test_add(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            await cache.add("kb1", "hash123")
            redis.sadd.assert_awaited_once()

    async def test_add_batch_empty(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            await cache.add_batch("kb1", [])
            redis.sadd.assert_not_awaited()

    async def test_add_batch(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            await cache.add_batch("kb1", ["h1", "h2"])
            redis.sadd.assert_awaited_once()

    async def test_clear(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            await cache.clear("kb1")
            redis.delete.assert_awaited_once()

    async def test_count(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            redis.scard.return_value = 42
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            result = await cache.count("kb1")
            assert result == 42

    async def test_close(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            await cache.close()
            redis.aclose.assert_awaited_once()

    async def test_stats(self):
        from src.cache.dedup_cache import DedupCache

        with patch("src.cache.dedup_cache.aioredis") as mock_redis:
            redis = AsyncMock()

            async def fake_scan(*args, **kwargs):
                yield "knowledge:dedup:kb1"

            redis.scan_iter = fake_scan
            redis.scard.return_value = 10
            mock_redis.from_url.return_value = redis

            cache = DedupCache(redis_url="redis://localhost:6379")
            stats = await cache.stats()
            assert stats["total_kbs"] == 1


def test_content_hash():
    from src.cache.dedup_cache import content_hash
    h1 = content_hash("hello world")
    h2 = content_hash("Hello World")  # Different case -> same after normalization
    assert h1 == h2
    assert len(h1) == 32


# ===========================================================================
# CacheKeyBuilder
# ===========================================================================

class TestCacheKeyBuilder:
    def test_normalize_query(self):
        from src.cache.cache_key_builder import normalize_query
        assert normalize_query("  Hello   World  ") == "hello world"
        assert normalize_query("") == ""

    def test_build_cache_key_basic(self):
        from src.cache.cache_key_builder import build_cache_key
        key = build_cache_key("test query")
        assert key.startswith("knowledge:")
        assert ":" in key

    def test_build_cache_key_with_kb_ids(self):
        from src.cache.cache_key_builder import build_cache_key
        key = build_cache_key("test", kb_ids=["kb1", "kb2"])
        assert key.count(":") == 2

    def test_build_cache_key_sorted_kb_ids(self):
        from src.cache.cache_key_builder import build_cache_key
        key1 = build_cache_key("test", kb_ids=["kb1", "kb2"])
        key2 = build_cache_key("test", kb_ids=["kb2", "kb1"])
        assert key1 == key2

    def test_build_cache_key_with_top_k(self):
        from src.cache.cache_key_builder import build_cache_key
        key1 = build_cache_key("test", top_k=5)
        key2 = build_cache_key("test", top_k=10)
        assert key1 != key2

    def test_build_cache_key_custom_prefix(self):
        from src.cache.cache_key_builder import build_cache_key
        key = build_cache_key("test", prefix="custom")
        assert key.startswith("custom:")

    def test_build_cache_key_deterministic(self):
        from src.cache.cache_key_builder import build_cache_key
        key1 = build_cache_key("same query", kb_ids=["kb1"])
        key2 = build_cache_key("same query", kb_ids=["kb1"])
        assert key1 == key2

    def test_build_cache_key_case_insensitive(self):
        from src.cache.cache_key_builder import build_cache_key
        key1 = build_cache_key("Test Query")
        key2 = build_cache_key("test query")
        assert key1 == key2
