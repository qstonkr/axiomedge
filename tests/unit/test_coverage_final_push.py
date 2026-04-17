"""Final coverage push — dedup_cache, qdrant_utils, frontmatter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── DedupCache ──────────────────────────────────────────────────


class TestDedupCacheExists:
    @pytest.mark.asyncio
    async def test_exists_true(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.sismember.return_value = 1
        assert await dc.exists("kb1", "abc") is True

    @pytest.mark.asyncio
    async def test_exists_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.sismember.side_effect = RuntimeError("fail")
        assert await dc.exists("kb1", "abc") is False


class TestDedupCacheAdd:
    @pytest.mark.asyncio
    async def test_add_success(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        await dc.add("kb1", "h1")
        dc._redis.sadd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.sadd.side_effect = RuntimeError("err")
        await dc.add("kb1", "h1")  # no exception


class TestDedupCacheAddBatch:
    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        await dc.add_batch("kb1", [])
        dc._redis.sadd.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        await dc.add_batch("kb1", ["h1", "h2"])
        dc._redis.sadd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_batch_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.sadd.side_effect = RuntimeError("err")
        await dc.add_batch("kb1", ["h1"])


class TestDedupCacheClear:
    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        await dc.clear("kb1")
        dc._redis.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.delete.side_effect = RuntimeError("err")
        await dc.clear("kb1")


class TestDedupCacheCount:
    @pytest.mark.asyncio
    async def test_count(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.scard.return_value = 42
        assert await dc.count("kb1") == 42

    @pytest.mark.asyncio
    async def test_count_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()
        dc._redis.scard.side_effect = RuntimeError("err")
        assert await dc.count("kb1") == 0


class TestDedupCacheStats:
    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()

        async def _scan(*a, **kw):
            yield "p:kb1"
            yield "p:kb2"

        dc._redis.scan_iter = _scan
        dc._redis.scard = AsyncMock(return_value=10)
        result = await dc.stats()
        assert result["total_kbs"] == 2

    @pytest.mark.asyncio
    async def test_stats_error(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._prefix = "p"
        dc._redis = AsyncMock()

        async def _scan(*a, **kw):
            raise RuntimeError("fail")
            yield  # noqa: unreachable

        dc._redis.scan_iter = _scan
        result = await dc.stats()
        assert "error" in result


class TestDedupCacheClose:
    @pytest.mark.asyncio
    async def test_close(self) -> None:
        from src.stores.redis.dedup_cache import DedupCache

        dc = DedupCache.__new__(DedupCache)
        dc._redis = AsyncMock()
        await dc.close()
        dc._redis.aclose.assert_awaited_once()


# ── qdrant_utils ────────────────────────────────────────────────


class TestQdrantUtils:
    def test_str_to_uuid_deterministic(self) -> None:
        from src.pipelines.qdrant_utils import str_to_uuid
        a = str_to_uuid("test-id")
        b = str_to_uuid("test-id")
        assert a == b

    def test_str_to_uuid_different(self) -> None:
        from src.pipelines.qdrant_utils import str_to_uuid
        assert str_to_uuid("a") != str_to_uuid("b")

    def test_truncate_short(self) -> None:
        from src.pipelines.qdrant_utils import truncate_content
        assert truncate_content("hello", 100) == "hello"

    def test_truncate_long(self) -> None:
        from src.pipelines.qdrant_utils import truncate_content
        result = truncate_content("a" * 200, 50)
        assert len(result) < 200
        assert "truncated" in result

    def test_get_qdrant_url(self) -> None:
        from src.pipelines.qdrant_utils import get_qdrant_url
        url = get_qdrant_url()
        assert isinstance(url, str)

    def test_create_qdrant_client(self) -> None:
        with patch(
            "src.pipelines.qdrant_utils.get_qdrant_url",
            return_value="http://localhost:6333",
        ):
            from src.pipelines.qdrant_utils import create_qdrant_client
            client = create_qdrant_client()
            assert client is not None


# ── content_hash ────────────────────────────────────────────────


class TestContentHash:
    def test_deterministic(self) -> None:
        from src.stores.redis.dedup_cache import content_hash
        assert content_hash("hello") == content_hash("hello")

    def test_case_insensitive(self) -> None:
        from src.stores.redis.dedup_cache import content_hash
        assert content_hash("Hello") == content_hash("hello")

    def test_strips_whitespace(self) -> None:
        from src.stores.redis.dedup_cache import content_hash
        assert content_hash("  hello  ") == content_hash("hello")
