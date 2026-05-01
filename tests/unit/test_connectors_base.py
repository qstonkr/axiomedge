"""BaseConnectorClient — PR-14 (L) Phase 1.

핵심 동작 검증:
- 401/403 즉시 raise
- 429 retry-after 존중
- 5xx 지수 백오프
- 정상 응답은 retry 없이 반환
"""

from __future__ import annotations

import httpx
import pytest

from src.connectors._base import BaseConnectorClient, BaseConnectorConfig


class _StubClient(BaseConnectorClient):
    """Concrete subclass — abstract 상속 검증용."""


def _mock_transport(responses):
    """Sequence of (status_code, headers, json_body) → MockTransport."""
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            status, headers, body = next(iterator)
        except StopIteration:
            return httpx.Response(500, json={"detail": "exhausted"})
        return httpx.Response(
            status, headers=dict(headers or {}), json=body,
        )

    return httpx.MockTransport(handler)


class TestRequestRetries:
    @pytest.mark.asyncio
    async def test_returns_immediately_on_2xx(self, monkeypatch):
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        cfg = BaseConnectorConfig(max_concurrent=2)
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([(200, {}, {"ok": 1})]),
            )
            resp = await c._request("GET", "/v")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_raises_on_401(self, monkeypatch):
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        cfg = BaseConnectorConfig()
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([
                    (401, {}, {"error": "unauth"}),
                ]),
            )
            with pytest.raises(httpx.HTTPStatusError):
                await c._request("GET", "/v")

    @pytest.mark.asyncio
    async def test_401_masks_sensitive_request_headers(self, monkeypatch):
        """P2-2: HTTPStatusError 의 request 객체에서 Authorization 등 마스킹."""
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        cfg = BaseConnectorConfig(auth_token="super-secret-token-xyz")
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([
                    (401, {}, {"error": "unauth"}),
                ]),
                headers={"Authorization": "Bearer super-secret-token-xyz",
                         "X-API-Key": "key-123"},
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await c._request("GET", "/v")

            req = exc_info.value.request
            # 마스킹된 request 객체에 토큰 흔적 없음
            joined_headers = " ".join(
                f"{k}={v}" for k, v in req.headers.items()
            )
            assert "super-secret-token" not in joined_headers
            assert "key-123" not in joined_headers
            assert "<MASKED>" in joined_headers

    @pytest.mark.asyncio
    async def test_retries_5xx_then_succeeds(self, monkeypatch):
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        cfg = BaseConnectorConfig()
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([
                    (500, {}, {}),
                    (502, {}, {}),
                    (200, {}, {"ok": 1}),
                ]),
            )
            resp = await c._request(
                "GET", "/v", retries=3, initial_backoff=0.01,
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_429_respects_retry_after(self, monkeypatch):
        sleeps: list[float] = []

        async def _capture_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr("asyncio.sleep", _capture_sleep)

        cfg = BaseConnectorConfig()
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([
                    (429, {"Retry-After": "2"}, {}),
                    (200, {}, {"ok": 1}),
                ]),
            )
            resp = await c._request(
                "GET", "/v", retries=2, initial_backoff=0.5,
            )
            assert resp.status_code == 200
        # Retry-After=2 was respected (jitter ±10% so within [1.8, 2.2])
        assert sleeps and 1.5 <= sleeps[0] <= 2.5

    @pytest.mark.asyncio
    async def test_request_raises_when_client_not_entered(self):
        """async-with 안 거치면 self._client = None → RuntimeError."""
        cfg = BaseConnectorConfig()
        c = _StubClient(base_url="https://api.x", config=cfg)
        # Did not enter async with — _client is still None
        with pytest.raises(RuntimeError, match="async context manager"):
            await c._request("GET", "/v")

    @pytest.mark.asyncio
    async def test_retry_after_invalid_value_falls_back(self, monkeypatch):
        """Retry-After 가 숫자 아니면 (예: HTTP-date) fallback 사용."""
        sleeps: list[float] = []

        async def _capture_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr("asyncio.sleep", _capture_sleep)

        cfg = BaseConnectorConfig()
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([
                    (429, {"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"}, {}),
                    (200, {}, {"ok": 1}),
                ]),
            )
            resp = await c._request(
                "GET", "/v", retries=2, initial_backoff=0.3,
            )
            assert resp.status_code == 200
        # Falls back to initial_backoff (~0.3s, not the date value)
        assert sleeps and sleeps[0] < 1.0

    @pytest.mark.asyncio
    async def test_5xx_retries_exhausted_returns_last(self, monkeypatch):
        """5xx 가 재시도 한도 초과하면 마지막 응답 반환 (raise 없이)."""
        async def _no_sleep(_):
            return None
        monkeypatch.setattr("asyncio.sleep", _no_sleep)

        cfg = BaseConnectorConfig()
        async with _StubClient(base_url="https://api.x", config=cfg) as c:
            c._client = httpx.AsyncClient(
                base_url="https://api.x",
                transport=_mock_transport([(503, {}, {}), (503, {}, {})]),
            )
            resp = await c._request(
                "GET", "/v", retries=2, initial_backoff=0.01,
            )
            assert resp.status_code == 503
