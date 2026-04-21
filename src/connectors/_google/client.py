"""Google API base client — async httpx + paging + throttle.

지원 기능:
- Bearer access_token (resolve_access_token 으로 service account/raw 모두 처리)
- Auto-paging via ``pageToken`` → ``nextPageToken`` (``iterate_pages()``)
- 429 retry with ``Retry-After`` 또는 default backoff
- 공통 exception (``GoogleAPIError``) — status/code 분기
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class GoogleAPIError(RuntimeError):
    """Google API 호출 실패 — status + Google error reason 포함."""

    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class GoogleClient:
    """Stateless Google API wrapper — 한 connector run 내 instance 1개 재사용.

    base_url 은 connector 별로 다름 (Drive/Sheets/Gmail) — 호출자가 ``base_url``
    인자 전달.
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not access_token:
            raise ValueError("GoogleClient requires non-empty access_token")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> GoogleClient:
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
                raise GoogleAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise GoogleAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("google rate-limited (%s), sleeping %.1fs", path, wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {"_raw_text": resp.text}

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            err = payload.get("error") or {}
            reason = ""
            if isinstance(err.get("errors"), list) and err["errors"]:
                reason = str((err["errors"][0] or {}).get("reason") or "")
            message = str(err.get("message") or resp.text[:200])
            raise GoogleAPIError(
                f"google {resp.status_code} ({reason}): {message}",
                status=resp.status_code, reason=reason,
            )
        raise GoogleAPIError("google: max retries exceeded")

    async def get(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def get_raw(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> bytes:
        """Binary fetch (Drive ``files.export`` 등). JSON 파싱 안 함."""
        try:
            resp = await self._client.get(path, params=params)
        except httpx.RequestError as e:
            raise GoogleAPIError(f"network error: {e}") from e
        if resp.status_code != 200:
            raise GoogleAPIError(
                f"google {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp.content

    async def iterate_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        items_key: str = "items",
    ) -> AsyncIterator[dict[str, Any]]:
        """``nextPageToken`` 따라가며 모든 page item yield.

        items_key 는 응답 안의 list field 이름 — Drive=files, Sheets=values 등.
        호출자가 endpoint 별로 적절한 값 전달.
        """
        page_params = dict(params or {})
        while True:
            page = await self._request("GET", path, params=page_params)
            for item in page.get(items_key) or []:
                yield item
            next_token = page.get("nextPageToken")
            if not next_token:
                break
            page_params["pageToken"] = next_token
