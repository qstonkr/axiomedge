"""Slack Web API client — async httpx with rate-limit aware retry.

Slack rate-limit: tier 별 다름 — ``conversations.history`` 는 Tier 3 (50/min).
429 응답에 ``Retry-After`` 헤더 포함. MVP 에서는 1회 retry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://slack.com/api"
_DEFAULT_TIMEOUT = 30.0


class SlackAPIError(RuntimeError):
    """Slack API 호출 실패 — Slack ``error`` 코드 포함."""

    def __init__(self, message: str, code: str = "", channel: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.channel = channel


class SlackClient:
    """Slack Web API thin wrapper — channel message + thread + user."""

    def __init__(self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not auth_token:
            raise ValueError("slack auth_token is empty")
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL, headers=self._headers, timeout=timeout,
        )
        # username cache — same channel 의 mention 재조회 절약.
        self._user_cache: dict[str, str] = {}

    async def __aenter__(self) -> SlackClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Slack 의 모든 method 는 form-encoded POST 또는 GET — POST 통일."""
        for attempt in range(2):
            try:
                resp = await self._client.post(f"/{method}", data=params)
            except httpx.TimeoutException as e:
                raise SlackAPIError(f"timeout calling {method}: {e}") from e
            except httpx.RequestError as e:
                raise SlackAPIError(f"network error calling {method}: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("slack rate-limited (%s), sleeping %.1fs", method, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                raise SlackAPIError(
                    f"slack {method} HTTP {resp.status_code}: {resp.text[:200]}",
                )

            data: dict[str, Any] = resp.json()
            if not data.get("ok"):
                raise SlackAPIError(
                    f"slack {method} failed: {data.get('error', 'unknown')}",
                    code=str(data.get("error") or ""),
                )
            return data
        raise SlackAPIError(f"slack {method}: max retries exceeded")

    async def conversations_history(
        self,
        channel: str,
        *,
        oldest: float | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"channel": channel, "limit": limit}
        if oldest is not None:
            params["oldest"] = f"{oldest:.6f}"
        if cursor:
            params["cursor"] = cursor
        return await self._post("conversations.history", params)

    async def conversations_replies(
        self, channel: str, ts: str, *, limit: int = 200,
    ) -> dict[str, Any]:
        return await self._post(
            "conversations.replies",
            {"channel": channel, "ts": ts, "limit": limit},
        )

    async def conversations_info(self, channel: str) -> dict[str, Any]:
        return await self._post("conversations.info", {"channel": channel})

    async def users_info(self, user_id: str) -> str:
        """Cache-aware username lookup. ``Unknown`` if API fails."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            data = await self._post("users.info", {"user": user_id})
            user = data.get("user") or {}
            display = (
                (user.get("profile") or {}).get("display_name")
                or user.get("real_name")
                or user.get("name")
                or user_id
            )
            self._user_cache[user_id] = display
            return display
        except SlackAPIError as e:
            logger.debug("slack users.info failed for %s: %s", user_id, e)
            self._user_cache[user_id] = user_id
            return user_id
