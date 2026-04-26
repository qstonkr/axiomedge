"""Base connector client/config — PR-14 (L) Phase 1.

Notion/Box/Linear/OneDrive 등 다수 connector 가 매번 자체 httpx 라이프사이클
+ retry + paginate + rate-limit 처리를 중복 구현하던 부분을 표준화.

Phase 1: 본 helper 도입 + Notion 만 reference 마이그레이션 (별 PR).
Phase 2~3: 다른 connector 점진 적용.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC
from typing import Any, AsyncIterator

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BaseConnectorConfig(BaseModel):
    """공통 connector 설정 — auth + 동시성 + 격리."""

    auth_token: str | None = Field(default=None)
    organization_id: str | None = Field(default=None)
    kb_id: str | None = Field(default=None)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_concurrent: int = Field(default=8, ge=1, le=64)
    max_depth: int | None = Field(default=None, ge=1)


class BaseConnectorClient(ABC):
    """공통 httpx-기반 client 라이프사이클 + retry + paginate.

    구현체는 endpoint 별로 ``_request`` 를 호출하면 됨. 다음을 자동 처리:
    - 401/403 즉시 raise (retry 무의미)
    - 429: ``Retry-After`` 헤더 존중 + 지수 백오프
    - 5xx: 지수 백오프 (max_retries 기본 3)
    - keep-alive connection pool
    """

    def __init__(
        self,
        *,
        base_url: str,
        config: BaseConnectorConfig,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._config = config
        self._default_headers = dict(default_headers or {})
        if config.auth_token and "Authorization" not in self._default_headers:
            self._default_headers["Authorization"] = f"Bearer {config.auth_token}"
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(config.max_concurrent)

    async def __aenter__(self) -> "BaseConnectorClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=self._config.timeout_seconds,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=20, max_keepalive_connections=10,
            ),
            headers=self._default_headers,
        )
        return self

    async def __aexit__(self, *_args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _mask_sensitive_headers(
        request: httpx.Request,
    ) -> httpx.Request:
        """Replace sensitive headers in an outgoing request *copy* with masks.

        P2-2: ``HTTPStatusError(request=...)`` 가 호출자에게 propagate 될 때
        request.headers 가 그대로 logger 에 들어가면 Authorization Bearer
        토큰이 누설된다. 401/403 raise 직전 request 의 헤더만 마스킹한 새
        Request 객체를 만들어 raise 한다 (원본 request 는 보존 — pool 이
        keep-alive 로 재사용 가능).
        """
        sensitive = {"authorization", "cookie", "x-api-key", "x-auth-token"}
        masked_headers = httpx.Headers([
            (k, "<MASKED>") if k.lower() in sensitive else (k, v)
            for k, v in request.headers.items()
        ])
        return httpx.Request(
            method=request.method,
            url=request.url,
            headers=masked_headers,
            content=None,  # body 는 비움 — 민감 데이터 회피
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retries: int = 3,
        initial_backoff: float = 0.5,
        max_backoff: float = 30.0,
        **httpx_kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request with retry + rate-limit handling."""
        if self._client is None:
            raise RuntimeError(
                "BaseConnectorClient must be used as async context manager",
            )
        delay = initial_backoff
        last: httpx.Response | None = None
        for attempt in range(1, retries + 1):
            async with self._sem:
                resp = await self._client.request(
                    method, path, **httpx_kwargs,
                )
            # 4xx auth → 즉시 raise; 429 → backoff; 5xx → backoff; else return
            if resp.status_code in (401, 403):
                # P2-2: request 객체에 Authorization 헤더가 그대로 담겨 있어
                # logger.exception(...) 시 토큰이 누설된다. 마스킹된 사본으로
                # 교체하여 raise.
                masked_req = self._mask_sensitive_headers(resp.request)
                raise httpx.HTTPStatusError(
                    f"{method} {path} returned {resp.status_code}",
                    request=masked_req, response=resp,
                )
            if resp.status_code == 429:
                last = resp
                wait = self._rate_limit_wait(resp.headers, delay)
                logger.warning(
                    "[connector] 429 on %s; retry %d/%d after %.2fs",
                    path, attempt, retries, wait,
                )
                if attempt >= retries:
                    break
                await asyncio.sleep(min(wait, max_backoff))
                delay = min(delay * 2, max_backoff)
                continue
            if 500 <= resp.status_code < 600:
                last = resp
                logger.warning(
                    "[connector] %d on %s; retry %d/%d after %.2fs",
                    resp.status_code, path, attempt, retries, delay,
                )
                if attempt >= retries:
                    break
                await asyncio.sleep(min(delay, max_backoff))
                delay = min(delay * 2, max_backoff)
                continue
            return resp
        assert last is not None
        return last

    @staticmethod
    def _rate_limit_wait(headers: httpx.Headers, fallback: float) -> float:
        """``Retry-After`` 헤더 존중 + jitter."""
        ra = headers.get("Retry-After")
        if ra:
            try:
                wait = float(ra)
                return max(0.5, wait * (1.0 + random.uniform(-0.1, 0.1)))
            except ValueError:
                pass
        return fallback

    async def _paginate(
        self,
        method: str,
        path: str,
        *,
        next_key: str = "next_cursor",
        cursor_param: str = "start_cursor",
        max_pages: int = 1000,
        **httpx_kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Cursor pagination — yield 각 page body dict."""
        cursor: str | None = None
        for _ in range(max_pages):
            params = dict(httpx_kwargs.pop("params", {}) or {})
            if cursor:
                params[cursor_param] = cursor
            resp = await self._request(
                method, path, params=params, **httpx_kwargs,
            )
            if resp.status_code >= 400:
                resp.raise_for_status()
            body = resp.json()
            yield body
            cursor = body.get(next_key)
            if not cursor:
                return
