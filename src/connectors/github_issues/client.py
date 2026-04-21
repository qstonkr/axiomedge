"""GitHub REST API v3 client — async httpx + Link header paging.

Auth: ``Authorization: Bearer {token}`` (PAT). 429/secondary rate limit 시
``Retry-After`` 또는 ``X-RateLimit-Reset`` 존중 (1회 retry).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubAPIError(RuntimeError):
    """GitHub REST API 호출 실패 — status 포함."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    """GitHub REST API thin wrapper — async + Link-header paging."""

    def __init__(
        self,
        auth_token: str,
        *,
        api_base_url: str = "https://api.github.com",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("GitHubClient requires non-empty auth_token")
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._client = httpx.AsyncClient(
            base_url=api_base_url, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_with_retry(
        self, method: str, url: str, **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(2):
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as e:
                raise GitHubAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise GitHubAPIError(f"network error: {e}") from e

            # Primary rate limit (429) or secondary (403 with X-RateLimit-Remaining=0)
            if attempt == 0 and (
                resp.status_code == 429
                or (resp.status_code == 403
                    and resp.headers.get("X-RateLimit-Remaining") == "0")
            ):
                wait = float(resp.headers.get("Retry-After", "1"))
                if wait < 0.5 and "X-RateLimit-Reset" in resp.headers:
                    try:
                        reset_ts = int(resp.headers["X-RateLimit-Reset"])
                        wait = max(1.0, reset_ts - time.time())
                    except (TypeError, ValueError):
                        pass
                wait = min(wait, 60.0)  # cap at 60s
                logger.warning(
                    "github rate-limited (%s), sleeping %.1fs", url, wait,
                )
                await asyncio.sleep(wait)
                continue
            return resp
        # 위 loop 가 retry 끝나면 마지막 resp 그대로 반환됐으므로 unreachable.
        raise GitHubAPIError("github: max retries exceeded")

    async def _get_json(self, path: str, **kwargs: Any) -> Any:
        resp = await self._request_with_retry("GET", path, **kwargs)
        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except ValueError:
                return None
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        msg = str(payload.get("message") or resp.text[:200])
        raise GitHubAPIError(f"github {resp.status_code}: {msg}", status=resp.status_code)

    async def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "all",
        since: str | None = None,
        per_page: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """``GET /repos/{owner}/{repo}/issues`` — Link header paging."""
        path = f"/repos/{owner}/{repo}/issues"
        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if since:
            params["since"] = since

        # 첫 page
        resp = await self._request_with_retry("GET", path, params=params)
        while True:
            if not (200 <= resp.status_code < 300):
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {}
                msg = str(payload.get("message") or resp.text[:200])
                raise GitHubAPIError(
                    f"github {resp.status_code}: {msg}", status=resp.status_code,
                )
            try:
                items = resp.json()
            except ValueError:
                items = []
            if not isinstance(items, list):
                items = []
            for it in items:
                yield it

            link = resp.headers.get("Link", "")
            m = _LINK_NEXT_RE.search(link)
            if not m:
                break
            next_url = m.group(1)
            # next_url 은 absolute URL — base_url 무시하고 직접 호출.
            resp = await self._request_with_retry("GET", next_url)

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int, *, per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """1 issue 의 모든 comment paged collect."""
        out: list[dict[str, Any]] = []
        path = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        resp = await self._request_with_retry(
            "GET", path, params={"per_page": per_page},
        )
        while True:
            if not (200 <= resp.status_code < 300):
                break
            try:
                items = resp.json()
            except ValueError:
                items = []
            if isinstance(items, list):
                out.extend(items)
            link = resp.headers.get("Link", "")
            m = _LINK_NEXT_RE.search(link)
            if not m:
                break
            resp = await self._request_with_retry("GET", m.group(1))
        return out
