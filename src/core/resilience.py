"""Centralized retry + circuit-breaker patterns for external dependencies.

외부 의존(TEI, SageMaker, Qdrant, Ollama, Confluence …) 호출이 transient
에러(ConnectError, ReadTimeout 등)에 부딪힐 때 exponential backoff 로 재시도.

Usage:
    from src.core.resilience import with_resilience
    from src.config.weights import weights

    @with_resilience("sagemaker", weights.resilience.sagemaker)
    async def invoke_endpoint(...):
        ...

설정은 ``src/config/weights/llm.py::ResilienceWeights`` 가 SSOT.
서비스별 max_attempts / backoff 가 다름.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


# Default transient exception set — extend per-service if needed
_DEFAULT_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    ConnectionError,
    TimeoutError,
)


@dataclass(frozen=True)
class ResilienceConfig:
    """Per-service retry policy.

    - ``max_attempts``: total tries (1 = no retry, 3 = 1 attempt + 2 retries)
    - ``initial_backoff_seconds``: first wait between retries
    - ``max_backoff_seconds``: cap on exponential backoff
    - ``retry_on``: exception types that trigger retry (default: HTTP transient)
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    retry_on: tuple[type[BaseException], ...] = field(
        default_factory=lambda: _DEFAULT_RETRY_EXCEPTIONS
    )


def with_resilience(
    service_name: str,
    config: ResilienceConfig | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Async decorator: retry transient failures with exponential backoff.

    Example:
        @with_resilience("ollama")
        async def generate(prompt: str) -> str:
            ...

    Logs a WARNING before each retry with attempt number and exception type.
    Re-raises the original exception after exhausting attempts (preserves
    stack trace for caller).
    """
    cfg = config or ResilienceConfig()

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(cfg.max_attempts),
                wait=wait_exponential(
                    multiplier=cfg.initial_backoff_seconds,
                    max=cfg.max_backoff_seconds,
                ),
                retry=retry_if_exception_type(cfg.retry_on),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    logger.debug(
                        "resilience: %s attempt %d/%d",
                        service_name,
                        attempt.retry_state.attempt_number,
                        cfg.max_attempts,
                    )
                    return await fn(*args, **kwargs)
            raise RuntimeError("unreachable: AsyncRetrying always returns or raises")

        return wrapper

    return decorator
