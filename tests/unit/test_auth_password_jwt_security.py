"""Security-focused tests for password hashing and JWT service.

Supplements test_auth_internal.py with edge cases critical for security:
- Unicode/multibyte passwords
- Token tampering
- Issuer validation
- Timing-related behavior
- Malformed inputs
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from src.auth.jwt_service import JWTService, TokenPair
from src.auth.password import BCRYPT_ROUNDS, hash_password, verify_password
from src.auth.providers import AuthenticationError


# =============================================================================
# Password Security Edge Cases
# =============================================================================


class TestPasswordUnicode:
    """Test password hashing with Unicode and multibyte characters."""

    def test_korean_password(self) -> None:
        """Korean characters should hash and verify."""
        pw = "비밀번호123!"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_japanese_password(self) -> None:
        pw = "パスワード"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_emoji_password(self) -> None:
        pw = "p@ss🔒🔑"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_mixed_script_password(self) -> None:
        """Password mixing Latin, Korean, numbers, and symbols."""
        pw = "Admin관리자!@#$%^&*()_+123"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True


class TestPasswordEdgeCases:
    """Test edge cases for password handling."""

    def test_very_long_password_raises(self) -> None:
        """bcrypt rejects passwords longer than 72 bytes."""
        pw = "A" * 100
        with pytest.raises(ValueError, match="password cannot be longer than 72 bytes"):
            hash_password(pw)

    def test_max_length_password(self) -> None:
        """72-byte password (bcrypt max) should work."""
        pw = "A" * 72
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_whitespace_only_password(self) -> None:
        pw = "   \t\n"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True
        assert verify_password("", hashed) is False

    def test_null_bytes_in_password(self) -> None:
        """Null bytes can be a security concern — verify handling."""
        # bcrypt implementations may handle null bytes differently
        pw = "pass\x00word"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_bcrypt_rounds_constant(self) -> None:
        """Verify BCRYPT_ROUNDS is the security-recommended value."""
        assert BCRYPT_ROUNDS == 12

    def test_hash_output_length(self) -> None:
        """bcrypt hashes should be 60 characters."""
        hashed = hash_password("test")
        assert len(hashed) == 60

    def test_invalid_hash_raises(self) -> None:
        """Invalid hash string should raise an error."""
        with pytest.raises(Exception):
            verify_password("password", "not-a-valid-hash")


# =============================================================================
# JWT Security Edge Cases
# =============================================================================


class TestJWTTokenTampering:
    """Test JWT token integrity against tampering."""

    def setup_method(self) -> None:
        self.secret = "test-secret-key-minimum-32-chars-long"
        self.service = JWTService(secret_key=self.secret)

    def test_modified_payload_rejected(self) -> None:
        """Modifying the payload without re-signing should fail."""
        pair = self.service.create_token_pair(
            user_id="user-1", email="a@b.com", roles=["viewer"], permissions=[]
        )
        # Tamper with the token by changing a character in the payload
        parts = pair.access_token.split(".")
        # Modify the payload part
        tampered_payload = parts[1] + "x"
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
        with pytest.raises(AuthenticationError, match="Invalid token"):
            self.service.verify_access_token(tampered_token)

    def test_empty_token_rejected(self) -> None:
        with pytest.raises(AuthenticationError, match="Invalid token"):
            self.service.verify_access_token("")

    def test_none_algorithm_attack(self) -> None:
        """Token with 'none' algorithm should be rejected."""
        payload = {
            "sub": "attacker",
            "type": "access",
            "iss": "axiomedge-api",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        # Create unsigned token
        token = pyjwt.encode(payload, "", algorithm="none")
        with pytest.raises(AuthenticationError):
            self.service.verify_access_token(token)


class TestJWTIssuerValidation:
    """Test issuer claim validation."""

    def test_wrong_issuer_rejected(self) -> None:
        """Token from a different issuer should be rejected."""
        svc1 = JWTService(secret_key="shared-secret-32-chars-long!!!!", issuer="service-a")
        svc2 = JWTService(secret_key="shared-secret-32-chars-long!!!!", issuer="service-b")

        pair = svc1.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        # Same secret but different issuer should fail
        with pytest.raises(AuthenticationError, match="Invalid token"):
            svc2.verify_access_token(pair.access_token)

    def test_default_issuer_matches_settings(self) -> None:
        svc = JWTService(secret_key="test-secret-32-chars-minimum!!!!")
        pair = svc.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        claims = svc.verify_access_token(pair.access_token)
        assert claims["iss"] == "axiomedge-api"


class TestJWTTokenPairStructure:
    """Test TokenPair structure and claims completeness."""

    def setup_method(self) -> None:
        self.secret = "test-secret-key-minimum-32-chars-long"
        self.service = JWTService(
            secret_key=self.secret,
            access_token_expire_minutes=30,
            refresh_token_expire_hours=4,
        )

    def test_token_pair_types(self) -> None:
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        assert isinstance(pair, TokenPair)
        assert isinstance(pair.access_token, str)
        assert isinstance(pair.refresh_token, str)
        assert isinstance(pair.access_expires_at, datetime)
        assert isinstance(pair.refresh_expires_at, datetime)

    def test_access_token_expires_before_refresh(self) -> None:
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        assert pair.access_expires_at < pair.refresh_expires_at

    def test_jti_uniqueness(self) -> None:
        """Each token pair should have unique JTIs."""
        pair1 = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        pair2 = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        claims1 = self.service.verify_access_token(pair1.access_token)
        claims2 = self.service.verify_access_token(pair2.access_token)
        assert claims1["jti"] != claims2["jti"]

        refresh1 = self.service.decode_refresh_token(pair1.refresh_token)
        refresh2 = self.service.decode_refresh_token(pair2.refresh_token)
        assert refresh1["jti"] != refresh2["jti"]

    def test_access_and_refresh_have_different_jti(self) -> None:
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        access = self.service.verify_access_token(pair.access_token)
        refresh = self.service.decode_refresh_token(pair.refresh_token)
        assert access["jti"] != refresh["jti"]

    def test_display_name_defaults_to_email(self) -> None:
        """When display_name is empty, it should default to email."""
        pair = self.service.create_token_pair(
            user_id="u1",
            email="user@example.com",
            roles=[],
            permissions=[],
            display_name="",
        )
        claims = self.service.verify_access_token(pair.access_token)
        assert claims["display_name"] == "user@example.com"

    def test_custom_expire_times(self) -> None:
        svc = JWTService(
            secret_key=self.secret,
            access_token_expire_minutes=15,
            refresh_token_expire_hours=2,
        )
        assert svc.access_expire_seconds == 15 * 60
        assert svc.refresh_expire_seconds == 2 * 3600


class TestJWTRefreshTokenSecurity:
    """Test refresh token security properties."""

    def setup_method(self) -> None:
        self.secret = "test-secret-key-minimum-32-chars-long"
        self.service = JWTService(secret_key=self.secret)

    def test_refresh_token_has_family_id(self) -> None:
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        claims = self.service.decode_refresh_token(pair.refresh_token)
        assert "family_id" in claims
        assert len(claims["family_id"]) > 0

    def test_rotation_count_preserved(self) -> None:
        pair = self.service.create_token_pair(
            user_id="u1",
            email="a@b.com",
            roles=[],
            permissions=[],
            family_id="fam-1",
            rotation_count=5,
        )
        claims = self.service.decode_refresh_token(pair.refresh_token)
        assert claims["rotation_count"] == 5

    def test_expired_refresh_token_rejected(self) -> None:
        svc = JWTService(
            secret_key=self.secret,
            refresh_token_expire_hours=0,
        )
        pair = svc.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        time.sleep(1)
        with pytest.raises(AuthenticationError, match="Refresh token expired"):
            svc.decode_refresh_token(pair.refresh_token)

    def test_wrong_secret_on_refresh(self) -> None:
        other = JWTService(secret_key="different-secret-key-also-32-chars")
        pair = self.service.create_token_pair(
            user_id="u1", email="a@b.com", roles=[], permissions=[]
        )
        with pytest.raises(AuthenticationError, match="Invalid refresh token"):
            other.decode_refresh_token(pair.refresh_token)

    def test_garbage_refresh_token(self) -> None:
        with pytest.raises(AuthenticationError, match="Invalid refresh token"):
            self.service.decode_refresh_token("garbage.token.here")

    def test_refresh_token_lacks_access_claims(self) -> None:
        """Refresh tokens should NOT contain email, roles, permissions."""
        pair = self.service.create_token_pair(
            user_id="u1",
            email="a@b.com",
            roles=["admin"],
            permissions=["*:*"],
        )
        claims = self.service.decode_refresh_token(pair.refresh_token)
        assert "email" not in claims
        assert "roles" not in claims
        assert "permissions" not in claims


class TestJWTTokenTypeEnforcement:
    """Test strict type enforcement between access and refresh tokens."""

    def setup_method(self) -> None:
        self.service = JWTService(secret_key="test-secret-key-minimum-32-chars-long")

    def test_crafted_token_with_wrong_type(self) -> None:
        """Manually crafted token with type=access should not pass as refresh."""
        payload = {
            "sub": "attacker",
            "jti": "fake-jti",
            "family_id": "fake-family",
            "rotation_count": 0,
            "iss": "axiomedge-api",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "type": "access",  # wrong type for refresh
        }
        token = pyjwt.encode(
            payload, "test-secret-key-minimum-32-chars-long", algorithm="HS256"
        )
        with pytest.raises(AuthenticationError, match="Not a refresh token"):
            self.service.decode_refresh_token(token)

    def test_crafted_token_with_missing_type(self) -> None:
        """Token without type field should be rejected."""
        payload = {
            "sub": "user-1",
            "iss": "axiomedge-api",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            # no "type" field
        }
        token = pyjwt.encode(
            payload, "test-secret-key-minimum-32-chars-long", algorithm="HS256"
        )
        with pytest.raises(AuthenticationError, match="Not an access token"):
            self.service.verify_access_token(token)
