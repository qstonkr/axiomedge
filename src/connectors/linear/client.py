"""Linear GraphQL client — async httpx + cursor pagination.

Linear API:
- POST https://api.linear.app/graphql with ``{query, variables}`` body
- Auth: ``Authorization: {api_key}`` (Bearer prefix 없음 — 그냥 raw key)
- Paging: ``pageInfo { hasNextPage endCursor }`` — Relay-style cursor
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.linear.app/graphql"
_DEFAULT_TIMEOUT = 30.0


class LinearAPIError(RuntimeError):
    """Linear API 호출 실패 — GraphQL errors 포함."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class LinearClient:
    """Linear GraphQL thin wrapper."""

    def __init__(
        self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("LinearClient requires non-empty auth_token")
        # Linear 는 Bearer prefix 없이 raw key 사용 (이상한 컨벤션).
        self._headers = {
            "Authorization": auth_token,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> LinearClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def query(
        self, query: str, variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run GraphQL query — 1회 retry on 429."""
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables

        for attempt in range(2):
            try:
                resp = await self._client.post(_ENDPOINT, json=body)
            except httpx.TimeoutException as e:
                raise LinearAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise LinearAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.warning("linear rate-limited, sleeping %.1fs", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                raise LinearAPIError(
                    f"linear HTTP {resp.status_code}: {resp.text[:200]}",
                    status=resp.status_code,
                )

            payload = resp.json()
            if payload.get("errors"):
                errs = payload["errors"]
                msg = "; ".join(
                    str(e.get("message") or "") for e in errs if isinstance(e, dict)
                )
                raise LinearAPIError(f"linear GraphQL errors: {msg}")
            return payload.get("data") or {}

        raise LinearAPIError("linear: max retries exceeded")
