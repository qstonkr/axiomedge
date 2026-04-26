"""Asana REST API client — A2 (BaseConnectorClient 마이그레이션).

Auth: ``Authorization: Bearer {pat}`` (base 가 자동 prefix).
Paging: response 의 ``next_page.offset`` 따라감.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://app.asana.com/api/1.0"
_DEFAULT_TIMEOUT = 30.0


class AsanaAPIError(RuntimeError):
    """Asana API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class AsanaClient(BaseConnectorClient):
    """Asana REST API thin wrapper."""

    def __init__(
        self,
        auth_token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = 8,
    ) -> None:
        if not auth_token:
            raise ValueError("AsanaClient requires non-empty auth_token")
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

    async def _asana_request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict[str, Any]:
        await self._ensure_client()
        try:
            resp = await self._request(method, path, **kwargs)
        except httpx.TimeoutException as e:
            raise AsanaAPIError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            try:
                payload = e.response.json()
            except ValueError:
                payload = {}
            errs = payload.get("errors") or [
                {"message": e.response.text[:200]}
            ]
            msg = "; ".join(
                str(d.get("message") or "")
                for d in errs if isinstance(d, dict)
            )
            raise AsanaAPIError(
                f"asana {e.response.status_code}: {msg}",
                status=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise AsanaAPIError(f"network error: {e}") from e

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
        msg = "; ".join(
            str(d.get("message") or "")
            for d in errs if isinstance(d, dict)
        )
        raise AsanaAPIError(
            f"asana {resp.status_code}: {msg}", status=resp.status_code,
        )

    async def iterate_pages(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Asana paging — response 의 ``next_page.offset`` 따라감."""
        page_params = dict(params or {})
        page_params.setdefault("limit", 100)
        while True:
            page = await self._asana_request("GET", path, params=page_params)
            for item in page.get("data") or []:
                yield item
            next_page = page.get("next_page") or {}
            offset = (
                next_page.get("offset")
                if isinstance(next_page, dict) else None
            )
            if not offset:
                break
            page_params["offset"] = offset
