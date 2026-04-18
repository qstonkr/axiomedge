"""Unit tests for src/auth/middleware.py — middleware-level auth enforcement.

This is the SSOT for "no anonymous request slips through". Routes are tested
generically via a tiny FastAPI app so we don't need to spin up the real one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from src.auth import dependencies as deps
from src.auth import middleware as mw
from src.auth.dependencies import get_current_user
from src.auth.middleware import AuthMiddleware, _is_public
from src.auth.providers import AuthenticationError, AuthUser


# =============================================================================
# Public-path matcher
# =============================================================================


@pytest.mark.parametrize("path", [
    "/health", "/ready", "/metrics",
    "/openapi.json", "/favicon.ico", "/",
    "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/register",
    "/docs", "/docs/oauth2-redirect", "/redoc",
    "/static/css/app.css",
])
def test_public_paths_bypass(path: str) -> None:
    assert _is_public(path) is True


@pytest.mark.parametrize("path", [
    "/api/v1/kb",
    "/api/v1/search/hub",
    "/api/v1/agentic/ask",
    "/api/v1/glossary",
    "/api/v1/admin/data-sources",
    "/api/v1/auth/me",        # /me requires auth
    "/api/v1/auth/users",     # user mgmt requires auth
])
def test_protected_paths_not_bypassed(path: str) -> None:
    assert _is_public(path) is False


# =============================================================================
# Middleware enforcement
# =============================================================================


def _make_app(provider_verify: Any) -> FastAPI:
    """Build a minimal FastAPI app with AuthMiddleware mounted.

    ``provider_verify`` is the AsyncMock used by the auth_provider's
    ``verify_token``. State is wired via app.state._app_state to mimic real app.
    """
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    auth_provider = MagicMock()
    auth_provider.verify_token = provider_verify
    app.state._app_state = {"auth_provider": auth_provider}

    @app.get("/api/v1/test/protected")
    async def protected(user: AuthUser = Depends(get_current_user)) -> dict:
        return {"sub": user.sub}

    @app.get("/api/v1/test/no-deps")
    async def no_deps(request: Request) -> dict:
        # Defense-in-depth: route forgot to add Depends. Middleware should still
        # have populated request.state.auth_user (or 401'd before we got here).
        cached = getattr(request.state, "auth_user", None)
        return {"sub": cached.sub if cached else None}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


@pytest.fixture
def fake_user() -> AuthUser:
    return AuthUser(
        sub="user-1", email="u@test", display_name="U", provider="internal",
        roles=["MEMBER"], active_org_id="org-1",
    )


def test_protected_route_401_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock()
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get("/api/v1/test/protected")

    assert resp.status_code == 401
    assert "Missing authentication token" in resp.json()["detail"]
    verify.assert_not_called()


def test_protected_route_401_on_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock(side_effect=AuthenticationError("Token expired", status_code=401))
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/test/protected",
        headers={"Authorization": "Bearer invalid"},
    )

    assert resp.status_code == 401
    assert "Token expired" in resp.json()["detail"]


def test_protected_route_200_with_valid_token(
    monkeypatch: pytest.MonkeyPatch, fake_user: AuthUser,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock(return_value=fake_user)
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/test/protected",
        headers={"Authorization": "Bearer good"},
    )

    assert resp.status_code == 200
    assert resp.json()["sub"] == "user-1"


def test_route_without_depends_still_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth — route forgot Depends, middleware still 401s."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock()
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get("/api/v1/test/no-deps")  # no Authorization

    assert resp.status_code == 401


def test_route_without_depends_uses_cached_user(
    monkeypatch: pytest.MonkeyPatch, fake_user: AuthUser,
) -> None:
    """Middleware populates state.auth_user even if handler doesn't Depends."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock(return_value=fake_user)
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/test/no-deps",
        headers={"Authorization": "Bearer good"},
    )

    assert resp.status_code == 200
    assert resp.json()["sub"] == "user-1"


def test_public_path_bypasses_auth_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock()
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get("/health")  # no token

    assert resp.status_code == 200
    verify.assert_not_called()


def test_auth_disabled_attaches_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", False)
    monkeypatch.setattr(mw, "AUTH_ENABLED", False)

    verify = AsyncMock()
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get("/api/v1/test/protected")

    assert resp.status_code == 200
    assert resp.json()["sub"] == "anonymous"
    verify.assert_not_called()


def test_503_when_state_not_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token sent but app state isn't wired — surfaces a 503, not a 500."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    # Deliberately do NOT set app.state._app_state

    @app.get("/api/v1/test/x")
    async def x() -> dict:
        return {}

    client = TestClient(app)
    resp = client.get("/api/v1/test/x", headers={"Authorization": "Bearer x"})

    assert resp.status_code == 503


def test_x_api_key_header_accepted(
    monkeypatch: pytest.MonkeyPatch, fake_user: AuthUser,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock(return_value=fake_user)
    app = _make_app(verify)
    client = TestClient(app)

    resp = client.get(
        "/api/v1/test/protected",
        headers={"X-API-Key": "key-abc"},
    )

    assert resp.status_code == 200
    verify.assert_awaited_once_with("key-abc")


def test_cookie_token_accepted(
    monkeypatch: pytest.MonkeyPatch, fake_user: AuthUser,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)
    monkeypatch.setattr(mw, "AUTH_ENABLED", True)

    verify = AsyncMock(return_value=fake_user)
    app = _make_app(verify)
    client = TestClient(app)
    client.cookies.set("access_token", "cookie-token")

    resp = client.get("/api/v1/test/protected")

    assert resp.status_code == 200
    verify.assert_awaited_once_with("cookie-token")
