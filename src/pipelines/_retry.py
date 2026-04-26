"""Exponential backoff with jitter — embedding/external call 신뢰성 (PR-2 D).

기존 ``_EMBED_RETRY_DELAY=0.5/5`` 고정 sleep 대신 지수 백오프 + jitter 로
교체. tenacity 의존성을 추가하지 않기 위해 자체 helper 로 구현.

기본 정책: 4회 시도, 1s → 2s → 4s, max 30s, jitter ±25%.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.25,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    op_name: str = "op",
) -> T:
    """Run ``fn()``; retry with exponential backoff on listed exceptions.

    Sleeps between attempts: ``min(initial_delay * 2^(n-1), max_delay)``,
    multiplied by a uniform jitter factor in ``[1-jitter, 1+jitter]``.

    Args:
        fn: zero-arg async callable to execute.
        max_attempts: total tries including the first.
        initial_delay: first backoff in seconds.
        max_delay: cap per-attempt delay.
        jitter: ±fraction applied to each sleep (0.25 = ±25%).
        exceptions: which exceptions to retry on; others propagate immediately.
        op_name: label used in warning logs.

    Raises:
        Last caught exception if all attempts exhausted.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = max(0.0, float(initial_delay))
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except exceptions as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            sleep_for = min(delay, max_delay)
            sleep_for *= 1.0 + random.uniform(-jitter, jitter)
            sleep_for = max(0.05, sleep_for)
            logger.warning(
                "[retry] %s attempt %d/%d failed: %s; sleep=%.2fs",
                op_name, attempt, max_attempts, exc, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay = max(delay * 2, initial_delay)

    assert last_exc is not None
    raise last_exc
