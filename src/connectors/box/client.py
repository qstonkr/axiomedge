"""Box API v2 client — async httpx + offset paging + 다운로드 redirect.

F3 (L-3 Phase 2): ``BaseConnectorClient`` 표준 base 로 리팩터링. retry,
``Retry-After`` aware backoff, keep-alive pool, 401/403 헤더 마스킹은 base
가 담당하고 본 모듈은 Box 특화 path / error mapping / file content
streaming 만 책임.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.box.com/2.0"
_DEFAULT_TIMEOUT = 30.0


class BoxAPIError(RuntimeError):
    """Box API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class BoxClient(BaseConnectorClient):
    """Box API v2 thin wrapper — folders/items + files/content."""

    def __init__(
        self,
        auth_token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = 8,
    ) -> None:
        if not auth_token:
            raise ValueError("BoxClient requires non-empty auth_token")
        config = BaseConnectorConfig(
            auth_token=auth_token,
            timeout_seconds=timeout,
            max_concurrent=max_concurrent,
        )
        super().__init__(
            base_url=_BASE_URL,
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

    async def _box_request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        await self._ensure_client()
        try:
            resp = await self._request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise BoxAPIError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            try:
                payload = e.response.json()
            except ValueError:
                payload = {}
            raise BoxAPIError(
                f"box {e.response.status_code}: "
                f"{payload.get('message', e.response.text[:200])}",
                status=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise BoxAPIError(f"network error: {e}") from e

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
        msg = str(payload.get("message") or resp.text[:200])
        raise BoxAPIError(
            f"box {resp.status_code}: {msg}", status=resp.status_code,
        )

    async def folder_items(
        self,
        folder_id: str,
        *,
        fields: str = "id,type,name,size,modified_at,created_by",
    ) -> AsyncIterator[dict[str, Any]]:
        """``/folders/{id}/items`` — offset/limit paging. 모든 item yield."""
        offset = 0
        limit = 100
        while True:
            page = await self._box_request(
                "GET", f"/folders/{folder_id}/items",
                params={"offset": offset, "limit": limit, "fields": fields},
            )
            entries = page.get("entries") or []
            for entry in entries:
                yield entry
            total = int(page.get("total_count") or 0)
            offset += len(entries)
            if not entries or offset >= total:
                break

    async def file_content(self, file_id: str) -> bytes:
        """``/files/{id}/content`` — 보통 302 redirect to download URL.

        BaseConnectorClient 의 _request 는 retry/429 backoff 까지 자동.
        download 는 일반 GET 으로 호출하되 ``follow_redirects=True`` 적용.
        """
        await self._ensure_client()
        url = f"/files/{file_id}/content"
        try:
            assert self._client is not None
            resp = await self._client.get(url, follow_redirects=True)
        except httpx.RequestError as e:
            raise BoxAPIError(f"download network error: {e}") from e
        if resp.status_code != 200:
            raise BoxAPIError(
                f"box download {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp.content
