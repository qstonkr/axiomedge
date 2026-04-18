"""Unit tests for internal auth: password hashing, JWT service, token store, providers."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.password import hash_password, verify_password


def _run(coro):
    """Run async coroutine synchronously (no pytest-asyncio required)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# Password Hashing
# =============================================================================


class TestPasswordHashing:
    """Test bcrypt password hashing and verification."""

    def test_hash_and_verify_success(self) -> None:
        """Hash a password and verify it matches."""
        plain = "secure-password-123"
        hashed = hash_password(plain)
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password(self) -> None:
        """Wrong password should not verify."""
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_hash_is_bcrypt_format(self) -> None:
        """Hashed output should be valid bcrypt format ($2b$)."""
        hashed = hash_password("test")
        assert hashed.startswith("$2b$12$")

    def test_different_hashes_for_same_password(self) -> None:
        """Two hashes of the same password should differ (different salts)."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2
        assert verify_password("same-password", h1) is True
        assert verify_password("same-password", h2) is True

    def test_empty_password(self) -> None:
        """Empty password should hash and verify correctly."""
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("not-empty", hashed) is False


# =============================================================================
# JWT Service
# =============================================================================


class TestJWTService:
    """Test JWT token creation and verification."""

    def setup_method(self) -> None:
        from src.auth.jwt_service import JWTService

        self.secret = "test-secret-key-minimum-32-chars-long"
        self.service = JWTService(
            secret_key=self.secret,
            access_token_expire_minutes=60,
            refresh_token_expire_hours=8,
            issuer="axiomedge-api",
        )

    def test_create_token_pair(self) -> None:
        """Token pair should contain access and refresh tokens."""
        pair = self.service.create_token_pair(
            user_id="user-123",
            email="test@example.com",
            roles=["viewer"],
            permissions=["kb:read"],
            display_name="Test User",
        )
        assert pair.access_token
        assert pair.refresh_token
        assert pair.access_token != pair.refresh_token
        assert pair.token_type == "Bearer"

    def test_verify_access_token(self) -> None:
        """Access token should decode with correct claims."""
        pair = self.service.create_token_pair(
            user_id="user-456",
            email="user@test.com",
            roles=["admin", "viewer"],
            permissions=["kb:read", "kb:write"],
            display_name="Admin User",
        )
        claims = self.service.verify_access_token(pair.access_token)
        assert claims["sub"] == "user-456"
        assert claims["email"] == "user@test.com"
        assert claims["display_name"] == "Admin User"
        assert claims["roles"] == ["admin", "viewer"]
        assert claims["permissions"] == ["kb:read", "kb:write"]
        assert claims["type"] == "access"
        assert claims["iss"] == "axiomedge-api"
        assert "jti" in claims

    def test_decode_refresh_token(self) -> None:
        """Refresh token should decode with correct claims."""
        pair = self.service.create_token_pair(
            user_id="user-789",
            email="user@test.com",
            roles=["viewer"],
            permissions=[],
            family_id="family-abc",
            rotation_count=3,
        )
        claims = self.service.decode_refresh_token(pair.refresh_token)
        assert claims["sub"] == "user-789"
        assert claims["family_id"] == "family-abc"
        assert claims["rotation_count"] == 3
        assert claims["type"] == "refresh"
        assert claims["iss"] == "axiomedge-api"
        assert "jti" in claims

    def test_access_token_rejected_as_refresh(self) -> None:
        """Access token should not be accepted as refresh token."""
        from src.auth.providers import AuthenticationError

        pair = self.service.create_token_pair(
            user_id="user-1", email="a@b.com", roles=[], permissions=[]
        )
        with pytest.raises(AuthenticationError, match="Not a refresh token"):
            self.service.decode_refresh_token(pair.access_token)

    def test_refresh_token_rejected_as_access(self) -> None:
        """Refresh token should not be accepted as access token."""
        from src.auth.providers import AuthenticationError

        pair = self.service.create_token_pair(
            user_id="user-1", email="a@b.com", roles=[], permissions=[]
        )
        with pytest.raises(AuthenticationError, match="Not an access token"):
            self.service.verify_access_token(pair.refresh_token)

    def test_expired_access_token(self) -> None:
        """Expired access token should be rejected."""
        from src.auth.jwt_service import JWTService
        from src.auth.providers import AuthenticationError

        svc = JWTService(
            secret_key=self.secret,
            access_token_expire_minutes=0,  # Immediate expiry
        )
        pair = svc.create_token_pair(
            user_id="user-1", email="a@b.com", roles=[], permissions=[]
        )
        time.sleep(1)
        with pytest.raises(AuthenticationError, match="Token expired"):
            svc.verify_access_token(pair.access_token)

    def test_wrong_secret_rejected(self) -> None:
        """Token signed with different secret should be rejected."""
        from src.auth.jwt_service import JWTService
        from src.auth.providers import AuthenticationError

        other = JWTService(secret_key="different-secret-key-also-32-chars")
        pair = self.service.create_token_pair(
            user_id="user-1", email="a@b.com", roles=[], permissions=[]
        )
        with pytest.raises(AuthenticationError, match="Invalid token"):
            other.verify_access_token(pair.access_token)

    def test_invalid_token_string(self) -> None:
        """Garbage string should be rejected."""
        from src.auth.providers import AuthenticationError

        with pytest.raises(AuthenticationError, match="Invalid token"):
            self.service.verify_access_token("not.a.valid.jwt")

    def test_auto_generated_family_id(self) -> None:
        """Family ID should be auto-generated when not provided."""
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        claims = self.service.decode_refresh_token(pair.refresh_token)
        assert claims["family_id"]  # Should be a UUID string

    def test_expire_seconds_properties(self) -> None:
        """Expire second properties should match configured values."""
        assert self.service.access_expire_seconds == 3600
        assert self.service.refresh_expire_seconds == 28800

    def test_token_claims_have_required_fields(self) -> None:
        """JWT claims must include all required fields."""
        pair = self.service.create_token_pair(
            user_id="user-id",
            email="user@gs.com",
            roles=["viewer", "contributor"],
            permissions=["kb:read", "search:query"],
            display_name="Test",
        )
        claims = self.service.verify_access_token(pair.access_token)
        # Required claim fields
        assert "sub" in claims
        assert "email" in claims
        assert "roles" in claims
        assert "permissions" in claims
        assert "jti" in claims
        assert claims["iss"] == "axiomedge-api"


# =============================================================================
# Internal Auth Provider
# =============================================================================


class TestInternalAuthProvider:
    """Test InternalAuthProvider token verification."""

    def setup_method(self) -> None:
        from src.auth.jwt_service import JWTService
        from src.auth.providers import InternalAuthProvider

        self.jwt_service = JWTService(
            secret_key="test-secret-key-minimum-32-chars-long"
        )
        self.provider = InternalAuthProvider(jwt_service=self.jwt_service)

    def test_provider_name(self) -> None:
        assert self.provider.provider_name == "internal"

    def test_jwks_uri_none(self) -> None:
        """Internal provider uses HS256, no JWKS."""
        assert _run(self.provider.get_jwks_uri()) is None

    def test_verify_valid_token(self) -> None:
        """Valid access token should return AuthUser."""
        pair = self.jwt_service.create_token_pair(
            user_id="user-abc",
            email="test@example.com",
            roles=["viewer", "contributor"],
            permissions=["kb:read"],
            display_name="Test User",
        )
        user = _run(self.provider.verify_token(pair.access_token))
        assert user.sub == "user-abc"
        assert user.email == "test@example.com"
        assert user.display_name == "Test User"
        assert user.provider == "internal"
        assert "viewer" in user.roles

    def test_verify_invalid_token(self) -> None:
        """Invalid token should raise AuthenticationError."""
        from src.auth.providers import AuthenticationError

        with pytest.raises(AuthenticationError):
            _run(self.provider.verify_token("invalid-token"))

    def test_verify_refresh_token_rejected(self) -> None:
        """Refresh token should not pass as access token."""
        from src.auth.providers import AuthenticationError

        pair = self.jwt_service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        with pytest.raises(AuthenticationError, match="Not an access token"):
            _run(self.provider.verify_token(pair.refresh_token))


# =============================================================================
# Provider Factory
# =============================================================================


class TestProviderFactory:
    """Test create_auth_provider factory function."""

    def test_create_local_provider(self) -> None:
        from src.auth.providers import LocalAuthProvider, create_auth_provider

        provider = create_auth_provider("local")
        assert isinstance(provider, LocalAuthProvider)

    def test_create_internal_provider(self) -> None:
        from src.auth.jwt_service import JWTService
        from src.auth.providers import InternalAuthProvider, create_auth_provider

        jwt_svc = JWTService(secret_key="test-key-32-chars-long-minimum!!")
        provider = create_auth_provider("internal", jwt_service=jwt_svc)
        assert isinstance(provider, InternalAuthProvider)

    def test_internal_provider_requires_jwt_service(self) -> None:
        from src.auth.providers import create_auth_provider

        with pytest.raises(ValueError, match="jwt_service required"):
            create_auth_provider("internal")


# =============================================================================
# Auth Service (authenticate, create_user_with_password, change_password)
# =============================================================================


class TestAuthenticator:
    """Test Authenticator (email/password auth) with mocked DB."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    @pytest.fixture
    def authenticator(self, mock_session):
        """Create Authenticator with mocked session factory."""
        from src.auth.authenticator import Authenticator
        from src.auth.user_crud import UserCRUD

        session_factory = MagicMock(return_value=mock_session)
        user_crud = UserCRUD(session_factory)
        return Authenticator(session_factory, user_crud)

    def test_authenticate_success(self, authenticator, mock_session) -> None:
        """Successful authentication returns user dict."""
        from src.auth.models import UserModel
        from src.auth.password import hash_password

        mock_user = MagicMock(spec=UserModel)
        mock_user.id = "user-123"
        mock_user.email = "test@test.com"
        mock_user.display_name = "Test"
        mock_user.department = "IT"
        mock_user.organization_id = "org-1"
        mock_user.password_hash = hash_password("correct-password")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=result_mock)
        mock_session.commit = AsyncMock()

        result = _run(authenticator.authenticate("test@test.com", "correct-password"))
        assert result is not None
        assert result["id"] == "user-123"
        assert result["email"] == "test@test.com"

    def test_authenticate_wrong_password(self, authenticator, mock_session) -> None:
        """Wrong password returns None."""
        from src.auth.models import UserModel
        from src.auth.password import hash_password

        mock_user = MagicMock(spec=UserModel)
        mock_user.password_hash = hash_password("correct-password")

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_user
        mock_session.execute = AsyncMock(return_value=result_mock)

        result = _run(authenticator.authenticate("test@test.com", "wrong-password"))
        assert result is None

    def test_authenticate_user_not_found(self, authenticator, mock_session) -> None:
        """Non-existent user returns None (with constant-time comparison)."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=result_mock)

        result = _run(authenticator.authenticate("nonexistent@test.com", "any-password"))
        assert result is None
