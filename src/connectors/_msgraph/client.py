"""Microsoft Graph API base client — async httpx + paging + throttle.

지원 기능:
- Bearer token auth (admin app-only or delegated)
- Auto-paging via ``@odata.nextLink`` (``iterate_pages()`` async generator)
- 429 retry with ``Retry-After`` header
- 공통 exception (``MSGraphAPIError``) — 호출자가 status/code 분기
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://graph.microsoft.com/v1.0"
_DEFAULT_TIMEOUT = 30.0


class MSGraphAPIError(RuntimeError):
    """Microsoft Graph API 호출 실패 — status + Graph error code 포함."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class MSGraphClient:
    """Stateless Microsoft Graph wrapper — 한 connector run 내 instance 1개 재사용.

    인증: Bearer token (admin app-only 또는 user delegated). 토큰 갱신은 호출자
    책임 — 본 client 는 주어진 token 그대로 사용. 만료되면 401 → MSGraphAPIError.
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not access_token:
            raise ValueError("MSGraphClient requires non-empty access_token")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> MSGraphClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        """1회 요청 + 1회 retry on 429 (Retry-After 존중)."""
        for attempt in range(2):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                raise MSGraphAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise MSGraphAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("msgraph rate-limited (%s), sleeping %.1fs", path, wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                return resp.json()

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            err = payload.get("error") or {}
            code = str(err.get("code") or "")
            message = str(err.get("message") or resp.text[:200])
            raise MSGraphAPIError(
                f"msgraph {resp.status_code} ({code}): {message}",
                status=resp.status_code, code=code,
            )
        raise MSGraphAPIError("msgraph: max retries exceeded")

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def iterate_pages(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """``@odata.nextLink`` 자동 따라가며 모든 page item yield.

        OData v4 페이징 — 첫 응답의 ``value`` 배열 + ``@odata.nextLink`` 가
        다음 page absolute URL. 따라서 다음 호출은 path 가 아니라 full URL.
        """
        next_url: str | None = path
        first_call = True
        while next_url:
            if first_call:
                page = await self._request("GET", next_url, params=params)
                first_call = False
            else:
                # @odata.nextLink 는 absolute URL — base_url 무시하고 직접 호출.
                # httpx 가 absolute URL 자동 처리.
                page = await self._request("GET", next_url)
            for item in page.get("value") or []:
                yield item
            next_url = page.get("@odata.nextLink")
