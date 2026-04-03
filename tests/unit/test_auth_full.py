"""Comprehensive tests for src/auth/ — service, middleware, token_store."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# AuthService (facade)
# ===========================================================================

class TestAuthService:
    def test_init_creates_sub_services(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker") as mock_maker:
            mock_engine.return_value = MagicMock()
            mock_maker.return_value = MagicMock()
            svc = AuthService("postgresql+asyncpg://localhost/test")

        assert svc._users is not None
        assert svc._auth is not None
        assert svc._roles is not None
        assert svc._activity is not None

    async def test_close(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine") as mock_engine, \
             patch("src.auth.service.async_sessionmaker"):
            engine = AsyncMock()
            mock_engine.return_value = engine
            svc = AuthService("postgresql+asyncpg://localhost/test")
            await svc.close()
            engine.dispose.assert_awaited_once()

    async def test_delegates_sync_user(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._users = AsyncMock()
            svc._users.sync_user_from_idp.return_value = {"id": "u1"}

            result = await svc.sync_user_from_idp(MagicMock())
            assert result["id"] == "u1"

    async def test_delegates_create_user(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._users = AsyncMock()
            svc._users.create_user.return_value = {"id": "u1", "email": "a@b.com"}

            result = await svc.create_user("a@b.com", "Alice")
            assert result["email"] == "a@b.com"

    async def test_delegates_authenticate(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._auth = AsyncMock()
            svc._auth.authenticate.return_value = {"id": "u1"}

            result = await svc.authenticate("a@b.com", "pass")
            assert result is not None

    async def test_delegates_assign_role(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._roles = AsyncMock()
            svc._roles.assign_role.return_value = {"role": "admin"}

            result = await svc.assign_role("u1", "admin")
            assert result["role"] == "admin"

    async def test_delegates_log_activity(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._activity = AsyncMock()

            await svc.log_activity("u1", "search", "search")
            svc._activity.log_activity.assert_awaited_once()

    async def test_delegates_get_user(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._users = AsyncMock()
            svc._users.get_user.return_value = {"id": "u1"}

            result = await svc.get_user("u1")
            assert result["id"] == "u1"

    async def test_delegates_list_users(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._users = AsyncMock()
            svc._users.list_users.return_value = [{"id": "u1"}]

            result = await svc.list_users()
            assert len(result) == 1

    async def test_delegates_revoke_role(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._roles = AsyncMock()
            svc._roles.revoke_role.return_value = True

            result = await svc.revoke_role("u1", "admin")
            assert result is True

    async def test_delegates_change_password(self):
        from src.auth.service import AuthService

        with patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker"):
            svc = AuthService("postgresql+asyncpg://localhost/test")
            svc._auth = AsyncMock()
            svc._auth.change_password.return_value = True

            result = await svc.change_password("u1", "old", "new")
            assert result is True


# ===========================================================================
# AuthMiddleware
# ===========================================================================

class TestAuthMiddleware:
    def test_classify_activity_search(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/search")
        assert result is not None
        assert result["type"] == "search"

    def test_classify_activity_upload(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/knowledge/file-upload-ingest")
        assert result["type"] == "upload"

    def test_classify_activity_ingest(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/knowledge/ingest")
        assert result["type"] == "ingest"

    def test_classify_activity_glossary_create(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/glossary/terms")
        assert result["type"] == "create"

    def test_classify_activity_glossary_edit(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("PUT", "/api/v1/glossary/terms/123")
        assert result["type"] == "edit"

    def test_classify_activity_feedback(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/feedback/submit")
        assert result["type"] == "feedback"

    def test_classify_activity_kb_create(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/kb/create")
        assert result["type"] == "create"

    def test_classify_activity_unknown(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("GET", "/api/v1/health")
        assert result is None

    def test_classify_activity_rag_query(self):
        from src.auth.middleware import AuthMiddleware
        mw = AuthMiddleware.__new__(AuthMiddleware)
        result = mw._classify_activity("POST", "/api/v1/knowledge/ask")
        assert result["type"] == "query"

    def test_public_paths(self):
        from src.auth.middleware import _PUBLIC_PATHS
        assert "/health" in _PUBLIC_PATHS
        assert "/docs" in _PUBLIC_PATHS


# ===========================================================================
# TokenStore
# ===========================================================================

class TestTokenStore:
    def setup_method(self):
        self.session = AsyncMock()
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock(return_value=False)
        self.maker = MagicMock()
        self.maker.return_value = self.session

    async def test_store_refresh_token(self):
        from src.auth.token_store import TokenStore

        store = TokenStore(self.maker)
        await store.store_refresh_token(
            jti="jti1",
            user_id="u1",
            family_id="f1",
            rotation_count=0,
            token_raw="token123",
            expires_at=datetime.now(timezone.utc),
        )
        self.session.add.assert_called_once()
        self.session.commit.assert_awaited_once()

    async def test_validate_and_rotate_not_found(self):
        from src.auth.token_store import TokenStore

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        result = await store.validate_and_rotate("jti1", "token123")
        assert result is None

    async def test_validate_and_rotate_revoked(self):
        from src.auth.token_store import TokenStore

        token = MagicMock()
        token.revoked_at = datetime.now(timezone.utc)
        token.family_id = "f1"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = token
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        with patch.object(store, "revoke_family", new_callable=AsyncMock):
            result = await store.validate_and_rotate("jti1", "token123")
        assert result is None

    async def test_revoke_family(self):
        from src.auth.token_store import TokenStore

        result_mock = MagicMock()
        result_mock.rowcount = 3
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        count = await store.revoke_family("f1")
        assert count == 3

    async def test_revoke_all_user_tokens(self):
        from src.auth.token_store import TokenStore

        result_mock = MagicMock()
        result_mock.rowcount = 5
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        count = await store.revoke_all_user_tokens("u1")
        assert count == 5

    async def test_get_active_sessions(self):
        from src.auth.token_store import TokenStore

        token = MagicMock()
        token.id = "jti1"
        token.family_id = "f1"
        token.ip_address = "1.2.3.4"
        token.user_agent = "Mozilla"
        token.created_at = datetime.now(timezone.utc)
        token.expires_at = datetime.now(timezone.utc)

        result_mock = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = [token]
        result_mock.scalars.return_value = scalars
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        sessions = await store.get_active_sessions("u1")
        assert len(sessions) == 1
        assert sessions[0]["jti"] == "jti1"

    async def test_cleanup_expired(self):
        from src.auth.token_store import TokenStore

        result_mock = MagicMock()
        result_mock.rowcount = 10
        self.session.execute.return_value = result_mock

        store = TokenStore(self.maker)
        count = await store.cleanup_expired()
        assert count == 10
