"""PostgreSQL-backed refresh token store.

Handles token family tracking, rotation, and revocation.
No Redis dependency — auth works with PostgreSQL alone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.auth.models import RefreshTokenModel

logger = logging.getLogger(__name__)


class TokenStore:
    """Refresh token lifecycle management backed by PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    async def store_refresh_token(
        self,
        jti: str,
        user_id: str,
        family_id: str,
        rotation_count: int,
        token_raw: str,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Store a new refresh token."""
        async with self._session() as session:
            token = RefreshTokenModel(
                id=jti,
                user_id=user_id,
                family_id=family_id,
                rotation_count=rotation_count,
                token_hash=hashlib.sha256(token_raw.encode()).hexdigest(),
                expires_at=expires_at,
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else None,
            )
            session.add(token)
            await session.commit()

    async def validate_and_rotate(self, jti: str, token_raw: str) -> dict | None:
        """Validate a refresh token and mark it as used (revoked).

        Returns token metadata if valid, None if revoked/invalid.
        When a revoked token is presented, the entire family is revoked
        (possible token reuse attack).
        """
        async with self._session() as session:
            result = await session.execute(
                select(RefreshTokenModel)
                .where(RefreshTokenModel.id == jti)
                .with_for_update()
            )
            token = result.scalar_one_or_none()

            if not token:
                return None

            # Already revoked → potential token reuse attack
            if token.revoked_at is not None:
                await self.revoke_family(token.family_id)
                return None

            # Expired
            if token.expires_at < datetime.now(timezone.utc):
                return None

            # Verify hash (constant-time comparison)
            expected_hash = hashlib.sha256(token_raw.encode()).hexdigest()
            if not hmac.compare_digest(token.token_hash, expected_hash):
                return None

            # Mark current token as rotated (revoked)
            token.revoked_at = datetime.now(timezone.utc)
            await session.commit()

            return {
                "user_id": token.user_id,
                "family_id": token.family_id,
                "rotation_count": token.rotation_count,
            }

    async def revoke_family(self, family_id: str) -> int:
        """Revoke all tokens in a family (logout or theft detection)."""
        async with self._session() as session:
            result = await session.execute(
                update(RefreshTokenModel)
                .where(
                    RefreshTokenModel.family_id == family_id,
                    RefreshTokenModel.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return result.rowcount

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """Revoke all refresh tokens for a user (password change, account lock)."""
        async with self._session() as session:
            result = await session.execute(
                update(RefreshTokenModel)
                .where(
                    RefreshTokenModel.user_id == user_id,
                    RefreshTokenModel.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return result.rowcount

    async def get_active_sessions(self, user_id: str) -> list[dict]:
        """List active (non-revoked, non-expired) sessions for a user."""
        now = datetime.now(timezone.utc)
        async with self._session() as session:
            result = await session.execute(
                select(RefreshTokenModel)
                .where(
                    RefreshTokenModel.user_id == user_id,
                    RefreshTokenModel.revoked_at.is_(None),
                    RefreshTokenModel.expires_at > now,
                )
                .order_by(RefreshTokenModel.created_at.desc())
            )
            return [
                {
                    "jti": t.id,
                    "family_id": t.family_id,
                    "ip_address": t.ip_address,
                    "user_agent": t.user_agent,
                    "created_at": str(t.created_at),
                    "expires_at": str(t.expires_at),
                }
                for t in result.scalars().all()
            ]

    async def cleanup_expired(self) -> int:
        """Delete expired tokens (maintenance job)."""
        from sqlalchemy import delete

        now = datetime.now(timezone.utc)
        async with self._session() as session:
            result = await session.execute(
                delete(RefreshTokenModel).where(RefreshTokenModel.expires_at < now)
            )
            await session.commit()
            return result.rowcount
