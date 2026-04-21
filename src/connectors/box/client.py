"""Box API v2 client — async httpx + offset paging + 다운로드 redirect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.box.com/2.0"
_DEFAULT_TIMEOUT = 30.0


class BoxAPIError(RuntimeError):
    """Box API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class BoxClient:
    """Box API v2 thin wrapper — folders/items + files/content."""

    def __init__(
        self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("BoxClient requires non-empty auth_token")
        self._token = auth_token
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> BoxClient:
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
                raise BoxAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise BoxAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("box rate-limited (%s), sleeping %.1fs", path, wait)
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
            msg = str(payload.get("message") or resp.text[:200])
            raise BoxAPIError(
                f"box {resp.status_code}: {msg}", status=resp.status_code,
            )
        raise BoxAPIError("box: max retries exceeded")

    async def folder_items(
        self, folder_id: str, *, fields: str = "id,type,name,size,modified_at,created_by",
    ) -> AsyncIterator[dict[str, Any]]:
        """``/folders/{id}/items`` — offset/limit paging. 모든 item yield."""
        offset = 0
        limit = 100
        while True:
            page = await self._request(
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

        httpx 의 ``follow_redirects=True`` 로 자동 처리. raw bytes 반환.
        """
        url = f"/files/{file_id}/content"
        try:
            resp = await self._client.get(url, follow_redirects=True)
        except httpx.RequestError as e:
            raise BoxAPIError(f"download network error: {e}") from e
        if resp.status_code != 200:
            raise BoxAPIError(
                f"box download {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp.content
