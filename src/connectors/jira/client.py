"""Jira REST API client — async httpx wrapper.

Auth 분기:
- ``email`` 있으면 Basic ``email:api_token`` (Cloud)
- ``email`` 없으면 Bearer (Server/DC PAT)

429 retry with Retry-After. Issue 검색은 ``GET /rest/api/{ver}/search`` —
JQL paged via ``startAt`` + ``maxResults`` (max 100).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class JiraAPIError(RuntimeError):
    """Jira REST API 호출 실패 — status 포함."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class JiraClient:
    """Async Jira REST API thin wrapper — search/issue/comments."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        *,
        email: str = "",
        api_version: str = "3",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("JiraClient requires non-empty auth_token")
        if not base_url:
            raise ValueError("JiraClient requires non-empty base_url")

        self._api_path = f"/rest/api/{api_version}"
        if email:
            # Cloud Basic auth — email:api_token
            creds = base64.b64encode(f"{email}:{auth_token}".encode()).decode("ascii")
            auth_header = f"Basic {creds}"
        else:
            # Server/DC PAT — Bearer
            auth_header = f"Bearer {auth_token}"

        self._headers = {
            "Authorization": auth_header,
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> JiraClient:
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
                raise JiraAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise JiraAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("jira rate-limited (%s), sleeping %.1fs", path, wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {"_raw": resp.text}

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            messages = payload.get("errorMessages") or [resp.text[:200]]
            msg = "; ".join(str(m) for m in messages)
            raise JiraAPIError(
                f"jira {resp.status_code}: {msg}", status=resp.status_code,
            )
        raise JiraAPIError("jira: max retries exceeded")

    async def search_issues(
        self,
        jql: str,
        *,
        fields: str = "summary,description,status,reporter,updated,comment",
        max_results: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """JQL 검색 — startAt 기반 paging, 모든 issue yield."""
        start_at = 0
        page_size = min(max_results, 100)
        while True:
            params: dict[str, Any] = {
                "jql": jql, "fields": fields,
                "startAt": start_at, "maxResults": page_size,
            }
            page = await self._request("GET", f"{self._api_path}/search", params=params)
            issues = page.get("issues") or []
            for issue in issues:
                yield issue
            total = int(page.get("total") or 0)
            start_at += len(issues)
            if not issues or start_at >= total:
                break
