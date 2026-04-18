"""Tests for per-tenant rate limit scope (user / user_or_ip / ip)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.requests import Request

from src.api.middleware.rate_limiter import _compute_rate_key


@dataclass
class _FakeUser:
    sub: str


def _make_request(user_sub: str | None, headers: dict[str, str] | None = None) -> Request:
    raw_state: dict[str, Any] = {}
    if user_sub is not None:
        raw_state["auth_user"] = _FakeUser(sub=user_sub)
    scope: dict = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("1.2.3.4", 0),
        "state": raw_state,
    }
    request = Request(scope)
    # request.state proxies via scope["state"]
    for k, v in raw_state.items():
        setattr(request.state, k, v)
    return request


def test_scope_ip_uses_client_host() -> None:
    req = _make_request(user_sub="user-1")
    key = _compute_rate_key(req, scope_mode="ip", trust_proxy=False)
    assert key == "ip:1.2.3.4"  # ip mode ignores user


def test_scope_user_uses_user_sub() -> None:
    req = _make_request(user_sub="user-abc")
    key = _compute_rate_key(req, scope_mode="user", trust_proxy=False)
    assert key == "user:user-abc"


def test_scope_user_anonymous_falls_to_anon_bucket() -> None:
    req = _make_request(user_sub=None)
    key = _compute_rate_key(req, scope_mode="user", trust_proxy=False)
    assert key == "user:anonymous"


def test_scope_user_or_ip_prefers_user() -> None:
    req = _make_request(user_sub="user-xyz")
    key = _compute_rate_key(req, scope_mode="user_or_ip", trust_proxy=False)
    assert key == "user:user-xyz"


def test_scope_user_or_ip_falls_back_to_ip_for_anonymous() -> None:
    req = _make_request(user_sub=None)
    key = _compute_rate_key(req, scope_mode="user_or_ip", trust_proxy=False)
    assert key == "ip:1.2.3.4"


def test_scope_user_or_ip_treats_anonymous_string_as_unauthenticated() -> None:
    req = _make_request(user_sub="anonymous")
    key = _compute_rate_key(req, scope_mode="user_or_ip", trust_proxy=False)
    assert key == "ip:1.2.3.4"  # 'anonymous' sub treated as not-logged-in
