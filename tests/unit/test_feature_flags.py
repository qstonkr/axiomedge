"""Feature flags — PR-11 (N).

- ENV override (FF_<NAME>) wins over DB
- Scope precedence: kb > org > global > default
- Cache TTL — 동일 호출이 재조회하지 않음
- Cache invalidate: 다음 호출은 재조회
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.feature_flags import (
    FeatureFlagCache,
    get_flag,
    get_flag_payload,
    reset_cache_for_testing,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_cache_for_testing()
    yield
    reset_cache_for_testing()


class TestEnvOverride:
    @pytest.mark.asyncio
    async def test_env_true_wins(self, monkeypatch):
        monkeypatch.setenv("FF_FOO", "true")
        loader = AsyncMock(return_value={"enabled": False})
        assert await get_flag("FOO", default=False, loader=loader) is True
        loader.assert_not_called()

    @pytest.mark.asyncio
    async def test_env_false_overrides_db_true(self, monkeypatch):
        monkeypatch.setenv("FF_FOO", "0")
        loader = AsyncMock(return_value={"enabled": True})
        assert await get_flag("FOO", default=True, loader=loader) is False


class TestScopePrecedence:
    @pytest.mark.asyncio
    async def test_kb_overrides_global(self):
        async def loader(name, scope):
            if scope == "kb:k1":
                return {"enabled": True}
            return {"enabled": False}
        assert await get_flag("X", kb_id="k1", loader=loader) is True

    @pytest.mark.asyncio
    async def test_falls_back_to_global(self):
        async def loader(name, scope):
            if scope == "_global":
                return {"enabled": True}
            return None
        assert await get_flag("X", kb_id="k1", loader=loader) is True

    @pytest.mark.asyncio
    async def test_default_when_no_scope_enabled(self):
        loader = AsyncMock(return_value={"enabled": False})
        assert await get_flag("X", default=False, loader=loader) is False
        assert await get_flag("X", default=True, loader=loader) is True


class TestCacheTtl:
    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        loader = AsyncMock(return_value={"enabled": True})
        await get_flag("Y", loader=loader)
        await get_flag("Y", loader=loader)
        loader.assert_awaited_once()  # 1회만

    @pytest.mark.asyncio
    async def test_cache_isolation_across_scopes(self):
        async def loader(name, scope):
            return {"enabled": scope == "kb:k1"}
        await get_flag("Z", kb_id="k1", loader=loader)
        await get_flag("Z", kb_id="k2", loader=loader)
        # kb:k1 enabled, kb:k2 + global 모두 disabled — 각각 다른 결과 캐시
        assert await get_flag("Z", kb_id="k1", loader=loader) is True
        assert await get_flag("Z", kb_id="k2", loader=loader) is False


class TestPayload:
    @pytest.mark.asyncio
    async def test_payload_returned_for_enabled_scope(self):
        async def loader(name, scope):
            if scope == "_global":
                return {"enabled": True, "payload": {"workers": 8}}
            return None
        payload = await get_flag_payload("FF", loader=loader)
        assert payload == {"workers": 8}

    @pytest.mark.asyncio
    async def test_empty_payload_when_no_enabled(self):
        loader = AsyncMock(return_value={"enabled": False, "payload": {"x": 1}})
        assert await get_flag_payload("FF", loader=loader) == {}


class TestCacheClass:
    @pytest.mark.asyncio
    async def test_invalidate_specific_key(self):
        cache = FeatureFlagCache(ttl_seconds=60)
        loader = AsyncMock(return_value={"enabled": True})
        await cache.get("a", "_global", loader=loader)
        cache.invalidate(name="a")
        await cache.get("a", "_global", loader=loader)
        assert loader.await_count == 2
