"""Tests for proxy-aware client IP extraction in rate limiter."""

from __future__ import annotations

from starlette.requests import Request

from src.api.middleware.rate_limiter import _extract_client_ip


def _make_request(headers: dict[str, str] | None = None, client_host: str | None = "1.2.3.4") -> Request:
    scope: dict = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 0) if client_host else None,
    }
    return Request(scope)


def test_no_trust_returns_direct_client() -> None:
    req = _make_request(headers={"x-forwarded-for": "8.8.8.8"})
    assert _extract_client_ip(req, trust_proxy=False) == "1.2.3.4"


def test_trust_xff_returns_first_ip() -> None:
    req = _make_request(headers={"x-forwarded-for": "8.8.8.8, 10.0.0.1, 192.168.1.1"})
    assert _extract_client_ip(req, trust_proxy=True) == "8.8.8.8"


def test_trust_cf_takes_priority_over_xff() -> None:
    req = _make_request(headers={
        "cf-connecting-ip": "9.9.9.9",
        "x-forwarded-for": "8.8.8.8",
    })
    assert _extract_client_ip(req, trust_proxy=True) == "9.9.9.9"


def test_trust_x_real_ip_takes_priority_over_xff() -> None:
    req = _make_request(headers={
        "x-real-ip": "7.7.7.7",
        "x-forwarded-for": "8.8.8.8",
    })
    assert _extract_client_ip(req, trust_proxy=True) == "7.7.7.7"


def test_trust_falls_back_when_no_proxy_headers() -> None:
    req = _make_request(headers={})
    assert _extract_client_ip(req, trust_proxy=True) == "1.2.3.4"


def test_no_client_returns_unknown() -> None:
    req = _make_request(headers={}, client_host=None)
    assert _extract_client_ip(req, trust_proxy=False) == "unknown"
