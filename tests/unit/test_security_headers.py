"""Tests for src/api/middleware/security_headers.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.middleware.security_headers import SecurityHeadersMiddleware


def _make_request() -> MagicMock:
    req = MagicMock()
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/health"
    return req


def _make_response() -> MagicMock:
    resp = MagicMock()
    resp.headers = {}
    return resp


@pytest.fixture
def middleware() -> SecurityHeadersMiddleware:
    app = MagicMock()
    return SecurityHeadersMiddleware(app)


@pytest.mark.asyncio
async def test_basic_headers(middleware: SecurityHeadersMiddleware) -> None:
    resp = _make_response()
    call_next = AsyncMock(return_value=resp)

    result = await middleware.dispatch(_make_request(), call_next)

    assert result.headers["X-Content-Type-Options"] == "nosniff"
    assert result.headers["X-Frame-Options"] == "DENY"
    assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert result.headers["X-XSS-Protection"] == "0"


@pytest.mark.asyncio
async def test_hsts_when_https_enabled(middleware: SecurityHeadersMiddleware) -> None:
    resp = _make_response()
    call_next = AsyncMock(return_value=resp)

    with patch.dict("os.environ", {"HTTPS_ENABLED": "true"}):
        result = await middleware.dispatch(_make_request(), call_next)

    assert "Strict-Transport-Security" in result.headers


@pytest.mark.asyncio
async def test_no_hsts_by_default(middleware: SecurityHeadersMiddleware) -> None:
    resp = _make_response()
    call_next = AsyncMock(return_value=resp)

    with patch.dict("os.environ", {}, clear=True):
        result = await middleware.dispatch(_make_request(), call_next)

    assert "Strict-Transport-Security" not in result.headers


@pytest.mark.asyncio
async def test_csp_when_set(middleware: SecurityHeadersMiddleware) -> None:
    resp = _make_response()
    call_next = AsyncMock(return_value=resp)
    csp = "default-src 'self'"

    with patch.dict("os.environ", {"CONTENT_SECURITY_POLICY": csp}):
        result = await middleware.dispatch(_make_request(), call_next)

    assert result.headers["Content-Security-Policy"] == csp


@pytest.mark.asyncio
async def test_no_csp_by_default(middleware: SecurityHeadersMiddleware) -> None:
    resp = _make_response()
    call_next = AsyncMock(return_value=resp)

    with patch.dict("os.environ", {}, clear=True):
        result = await middleware.dispatch(_make_request(), call_next)

    assert "Content-Security-Policy" not in result.headers
