"""Revoked access-token jti store.

Refresh tokens are revoked via the DB-backed ``token_store`` (family revocation).
Access tokens have no DB row and were previously valid until expiry — so logout
left a window equal to ``access_token_expire_minutes``.

This module closes that gap: on logout (or admin revoke), the access token's jti
is recorded with TTL equal to its remaining lifetime. The auth provider checks
this store on every request.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

_KEY_PREFIX = "auth:revoked:jti:"


@runtime_checkable
class RevokedTokenStore(Protocol):
    """Records revoked access token jtis until natural expiry."""

    async def is_revoked(self, jti: str) -> bool: ...
    async def revoke(self, jti: str, ttl_seconds: int) -> None: ...


class RedisRevokedTokenStore:
    """Redis-backed implementation. Set TTL = remaining token lifetime so the
    key auto-expires once the token would have expired anyway."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def is_revoked(self, jti: str) -> bool:
        return bool(await self._redis.exists(f"{_KEY_PREFIX}{jti}"))

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return  # already expired
        await self._redis.setex(f"{_KEY_PREFIX}{jti}", ttl_seconds, "1")


class InMemoryRevokedTokenStore:
    """Process-local fallback for tests / single-instance dev. Not safe for
    multi-replica deployments — use Redis there."""

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}  # jti -> expiry timestamp

    async def is_revoked(self, jti: str) -> bool:
        now = time.time()
        # Lazy GC of expired entries
        if self._revoked:
            self._revoked = {k: v for k, v in self._revoked.items() if v > now}
        return jti in self._revoked

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        self._revoked[jti] = time.time() + ttl_seconds
