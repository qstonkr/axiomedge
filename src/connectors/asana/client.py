"""Asana REST API client — async httpx + offset paging.

Auth: ``Authorization: Bearer {pat}``. paging via ``offset`` field in
``next_page`` of response.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://app.asana.com/api/1.0"
_DEFAULT_TIMEOUT = 30.0


class AsanaAPIError(RuntimeError):
    """Asana API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class AsanaClient:
    """Asana REST API thin wrapper."""

    def __init__(
        self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("AsanaClient requires non-empty auth_token")
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> AsanaClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                raise AsanaAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise AsanaAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("asana rate-limited (%s), sleeping %.1fs", path, wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {}

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            errs = payload.get("errors") or [{"message": resp.text[:200]}]
            msg = "; ".join(str(e.get("message") or "") for e in errs if isinstance(e, dict))
            raise AsanaAPIError(
                f"asana {resp.status_code}: {msg}", status=resp.status_code,
            )
        raise AsanaAPIError("asana: max retries exceeded")

    async def iterate_pages(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Asana paging — response 의 ``next_page.offset`` 따라감."""
        page_params = dict(params or {})
        page_params.setdefault("limit", 100)
        while True:
            page = await self._request("GET", path, params=page_params)
            for item in page.get("data") or []:
                yield item
            next_page = page.get("next_page") or {}
            offset = next_page.get("offset") if isinstance(next_page, dict) else None
            if not offset:
                break
            page_params["offset"] = offset
