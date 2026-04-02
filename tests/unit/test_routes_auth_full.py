"""Unit tests for src/api/routes/auth.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import auth as auth_mod
from src.auth.providers import AuthUser


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _fake_user(**kwargs) -> AuthUser:
    defaults = dict(
        sub="user1", email="test@test.com", display_name="Test",
        provider="local", roles=["admin"], department="IT",
        organization_id="org1",
    )
    defaults.update(kwargs)
    return AuthUser(**defaults)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    return state


def _make_app():
    app = FastAPI()
    app.include_router(auth_mod.router)
    return app


def _override_auth_deps(app, user=None):
    """Override auth dependencies so tests don't need real JWT."""
    from src.auth.dependencies import get_current_user, require_permission

    u = user or _fake_user()
    app.dependency_overrides[get_current_user] = lambda: u

    def _require_perm(*args, **kwargs):
        def dep():
            return u
        return dep

    # We need to override each unique require_permission call
    # Since require_permission returns a new callable, we override get_current_user
    # and also override the specific dependency
    # Simpler: just override at module level
    return u


# ============================================================================
# Login
# ============================================================================

class TestLogin:
    def test_login_success(self):
        auth_svc = AsyncMock()
        auth_svc.authenticate = AsyncMock(return_value={"id": "u1", "email": "a@b.com", "display_name": "A"})
        auth_svc.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])

        jwt_svc = MagicMock()
        token_pair = MagicMock()
        token_pair.access_token = "access123"
        token_pair.refresh_token = "refresh123"
        token_pair.refresh_expires_at = "2025-01-01"
        jwt_svc.create_token_pair = MagicMock(return_value=token_pair)
        jwt_svc.decode_refresh_token = MagicMock(return_value={"jti": "j1", "family_id": "f1"})
        jwt_svc.access_expire_seconds = 3600
        jwt_svc.refresh_expire_seconds = 86400

        token_store = AsyncMock()
        token_store.store_refresh_token = AsyncMock()

        rbac = MagicMock()
        rbac.get_effective_permissions = MagicMock(return_value=["kb:read"])

        state = _mock_state(
            auth_service=auth_svc, jwt_service=jwt_svc,
            token_store=token_store, rbac_engine=rbac,
        )

        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch.object(auth_mod, "_get_state", return_value=state):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pass123"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "access_token" in resp.cookies or data["success"]

    def test_login_no_auth_service(self):
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "p"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 503

    def test_login_invalid_credentials(self):
        auth_svc = AsyncMock()
        auth_svc.authenticate = AsyncMock(return_value=None)
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 401

    def test_login_no_jwt_service(self):
        auth_svc = AsyncMock()
        auth_svc.authenticate = AsyncMock(return_value={"id": "u1", "email": "a@b.com"})
        state = _mock_state(auth_service=auth_svc)
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch.object(auth_mod, "_get_state", return_value=state):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "p"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 503


# ============================================================================
# Logout
# ============================================================================

class TestLogout:
    def test_logout(self):
        app = _make_app()
        jwt_svc = MagicMock()
        jwt_svc.decode_refresh_token = MagicMock(return_value={"family_id": "f1"})
        token_store = AsyncMock()
        token_store.revoke_family = AsyncMock()
        state = _mock_state(jwt_service=jwt_svc, token_store=token_store)

        async def _test():
            with patch.object(auth_mod, "_get_state", return_value=state):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/auth/logout")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_logout_no_services(self):
        app = _make_app()
        state = _mock_state()

        async def _test():
            with patch.object(auth_mod, "_get_state", return_value=state):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/auth/logout")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200


# ============================================================================
# Register
# ============================================================================

class TestRegister:
    def test_register_success(self):
        auth_svc = AsyncMock()
        auth_svc.create_user_with_password = AsyncMock(return_value={"id": "u1", "email": "new@b.com"})

        app = _make_app()
        from src.auth.dependencies import get_current_user, require_permission
        user = _fake_user()
        # Override the dependency
        app.dependency_overrides[get_current_user] = lambda: user
        # require_permission returns a callable that returns a dependency
        # We need to patch at module level
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/register", json={
                            "email": "new@b.com", "password": "longpass1", "display_name": "New"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_register_short_password(self):
        auth_svc = AsyncMock()
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/register", json={
                            "email": "a@b.com", "password": "short", "display_name": "A"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_register_no_auth_service(self):
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/register", json={
                            "email": "a@b.com", "password": "longpass1", "display_name": "A"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 503

    def test_register_duplicate(self):
        auth_svc = AsyncMock()
        auth_svc.create_user_with_password = AsyncMock(side_effect=ValueError("Email already exists"))
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/register", json={
                            "email": "dup@b.com", "password": "longpass1", "display_name": "A"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 409


# ============================================================================
# Me
# ============================================================================

class TestMe:
    def test_me(self):
        auth_svc = AsyncMock()
        auth_svc.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])
        rbac = MagicMock()
        rbac.get_effective_permissions = MagicMock(return_value=["kb:read"])
        state = _mock_state(auth_service=auth_svc, rbac_engine=rbac)

        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch.object(auth_mod, "_get_state", return_value=state):
                    with patch("src.api.app._get_state", return_value=state):
                        with patch("src.auth.dependencies.AUTH_ENABLED", False):
                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                resp = await client.get("/api/v1/auth/me")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "anonymous@local"  # Auth disabled -> anonymous


# ============================================================================
# List Users
# ============================================================================

class TestListUsers:
    def test_list_users(self):
        auth_svc = AsyncMock()
        auth_svc.list_users = AsyncMock(return_value=[{"id": "u1", "email": "a@b.com"}])
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/users")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_users_no_service(self):
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/users")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ============================================================================
# Create User
# ============================================================================

class TestCreateUser:
    def test_create_user(self):
        auth_svc = AsyncMock()
        auth_svc.create_user = AsyncMock(return_value={"id": "u1", "email": "n@b.com"})
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/users", json={
                            "email": "n@b.com", "display_name": "N"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_create_user_no_service(self):
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/users", json={
                            "email": "n@b.com", "display_name": "N"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 503


# ============================================================================
# Update User
# ============================================================================

class TestUpdateUser:
    def test_update_user(self):
        auth_svc = AsyncMock()
        auth_svc.update_user = AsyncMock(return_value={"id": "u1", "display_name": "Updated"})
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.put("/api/v1/auth/users/u1", json={"display_name": "Updated"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200

    def test_update_user_not_found(self):
        auth_svc = AsyncMock()
        auth_svc.update_user = AsyncMock(return_value=None)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.put("/api/v1/auth/users/u1", json={"display_name": "X"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 404


# ============================================================================
# Delete User
# ============================================================================

class TestDeleteUser:
    def test_delete_user(self):
        auth_svc = AsyncMock()
        auth_svc.delete_user = AsyncMock(return_value=True)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/users/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200

    def test_delete_user_not_found(self):
        auth_svc = AsyncMock()
        auth_svc.delete_user = AsyncMock(return_value=False)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/users/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 404


# ============================================================================
# Get User
# ============================================================================

class TestGetUser:
    def test_get_user(self):
        auth_svc = AsyncMock()
        auth_svc.get_user = AsyncMock(return_value={"id": "u1", "email": "a@b.com"})
        auth_svc.get_user_roles = AsyncMock(return_value=[{"role": "viewer"}])
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/users/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["roles"] == [{"role": "viewer"}]

    def test_get_user_not_found(self):
        auth_svc = AsyncMock()
        auth_svc.get_user = AsyncMock(return_value=None)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/users/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 404


# ============================================================================
# Roles
# ============================================================================

class TestRoles:
    def test_list_roles(self):
        app = _make_app()
        async def _test():
            with patch("src.auth.dependencies.AUTH_ENABLED", False):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.get("/api/v1/auth/roles")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert "roles" in resp.json()

    def test_assign_role(self):
        auth_svc = AsyncMock()
        auth_svc.assign_role = AsyncMock(return_value={"role": "editor", "user_id": "u1"})
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/users/u1/roles", json={"role": "editor"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_assign_role_missing_field(self):
        auth_svc = AsyncMock()
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/users/u1/roles", json={})
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_revoke_role(self):
        auth_svc = AsyncMock()
        auth_svc.revoke_role = AsyncMock(return_value=True)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/users/u1/roles/editor")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200

    def test_revoke_role_not_found(self):
        auth_svc = AsyncMock()
        auth_svc.revoke_role = AsyncMock(return_value=False)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/users/u1/roles/editor")
            return resp

        resp = _run(_test())
        assert resp.status_code == 404


# ============================================================================
# KB Permissions
# ============================================================================

class TestKBPermissions:
    def test_list_kb_permissions(self):
        auth_svc = AsyncMock()
        auth_svc.list_kb_permissions = AsyncMock(return_value=[{"user_id": "u1", "level": "reader"}])
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/kb/kb1/permissions")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["kb_id"] == "kb1"

    def test_set_kb_permission(self):
        auth_svc = AsyncMock()
        auth_svc.set_kb_permission = AsyncMock(return_value={"user_id": "u1", "level": "reader"})
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/kb/kb1/permissions", json={
                            "user_id": "u1", "permission_level": "reader"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 200

    def test_set_kb_permission_missing_user(self):
        auth_svc = AsyncMock()
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/kb/kb1/permissions", json={
                            "permission_level": "reader"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_set_kb_permission_invalid_level(self):
        auth_svc = AsyncMock()
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/kb/kb1/permissions", json={
                            "user_id": "u1", "permission_level": "superadmin"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_remove_kb_permission(self):
        auth_svc = AsyncMock()
        auth_svc.remove_kb_permission = AsyncMock(return_value=True)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/kb/kb1/permissions/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200

    def test_remove_kb_permission_not_found(self):
        auth_svc = AsyncMock()
        auth_svc.remove_kb_permission = AsyncMock(return_value=False)
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.delete("/api/v1/auth/kb/kb1/permissions/u1")
            return resp

        resp = _run(_test())
        assert resp.status_code == 404


# ============================================================================
# My Activities
# ============================================================================

class TestMyActivities:
    def test_get_activities(self):
        auth_svc = AsyncMock()
        auth_svc.get_user_activities = AsyncMock(return_value=[
            {"activity_type": "search", "created_at": "2024-06-15T10:00:00"},
        ])
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/my-activities")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert len(resp.json()["activities"]) == 1

    def test_get_activities_with_date_filter(self):
        auth_svc = AsyncMock()
        auth_svc.get_user_activities = AsyncMock(return_value=[
            {"created_at": "2024-06-15T10:00:00"},
            {"created_at": "2024-07-15T10:00:00"},
        ])
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/my-activities?date_from=2024-07-01")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert len(resp.json()["activities"]) == 1

    def test_get_activities_no_service(self):
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/my-activities")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["activities"] == []

    def test_activity_summary(self):
        auth_svc = AsyncMock()
        auth_svc.get_activity_summary = AsyncMock(return_value={"period_days": 30, "total": 5, "by_type": {}})
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/my-activities/summary")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_activity_summary_no_service(self):
        app = _make_app()
        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=None):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.get("/api/v1/auth/my-activities/summary")
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ============================================================================
# Change Password
# ============================================================================

class TestChangePassword:
    def test_change_password_success(self):
        auth_svc = AsyncMock()
        auth_svc.change_password = AsyncMock(return_value=True)
        token_store = AsyncMock()
        token_store.revoke_all_user_tokens = AsyncMock()
        state = _mock_state(auth_service=auth_svc, token_store=token_store)
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch.object(auth_mod, "_get_state", return_value=state):
                    with patch("src.auth.dependencies.AUTH_ENABLED", False):
                        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                            resp = await client.post("/api/v1/auth/change-password", json={
                                "old_password": "old12345", "new_password": "new12345"
                            })
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_change_password_short(self):
        auth_svc = AsyncMock()
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/change-password", json={
                            "old_password": "old12345", "new_password": "short"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_change_password_wrong_old(self):
        auth_svc = AsyncMock()
        auth_svc.change_password = AsyncMock(return_value=False)
        app = _make_app()

        async def _test():
            with patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
                with patch("src.auth.dependencies.AUTH_ENABLED", False):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post("/api/v1/auth/change-password", json={
                            "old_password": "wrong123", "new_password": "new12345"
                        })
            return resp

        resp = _run(_test())
        assert resp.status_code == 400
