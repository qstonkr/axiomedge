"""Unit tests for src/auth/dependencies.py — auth dependency functions."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from src.auth.providers import AuthUser, AuthenticationError


# ---------------------------------------------------------------------------
# get_current_user — AUTH_ENABLED=false
# ---------------------------------------------------------------------------

class TestGetCurrentUserAuthDisabled:
    def test_returns_anonymous_when_auth_disabled(self):
        """When AUTH_ENABLED=false, get_current_user returns anonymous admin."""
        with patch("src.auth.dependencies.AUTH_ENABLED", False):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub, "email": user.email, "roles": user.roles}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/test")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["sub"] == "anonymous"
                    assert data["email"] == "anonymous@local"
                    assert "admin" in data["roles"]

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_current_user — AUTH_ENABLED=true
# ---------------------------------------------------------------------------

class TestGetCurrentUserAuthEnabled:
    def test_missing_token_returns_401(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/test")
                    assert resp.status_code == 401

            asyncio.run(_run())

    def test_bearer_token_verified(self):
        """Valid Bearer token is verified via auth_provider."""
        mock_user = AuthUser(
            sub="user1", email="user@test.com", display_name="User",
            provider="local", roles=["reader"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub, "provider": user.provider}

            # Mock app state with auth provider
            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider

            # Set _app_state on app
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/test",
                        headers={"Authorization": "Bearer valid-token"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["sub"] == "user1"
                    mock_provider.verify_token.assert_called_once_with("valid-token")

            asyncio.run(_run())

    def test_apikey_header_verified(self):
        """ApiKey auth header is used as token."""
        mock_user = AuthUser(
            sub="api-user", email="api@test.com", display_name="API",
            provider="local", roles=["reader"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/test",
                        headers={"Authorization": "ApiKey my-api-key"},
                    )
                    assert resp.status_code == 200
                    mock_provider.verify_token.assert_called_once_with("my-api-key")

            asyncio.run(_run())

    def test_x_api_key_header(self):
        """X-API-Key header is used as fallback."""
        mock_user = AuthUser(
            sub="xapi", email="x@test.com", display_name="X",
            provider="local", roles=[],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/test", headers={"X-API-Key": "xkey"})
                    assert resp.status_code == 200
                    mock_provider.verify_token.assert_called_once_with("xkey")

            asyncio.run(_run())

    def test_invalid_token_returns_401(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(
                side_effect=AuthenticationError("Invalid token", status_code=401)
            )

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/test",
                        headers={"Authorization": "Bearer bad-token"},
                    )
                    assert resp.status_code == 401

            asyncio.run(_run())

    def test_no_app_state_returns_503(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            # No _app_state set on app.state
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/test",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 503

            asyncio.run(_run())

    def test_no_auth_provider_returns_503(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_current_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user: AuthUser = Depends(get_current_user)):
                return {"sub": user.sub}

            from src.api.state import AppState

            mock_state = AppState()  # No auth_provider set
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/test",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 503

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_optional_user
# ---------------------------------------------------------------------------

class TestGetOptionalUser:
    def test_returns_none_on_failure(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import get_optional_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user=Depends(get_optional_user)):
                return {"user": user.sub if user else None}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    # No auth header -> should return None (not 401)
                    resp = await ac.get("/test")
                    assert resp.status_code == 200
                    assert resp.json()["user"] is None

            asyncio.run(_run())

    def test_returns_user_when_auth_disabled(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", False):
            from src.auth.dependencies import get_optional_user

            app = FastAPI()

            @app.get("/test")
            async def test_endpoint(user=Depends(get_optional_user)):
                return {"user": user.sub if user else None}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/test")
                    assert resp.status_code == 200
                    assert resp.json()["user"] == "anonymous"

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# require_role
# ---------------------------------------------------------------------------

class TestRequireRole:
    def test_require_role_auth_disabled_allows_all(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", False):
            from src.auth.dependencies import require_role

            app = FastAPI()

            @app.get("/admin")
            async def admin_endpoint(user: AuthUser = Depends(require_role("admin"))):
                return {"sub": user.sub}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/admin")
                    assert resp.status_code == 200
                    assert resp.json()["sub"] == "anonymous"

            asyncio.run(_run())

    def test_require_role_checks_token_roles(self):
        """When RBAC engine not available, falls back to token roles."""
        mock_user = AuthUser(
            sub="u1", email="u@t.com", display_name="U",
            provider="local", roles=["admin"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import require_role

            app = FastAPI()

            @app.get("/admin")
            async def admin_endpoint(user: AuthUser = Depends(require_role("admin"))):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/admin",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_require_role_rejects_insufficient(self):
        """User without required role gets 403."""
        mock_user = AuthUser(
            sub="u1", email="u@t.com", display_name="U",
            provider="local", roles=["reader"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import require_role

            app = FastAPI()

            @app.get("/admin")
            async def admin_endpoint(user: AuthUser = Depends(require_role("admin"))):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get(
                        "/admin",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 403

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# require_permission
# ---------------------------------------------------------------------------

class TestRequirePermission:
    def test_require_permission_auth_disabled(self):
        with patch("src.auth.dependencies.AUTH_ENABLED", False):
            from src.auth.dependencies import require_permission

            app = FastAPI()

            @app.post("/import")
            async def import_endpoint(
                user: AuthUser = Depends(require_permission("glossary", "import")),
            ):
                return {"sub": user.sub}

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/import")
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_require_permission_no_rbac_engine(self):
        """Without RBAC engine, permission is allowed."""
        mock_user = AuthUser(
            sub="u1", email="u@t.com", display_name="U",
            provider="local", roles=["reader"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import require_permission

            app = FastAPI()

            @app.post("/import")
            async def import_endpoint(
                user: AuthUser = Depends(require_permission("glossary", "import")),
            ):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            # No rbac_engine set
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/import",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_require_permission_denied(self):
        """RBAC engine denies permission."""
        mock_user = AuthUser(
            sub="u1", email="u@t.com", display_name="U",
            provider="local", roles=["reader"],
        )

        with patch("src.auth.dependencies.AUTH_ENABLED", True):
            from src.auth.dependencies import require_permission

            app = FastAPI()

            @app.post("/import")
            async def import_endpoint(
                user: AuthUser = Depends(require_permission("glossary", "import")),
            ):
                return {"sub": user.sub}

            mock_provider = AsyncMock()
            mock_provider.verify_token = AsyncMock(return_value=mock_user)

            # Mock RBAC engine that denies
            mock_rbac = MagicMock()
            decision = MagicMock()
            decision.allowed = False
            decision.reason = "No permission"
            mock_rbac.check_permission = MagicMock(return_value=decision)

            from src.api.state import AppState

            mock_state = AppState()
            mock_state["auth_provider"] = mock_provider
            mock_state["rbac_engine"] = mock_rbac
            app.state._app_state = mock_state

            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/import",
                        headers={"Authorization": "Bearer token"},
                    )
                    assert resp.status_code == 403

            asyncio.run(_run())
