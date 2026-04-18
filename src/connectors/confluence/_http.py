# pyright: reportAttributeAccessIssue=false
"""HTTP transport helpers for the Confluence crawling client.

Provides :class:`HttpMixin` with retry-aware HTTP GET and connection
lifecycle management.  Extracted from ``client.py`` for SRP.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class HttpMixin:
    """HTTP helpers mixed into :class:`ConfluenceFullClient`.

    Host class must provide: ``client`` (:class:`httpx.AsyncClient`),
    ``shutdown_requested`` (bool property).
    """

    _RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    async def close(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Retry machinery
    # ------------------------------------------------------------------

    @staticmethod
    async def _backoff_and_log(
        error: Exception,
        attempt: int,
        max_retries: int,
        url: str,
        base_delay: int,
        max_delay: int,
    ) -> None:
        """Log a retry warning and sleep with exponential backoff."""
        if attempt >= max_retries - 1:
            return
        wait = min(2**attempt * base_delay, max_delay)
        label = (
            f"HTTP {error.response.status_code}"  # type: ignore[union-attr]
            if isinstance(error, httpx.HTTPStatusError)
            else type(error).__name__
        )
        logger.warning("%s retry %d/%d: %s", label, attempt + 1, max_retries, url[:80])
        await asyncio.sleep(wait)

    async def _http_get_with_retry(
        self,
        url: str,
        params: dict | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """HTTP GET with exponential backoff for transient errors."""
        last_error: Exception | None = None
        for attempt in range(max_retries):
            if self.shutdown_requested:
                raise RuntimeError("Shutdown requested during HTTP retry")
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.PoolTimeout,
            ) as e:
                last_error = e
                await self._backoff_and_log(e, attempt, max_retries, url, 2, 30)
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in self._RETRYABLE_STATUS_CODES:
                    raise
                last_error = e
                await self._backoff_and_log(e, attempt, max_retries, url, 5, 60)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unreachable: HTTP retry exhausted without error")
