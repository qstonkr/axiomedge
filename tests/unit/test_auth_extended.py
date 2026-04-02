"""Extended unit tests for src/auth/ — providers, service, dependencies."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.providers import (
    AuthUser,
    AuthenticationError,
    AzureADAuthProvider,
    InternalAuthProvider,
    KeycloakAuthProvider,
    LocalAuthProvider,
    create_auth_provider,
)


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# create_auth_provider factory
# ===========================================================================
class TestCreateAuthProvider:
    def test_local_default(self):
        provider = create_auth_provider()
        assert isinstance(provider, LocalAuthProvider)
        assert provider.provider_name == "local"

    def test_local_with_api_keys(self):
        keys = {"key1": {"email": "test@test.com", "name": "Test"}}
        provider = create_auth_provider("local", api_keys=keys)
        assert isinstance(provider, LocalAuthProvider)

    def test_keycloak(self):
        provider = create_auth_provider(
            "keycloak",
            server_url="https://kc.example.com",
            realm="test",
            client_id="my-app",
            client_secret="secret",
        )
        assert isinstance(provider, KeycloakAuthProvider)
        assert provider.provider_name == "keycloak"

    def test_azure_ad(self):
        provider = create_auth_provider(
            "azure_ad",
            tenant_id="tenant-123",
            client_id="client-456",
        )
        assert isinstance(provider, AzureADAuthProvider)
        assert provider.provider_name == "azure_ad"

    def test_internal(self):
        jwt_service = MagicMock()
        provider = create_auth_provider("internal", jwt_service=jwt_service)
        assert isinstance(provider, InternalAuthProvider)
        assert provider.provider_name == "internal"

    def test_internal_no_jwt_service(self):
        with pytest.raises(ValueError, match="jwt_service required"):
            create_auth_provider("internal")

    def test_unknown_defaults_to_local(self):
        provider = create_auth_provider("unknown_provider")
        assert isinstance(provider, LocalAuthProvider)


# ===========================================================================
# LocalAuthProvider
# ===========================================================================
class TestLocalAuthProvider:
    def test_verify_valid_key(self):
        keys = {"mykey": {"email": "user@test.com", "name": "User", "roles": ["admin"], "department": "IT"}}
        provider = LocalAuthProvider(api_keys=keys)

        async def _go():
            user = await provider.verify_token("mykey")
            assert user.email == "user@test.com"
            assert "admin" in user.roles
            assert user.department == "IT"
            assert user.provider == "local"

        _run(_go())

    def test_verify_invalid_key(self):
        provider = LocalAuthProvider()

        async def _go():
            with pytest.raises(AuthenticationError, match="Invalid API key"):
                await provider.verify_token("bad-key")

        _run(_go())

    def test_verify_empty_token(self):
        provider = LocalAuthProvider()

        async def _go():
            with pytest.raises(AuthenticationError, match="No token"):
                await provider.verify_token("")

        _run(_go())

    def test_get_jwks_uri(self):
        provider = LocalAuthProvider()

        async def _go():
            assert await provider.get_jwks_uri() is None

        _run(_go())


# ===========================================================================
# InternalAuthProvider
# ===========================================================================
class TestInternalAuthProvider:
    def test_verify_token(self):
        jwt_service = MagicMock()
        jwt_service.verify_access_token.return_value = {
            "sub": "user-1",
            "email": "user@test.com",
            "display_name": "User",
            "roles": ["viewer"],
        }
        provider = InternalAuthProvider(jwt_service=jwt_service)

        async def _go():
            user = await provider.verify_token("valid-jwt")
            assert user.sub == "user-1"
            assert user.email == "user@test.com"
            assert user.provider == "internal"

        _run(_go())

    def test_get_jwks_uri(self):
        jwt_service = MagicMock()
        provider = InternalAuthProvider(jwt_service=jwt_service)

        async def _go():
            assert await provider.get_jwks_uri() is None

        _run(_go())


# ===========================================================================
# KeycloakAuthProvider
# ===========================================================================
class TestKeycloakAuthProvider:
    def test_jwks_uri(self):
        provider = KeycloakAuthProvider(
            server_url="https://kc.example.com",
            realm="myrealm",
            client_id="app",
        )

        async def _go():
            uri = await provider.get_jwks_uri()
            assert "myrealm" in uri
            assert "certs" in uri

        _run(_go())


# ===========================================================================
# AzureADAuthProvider
# ===========================================================================
class TestAzureADAuthProvider:
    def test_jwks_uri(self):
        provider = AzureADAuthProvider(tenant_id="t-123", client_id="c-456")

        async def _go():
            uri = await provider.get_jwks_uri()
            assert "t-123" in uri
            assert "keys" in uri

        _run(_go())


# ===========================================================================
# AuthenticationError
# ===========================================================================
class TestAuthenticationError:
    def test_default(self):
        err = AuthenticationError()
        assert err.detail == "Authentication failed"
        assert err.status_code == 401

    def test_custom(self):
        err = AuthenticationError("Custom error", 403)
        assert err.detail == "Custom error"
        assert err.status_code == 403


# ===========================================================================
# AuthUser dataclass
# ===========================================================================
class TestAuthUser:
    def test_defaults(self):
        user = AuthUser(sub="u1", email="a@b.com", display_name="A", provider="local")
        assert user.roles == []
        assert user.groups == []
        assert user.department is None
        assert user.organization_id is None
        assert user.raw_claims == {}


# ===========================================================================
# AuthService facade
# ===========================================================================
class TestAuthServiceFacade:
    def test_delegates_sync_user(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        auth_user = AuthUser(sub="u1", email="a@b.com", display_name="A", provider="keycloak")
        svc._users.sync_user_from_idp = AsyncMock(return_value={"id": "u1"})

        async def _go():
            result = await svc.sync_user_from_idp(auth_user)
            assert result["id"] == "u1"

        _run(_go())

    def test_delegates_create_user(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._users.create_user = AsyncMock(return_value={"id": "new"})

        async def _go():
            result = await svc.create_user("a@b.com", "A")
            assert result["id"] == "new"

        _run(_go())

    def test_delegates_authenticate(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._auth.authenticate = AsyncMock(return_value={"id": "u1"})

        async def _go():
            result = await svc.authenticate("a@b.com", "pass")
            assert result["id"] == "u1"

        _run(_go())

    def test_delegates_roles(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._roles.get_user_roles = AsyncMock(return_value=[{"role": "admin"}])
        svc._roles.assign_role = AsyncMock(return_value={"role": "editor"})
        svc._roles.revoke_role = AsyncMock(return_value=True)

        async def _go():
            roles = await svc.get_user_roles("u1")
            assert len(roles) == 1
            r = await svc.assign_role("u1", "editor")
            assert r["role"] == "editor"
            assert await svc.revoke_role("u1", "admin") is True

        _run(_go())

    def test_delegates_activity(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._activity.log_activity = AsyncMock()
        svc._activity.get_user_activities = AsyncMock(return_value=[])
        svc._activity.get_activity_summary = AsyncMock(return_value={"total": 0})

        async def _go():
            await svc.log_activity("u1", "search", "kb")
            activities = await svc.get_user_activities("u1")
            assert activities == []
            summary = await svc.get_activity_summary("u1")
            assert summary["total"] == 0

        _run(_go())

    def test_delegates_kb_permissions(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._roles.get_kb_permission = AsyncMock(return_value="contributor")
        svc._roles.set_kb_permission = AsyncMock(return_value={"level": "manager"})
        svc._roles.list_kb_permissions = AsyncMock(return_value=[])
        svc._roles.remove_kb_permission = AsyncMock(return_value=True)

        async def _go():
            perm = await svc.get_kb_permission("u1", "kb1")
            assert perm == "contributor"
            await svc.set_kb_permission("u1", "kb1", "manager")
            perms = await svc.list_kb_permissions("kb1")
            assert perms == []
            assert await svc.remove_kb_permission("u1", "kb1") is True

        _run(_go())

    def test_delegates_user_crud(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._users.update_user = AsyncMock(return_value={"id": "u1"})
        svc._users.delete_user = AsyncMock(return_value=True)
        svc._users.get_user = AsyncMock(return_value={"id": "u1"})
        svc._users.list_users = AsyncMock(return_value=[{"id": "u1"}])

        async def _go():
            assert (await svc.update_user("u1", name="New"))["id"] == "u1"
            assert await svc.delete_user("u1") is True
            assert (await svc.get_user("u1"))["id"] == "u1"
            assert len(await svc.list_users()) == 1

        _run(_go())

    def test_delegates_change_password(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            mock_engine.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        svc._auth.change_password = AsyncMock(return_value=True)

        async def _go():
            assert await svc.change_password("u1", "old", "new") is True

        _run(_go())


# ===========================================================================
# Dependencies
# ===========================================================================
class TestDependencies:
    def test_anonymous_user_when_auth_disabled(self):
        from src.auth.dependencies import get_current_user, AUTH_ENABLED

        if AUTH_ENABLED:
            pytest.skip("AUTH_ENABLED=true in env")

        request = MagicMock()
        request.headers = {}
        request.cookies = {}

        async def _go():
            user = await get_current_user(request)
            assert user.sub == "anonymous"
            assert "admin" in user.roles

        _run(_go())

    def test_get_optional_user_returns_none_on_error(self):
        from src.auth.dependencies import get_optional_user

        request = MagicMock()
        request.headers = {"Authorization": "Bearer invalid"}
        request.cookies = {}

        # Mock app state to not have auth provider
        app_state = MagicMock()
        app_state.get = MagicMock(return_value=None)
        request.app = MagicMock()
        request.app.state = MagicMock()
        request.app.state._app_state = app_state

        async def _go():
            with patch("src.auth.dependencies.AUTH_ENABLED", True):
                user = await get_optional_user(request)
                # Should return None instead of raising
                assert user is None

        _run(_go())

    def test_require_role_returns_callable(self):
        from src.auth.dependencies import require_role
        dep = require_role("admin", "editor")
        assert callable(dep)

    def test_require_permission_returns_callable(self):
        from src.auth.dependencies import require_permission
        dep = require_permission("kb", "manage")
        assert callable(dep)

    def test_require_kb_access_returns_callable(self):
        from src.auth.dependencies import require_kb_access
        dep = require_kb_access("contributor")
        assert callable(dep)
