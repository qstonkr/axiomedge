"""retry_with_backoff helper — PR-2 (D).

- 정상 호출: 1회만 실행
- N-1 회 실패 후 마지막 attempt 성공
- 모두 실패 시 마지막 예외 propagate
- 비대상 예외는 retry 없이 즉시 propagate
- 누적 sleep 이 지수적으로 증가
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.pipelines._retry import retry_with_backoff


class TestSuccessPath:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_success(self):
        fn = AsyncMock(return_value=42)
        result = await retry_with_backoff(fn, max_attempts=4)
        assert result == 42
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_succeeds_on_last_attempt(self, monkeypatch):
        # asyncio.sleep 을 no-op 으로 패치 — 테스트 가속
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _instant_sleep
        )

        fn = AsyncMock(
            side_effect=[RuntimeError("a"), RuntimeError("b"),
                         RuntimeError("c"), 99],
        )
        result = await retry_with_backoff(
            fn, max_attempts=4, initial_delay=0.1, jitter=0.0,
        )
        assert result == 99
        assert fn.call_count == 4


class TestFailurePath:
    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self, monkeypatch):
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _instant_sleep
        )

        fn = AsyncMock(side_effect=RuntimeError("persistent"))
        with pytest.raises(RuntimeError, match="persistent"):
            await retry_with_backoff(
                fn, max_attempts=3, initial_delay=0.1,
            )
        assert fn.call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_exception_propagates_immediately(self):
        fn = AsyncMock(side_effect=KeyError("not retried"))
        with pytest.raises(KeyError):
            await retry_with_backoff(
                fn, max_attempts=4,
                exceptions=(RuntimeError,),  # KeyError 아님
            )
        assert fn.call_count == 1


class TestBackoffSchedule:
    @pytest.mark.asyncio
    async def test_exponential_growth_within_max(self, monkeypatch):
        sleeps: list[float] = []

        async def _capture_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _capture_sleep
        )

        fn = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await retry_with_backoff(
                fn, max_attempts=4, initial_delay=1.0,
                max_delay=30.0, jitter=0.0,
            )
        # 3 sleep ((4-1)회), 지수 증가 — jitter=0
        assert len(sleeps) == 3
        assert sleeps[0] == pytest.approx(1.0)
        assert sleeps[1] == pytest.approx(2.0)
        assert sleeps[2] == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_max_delay_caps(self, monkeypatch):
        sleeps: list[float] = []

        async def _capture_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _capture_sleep
        )

        fn = AsyncMock(side_effect=RuntimeError("x"))
        with pytest.raises(RuntimeError):
            await retry_with_backoff(
                fn, max_attempts=5, initial_delay=10.0,
                max_delay=15.0, jitter=0.0,
            )
        # 1st sleep delay=10 → cap none; 2nd delay=20 → cap 15; 3rd delay=40 → cap 15; 4th delay=80 → cap 15
        assert sleeps == [10.0, 15.0, 15.0, 15.0]


class TestValidation:
    @pytest.mark.asyncio
    async def test_zero_max_attempts_raises(self):
        with pytest.raises(ValueError):
            await retry_with_backoff(
                AsyncMock(return_value=1), max_attempts=0,
            )
