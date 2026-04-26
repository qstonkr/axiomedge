"""Microsoft Graph API base client — async httpx + paging + throttle.

A1 (L-3 Phase 2 — OneDrive 트랙): ``BaseConnectorClient`` 표준 base 로 마이
그레이션. retry, ``Retry-After`` aware backoff, keep-alive pool, 401/403
헤더 마스킹은 base 가 담당하고 본 모듈은 Microsoft Graph 특화 paging
(``@odata.nextLink``) 과 error mapping 만 책임.

지원 기능:
- Bearer token auth (admin app-only or delegated)
- Auto-paging via ``@odata.nextLink`` (``iterate_pages()`` async generator)
- 429 retry with ``Retry-After`` header (base 가 처리)
- 공통 exception (``MSGraphAPIError``) — 호출자가 status/code 분기
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://graph.microsoft.com/v1.0"
_DEFAULT_TIMEOUT = 30.0


class MSGraphAPIError(RuntimeError):
    """Microsoft Graph API 호출 실패 — status + Graph error code 포함."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class MSGraphClient(BaseConnectorClient):
    """Stateless Microsoft Graph wrapper — 한 connector run 내 instance 1개 재사용.

    인증: Bearer token (admin app-only 또는 user delegated). 토큰 갱신은 호출자
    책임 — 본 client 는 주어진 token 그대로 사용. 만료되면 401 → 즉시
    ``MSGraphAPIError`` (base 의 401/403 즉시 raise + header 마스킹).
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = 8,
    ) -> None:
        if not access_token:
            raise ValueError("MSGraphClient requires non-empty access_token")
        config = BaseConnectorConfig(
            auth_token=access_token,
            timeout_seconds=timeout,
            max_concurrent=max_concurrent,
        )
        super().__init__(
            base_url=base_url,
            config=config,
            default_headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> None:
        if self._client is None:
            await self.__aenter__()

    async def _graph_request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        """Graph 특화 wrapper — base 의 ``_request`` 결과를 dict 로 반환."""
        await self._ensure_client()
        try:
            resp = await self._request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise MSGraphAPIError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            try:
                payload = e.response.json()
            except ValueError:
                payload = {}
            err = payload.get("error") or {}
            raise MSGraphAPIError(
                f"msgraph {e.response.status_code} "
                f"({err.get('code', '')}): "
                f"{err.get('message', e.response.text[:200])}",
                status=e.response.status_code,
                code=str(err.get("code") or ""),
            ) from e
        except httpx.RequestError as e:
            raise MSGraphAPIError(f"network error: {e}") from e

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

    async def get(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._graph_request("GET", path, params=params)

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
                page = await self._graph_request(
                    "GET", next_url, params=params,
                )
                first_call = False
            else:
                # @odata.nextLink 는 absolute URL — httpx 자동 처리.
                page = await self._graph_request("GET", next_url)
            for item in page.get("value") or []:
                yield item
            next_url = page.get("@odata.nextLink")
