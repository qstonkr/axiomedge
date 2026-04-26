"""Linear GraphQL client — async httpx + cursor pagination.

F3 (L-3 Phase 2): ``BaseConnectorClient`` 표준 base 로 리팩터링.

Linear API:
- POST https://api.linear.app/graphql with ``{query, variables}`` body
- Auth: ``Authorization: {api_key}`` (Bearer prefix 없음 — raw key)
  → BaseConnectorConfig 의 auth_token 을 그대로 사용하지 않고 default header
    에서 직접 세팅 (base 의 ``Bearer ...`` 자동 prefix 회피).
- Paging: ``pageInfo { hasNextPage endCursor }`` — Relay-style cursor
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.linear.app/graphql"
_BASE_URL = "https://api.linear.app"
_DEFAULT_TIMEOUT = 30.0


class LinearAPIError(RuntimeError):
    """Linear API 호출 실패 — GraphQL errors 포함."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class LinearClient(BaseConnectorClient):
    """Linear GraphQL thin wrapper."""

    def __init__(
        self,
        auth_token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = 8,
    ) -> None:
        if not auth_token:
            raise ValueError("LinearClient requires non-empty auth_token")
        # Linear 는 Bearer prefix 없이 raw key 사용. base 가 자동 추가하지
        # 않도록 auth_token 을 None 으로 두고 default_headers 에서 세팅.
        config = BaseConnectorConfig(
            auth_token=None,
            timeout_seconds=timeout,
            max_concurrent=max_concurrent,
        )
        super().__init__(
            base_url=_BASE_URL,
            config=config,
            default_headers={
                "Authorization": auth_token,
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> None:
        if self._client is None:
            await self.__aenter__()

    async def query(
        self, query: str, variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run GraphQL query — base 가 retry/429/Retry-After 처리."""
        await self._ensure_client()
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables

        try:
            resp = await self._request("POST", "/graphql", json=body)
        except httpx.TimeoutException as e:
            raise LinearAPIError(f"timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            raise LinearAPIError(
                f"linear HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}",
                status=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise LinearAPIError(f"network error: {e}") from e

        if resp.status_code != 200:
            raise LinearAPIError(
                f"linear HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )

        payload = resp.json()
        if payload.get("errors"):
            errs = payload["errors"]
            msg = "; ".join(
                str(e.get("message") or "")
                for e in errs if isinstance(e, dict)
            )
            raise LinearAPIError(f"linear GraphQL errors: {msg}")
        return payload.get("data") or {}
