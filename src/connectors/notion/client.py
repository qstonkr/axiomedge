"""Notion API v1 client — page metadata + block children pagination.

요청은 모두 async (httpx.AsyncClient). retry/backoff 는 일단 단순 — Notion
은 rate-limit 시 ``Retry-After`` 헤더 + 429 status 로 응답하므로 호출자가
sleep 후 재시도. MVP 에서는 1회 retry만 (코드 단순성).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_DEFAULT_TIMEOUT = 30.0


class NotionAPIError(RuntimeError):
    """Notion API 호출 실패 — status / Notion error code 포함."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NotionClient:
    """Stateless wrapper around Notion API v1 — async with httpx.

    한 connector run 내내 동일 토큰 사용 — 인스턴스 1개 재사용. ``aclose()``
    로 underlying httpx client 정리 (또는 ``async with`` context manager).
    """

    def __init__(self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not auth_token:
            raise ValueError("notion auth_token is empty")
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> NotionClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Single request with 1 retry on 429 (Retry-After respected)."""
        for attempt in range(2):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                raise NotionAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise NotionAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("notion rate-limited, sleeping %.1fs", wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                return resp.json()

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            code = str(payload.get("code") or "")
            message = str(payload.get("message") or resp.text[:200])
            raise NotionAPIError(
                f"notion API {resp.status_code} ({code}): {message}",
                status=resp.status_code,
                code=code,
            )
        # unreachable — loop returns or raises
        raise NotionAPIError("notion API: max retries exceeded")

    async def get_page(self, page_id: str) -> dict[str, Any]:
        """``GET /pages/{id}`` — page metadata + properties."""
        return await self._request("GET", f"/pages/{page_id}")

    async def get_block_children(
        self, block_id: str, *, start_cursor: str | None = None, page_size: int = 100,
    ) -> dict[str, Any]:
        """``GET /blocks/{id}/children`` — paginated."""
        params: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return await self._request("GET", f"/blocks/{block_id}/children", params=params)

    async def list_all_blocks(
        self, block_id: str, *, page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """모든 자식 블록을 page-loop 로 수집 후 반환 (next_cursor 자동 처리)."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = await self.get_block_children(
                block_id, start_cursor=cursor, page_size=page_size,
            )
            out.extend(page.get("results") or [])
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return out
