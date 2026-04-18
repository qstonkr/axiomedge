"""Tests for retry + backoff resilience decorator."""

from __future__ import annotations

import httpx
import pytest

from src.core.resilience import ResilienceConfig, with_resilience


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure() -> None:
    """Decorator retries httpx.ConnectError and eventually succeeds."""
    attempts = {"n": 0}

    @with_resilience("test", ResilienceConfig(max_attempts=3, initial_backoff_seconds=0.01))
    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectError("simulated transient failure")
        return "ok"

    result = await flaky()
    assert result == "ok"
    assert attempts["n"] == 2  # 1 fail + 1 success


@pytest.mark.asyncio
async def test_reraises_after_exhausting_attempts() -> None:
    """After max_attempts, the original exception is re-raised."""
    attempts = {"n": 0}

    @with_resilience("test", ResilienceConfig(max_attempts=2, initial_backoff_seconds=0.01))
    async def always_fails() -> str:
        attempts["n"] += 1
        raise httpx.TimeoutException("never recovers")

    with pytest.raises(httpx.TimeoutException):
        await always_fails()
    assert attempts["n"] == 2  # exactly max_attempts


@pytest.mark.asyncio
async def test_does_not_retry_non_transient() -> None:
    """ValueError (not in retry_on) propagates immediately."""
    attempts = {"n": 0}

    @with_resilience("test", ResilienceConfig(max_attempts=3, initial_backoff_seconds=0.01))
    async def value_error() -> str:
        attempts["n"] += 1
        raise ValueError("non-transient")

    with pytest.raises(ValueError):
        await value_error()
    assert attempts["n"] == 1  # no retry — raised immediately


@pytest.mark.asyncio
async def test_succeeds_first_try_no_retry() -> None:
    """If function succeeds, no retry happens."""
    attempts = {"n": 0}

    @with_resilience("test", ResilienceConfig(max_attempts=3))
    async def fast() -> int:
        attempts["n"] += 1
        return 42

    assert await fast() == 42
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_custom_retry_on_extends_default() -> None:
    """retry_on can include custom exception types."""
    attempts = {"n": 0}

    class MyTransientError(Exception):
        pass

    @with_resilience(
        "test",
        ResilienceConfig(
            max_attempts=3,
            initial_backoff_seconds=0.01,
            retry_on=(MyTransientError,),
        ),
    )
    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise MyTransientError("custom")
        return "ok"

    assert await flaky() == "ok"
    assert attempts["n"] == 2
