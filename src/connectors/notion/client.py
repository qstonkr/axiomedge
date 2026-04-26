"""Notion API v1 client — page metadata + block children pagination.

P1-9 (L-3 Phase 1): ``BaseConnectorClient`` 표준 base 로 리팩터링. retry,
rate-limit-aware backoff, connection pool 라이프사이클을 base 가 담당하고
이 모듈은 Notion-특화 path / error mapping 만 책임진다.

호환성: 외부 호출 시그니처 (``NotionClient(auth_token)``, ``get_page``,
``get_block_children``, ``list_all_blocks``, ``aclose``, ``async with``)
는 그대로 유지된다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig

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


class NotionClient(BaseConnectorClient):
    """Stateless wrapper around Notion API v1.

    P1-9: BaseConnectorClient 가 retry / 429 ``Retry-After`` / 5xx backoff /
    keep-alive pool 을 일괄 처리. Notion-Version 헤더 + Bearer 토큰만 본 클래스
    가 추가로 세팅한다.
    """

    def __init__(
        self,
        auth_token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = 8,
    ) -> None:
        if not auth_token:
            raise ValueError("notion auth_token is empty")
        config = BaseConnectorConfig(
            auth_token=auth_token,
            timeout_seconds=timeout,
            max_concurrent=max_concurrent,
        )
        super().__init__(
            base_url=_BASE_URL,
            config=config,
            default_headers={
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    # __aenter__ / __aexit__ 는 BaseConnectorClient 가 제공.

    async def aclose(self) -> None:
        """Backward-compat — base 의 __aexit__ 와 동일한 close 동작."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """``async with NotionClient(...)`` 미사용 시에도 client 가 살아있도록.

        BaseConnectorClient 는 컨텍스트 매니저 진입 시 self._client 를
        만든다. 기존 NotionClient 호출자는 직접 인스턴스화 후 메서드를
        호출하는 패턴이므로 lazy init 으로 호환성 유지.
        """
        if self._client is None:
            await self.__aenter__()
        # type: ignore — None check 위에서 끝남
        return self._client  # type: ignore[return-value]

    async def _notion_request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        """Notion-특화 wrapper: base ``_request`` 를 호출하고 응답을 파싱.

        - timeout / network → ``NotionAPIError``
        - 4xx (401/403 외) / 5xx / 429-after-retry → ``NotionAPIError`` 매핑
        - 2xx → JSON dict 반환
        """
        await self._ensure_client()
        try:
            resp = await self._request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise NotionAPIError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            # base 가 401/403 시 raise — Notion 도메인 에러로 매핑
            try:
                payload = e.response.json()
            except ValueError:
                payload = {}
            raise NotionAPIError(
                f"notion API {e.response.status_code} "
                f"({payload.get('code', '')}): "
                f"{payload.get('message', '')[:200]}",
                status=e.response.status_code,
                code=str(payload.get("code") or ""),
            ) from e
        except httpx.RequestError as e:
            raise NotionAPIError(f"network error: {e}") from e

        if 200 <= resp.status_code < 300:
            return resp.json()

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        raise NotionAPIError(
            f"notion API {resp.status_code} ({payload.get('code', '')}): "
            f"{payload.get('message') or resp.text[:200]}",
            status=resp.status_code,
            code=str(payload.get("code") or ""),
        )

    async def get_page(self, page_id: str) -> dict[str, Any]:
        """``GET /pages/{id}`` — page metadata + properties."""
        return await self._notion_request("GET", f"/pages/{page_id}")

    async def get_block_children(
        self,
        block_id: str,
        *,
        start_cursor: str | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """``GET /blocks/{id}/children`` — paginated."""
        params: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return await self._notion_request(
            "GET", f"/blocks/{block_id}/children", params=params,
        )

    async def list_all_blocks(
        self, block_id: str, *, page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """모든 자식 블록을 next_cursor 자동 처리로 수집."""
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
