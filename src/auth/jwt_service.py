"""JWT token creation and verification, compatible with oreo-ecosystem.

Token claim structure matches oreo-internal-api:
- Access token: sub, email, roles, permissions, jti, iss, type="access"
- Refresh token: sub, jti, family_id, rotation_count, iss, type="refresh"
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from src.auth.providers import AuthenticationError


@dataclass
class TokenPair:
    """Access + refresh token pair."""

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    token_type: str = "Bearer"


class JWTService:
    """JWT token creation and verification service."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_token_expire_minutes: int = 60,
        refresh_token_expire_hours: int = 8,
        issuer: str = "oreo-internal-api",
    ):
        self._secret = secret_key
        self._algorithm = algorithm
        self._access_expire = timedelta(minutes=access_token_expire_minutes)
        self._refresh_expire = timedelta(hours=refresh_token_expire_hours)
        self._issuer = issuer

    @property
    def access_expire_seconds(self) -> int:
        return int(self._access_expire.total_seconds())

    @property
    def refresh_expire_seconds(self) -> int:
        return int(self._refresh_expire.total_seconds())

    def create_token_pair(
        self,
        user_id: str,
        email: str,
        roles: list[str],
        permissions: list[str],
        family_id: str | None = None,
        rotation_count: int = 0,
        display_name: str = "",
    ) -> TokenPair:
        """Create access + refresh tokens with oreo-compatible claims."""
        now = datetime.now(timezone.utc)
        access_jti = str(uuid.uuid4())
        refresh_jti = str(uuid.uuid4())
        family_id = family_id or str(uuid.uuid4())

        access_payload = {
            "sub": user_id,
            "email": email,
            "display_name": display_name or email,
            "roles": roles,
            "permissions": permissions,
            "jti": access_jti,
            "iss": self._issuer,
            "iat": now,
            "exp": now + self._access_expire,
            "type": "access",
        }

        refresh_payload = {
            "sub": user_id,
            "jti": refresh_jti,
            "family_id": family_id,
            "rotation_count": rotation_count,
            "iss": self._issuer,
            "iat": now,
            "exp": now + self._refresh_expire,
            "type": "refresh",
        }

        return TokenPair(
            access_token=pyjwt.encode(access_payload, self._secret, algorithm=self._algorithm),
            refresh_token=pyjwt.encode(
                refresh_payload, self._secret, algorithm=self._algorithm
            ),
            access_expires_at=now + self._access_expire,
            refresh_expires_at=now + self._refresh_expire,
        )

    def verify_access_token(self, token: str) -> dict:
        """Verify and decode an access token. Raises AuthenticationError."""
        try:
            payload = pyjwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"verify_exp": True},
            )
            if payload.get("type") != "access":
                raise AuthenticationError("Not an access token")
            return payload
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("Token expired")
        except pyjwt.InvalidTokenError as e:
            raise AuthenticationError(f"Invalid token: {e}")

    def decode_refresh_token(self, token: str) -> dict:
        """Decode a refresh token (verify signature + expiry)."""
        try:
            payload = pyjwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"verify_exp": True},
            )
            if payload.get("type") != "refresh":
                raise AuthenticationError("Not a refresh token")
            return payload
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("Refresh token expired")
        except pyjwt.InvalidTokenError as e:
            raise AuthenticationError(f"Invalid refresh token: {e}")
