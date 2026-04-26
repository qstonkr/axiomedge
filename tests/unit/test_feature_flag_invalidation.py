"""FeatureFlagCache cross-worker invalidation — P1-6."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.feature_flags import (
    INVALIDATION_CHANNEL,
    invalidation_listener,
    publish_invalidation,
    reset_cache_for_testing,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_cache_for_testing()
    yield
    reset_cache_for_testing()


class TestPublish:
    @pytest.mark.asyncio
    async def test_publishes_json_payload(self):
        redis = MagicMock()
        redis.publish = AsyncMock()
        ok = await publish_invalidation(
            redis, name="X", scope="kb:k1",
        )
        assert ok is True
        redis.publish.assert_awaited_once()
        channel, raw = redis.publish.await_args.args
        assert channel == INVALIDATION_CHANNEL
        data = json.loads(raw)
        assert data == {"name": "X", "scope": "kb:k1"}

    @pytest.mark.asyncio
    async def test_returns_false_when_redis_none(self):
        ok = await publish_invalidation(None, name="X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_swallows_redis_error(self):
        redis = MagicMock()
        redis.publish = AsyncMock(side_effect=RuntimeError("redis down"))
        ok = await publish_invalidation(redis, name="X")
        assert ok is False


class TestListener:
    @pytest.mark.asyncio
    async def test_invalidates_on_message(self):
        # 미리 cache 에 entry 채워둠
        from src.core.feature_flags import _cache
        _cache._cache[("X", "_global")] = (True, {}, 0.0)

        # mock pubsub: subscribe → listen yields one message → exit
        async def _listen():
            yield {
                "type": "message",
                "data": json.dumps(
                    {"name": "X", "scope": "_global"},
                ),
            }

        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.listen = MagicMock(return_value=_listen())
        redis = MagicMock()
        redis.pubsub = MagicMock(return_value=pubsub)

        await invalidation_listener(redis)

        # cache 항목이 비워졌는지
        assert ("X", "_global") not in _cache._cache

    @pytest.mark.asyncio
    async def test_listener_returns_when_redis_none(self):
        # graceful — exception 없이 즉시 종료
        await invalidation_listener(None)

    @pytest.mark.asyncio
    async def test_listener_handles_subscribe_error(self):
        redis = MagicMock()
        redis.pubsub = MagicMock(
            side_effect=RuntimeError("pubsub broken"),
        )
        # graceful exit
        await invalidation_listener(redis)
