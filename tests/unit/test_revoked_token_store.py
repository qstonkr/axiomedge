"""Tests for access-token jti revocation."""

from __future__ import annotations

import pytest

from src.auth.jwt_service import JWTService
from src.auth.providers import AuthenticationError, InternalAuthProvider
from src.auth.revoked_token_store import InMemoryRevokedTokenStore


@pytest.fixture
def jwt_svc() -> JWTService:
    return JWTService(secret_key="test-secret-do-not-use-in-prod", access_token_expire_minutes=5)


@pytest.fixture
def revoked_store() -> InMemoryRevokedTokenStore:
    return InMemoryRevokedTokenStore()


@pytest.mark.asyncio
async def test_in_memory_store_revoke_and_check(revoked_store: InMemoryRevokedTokenStore) -> None:
    assert await revoked_store.is_revoked("abc") is False
    await revoked_store.revoke("abc", ttl_seconds=60)
    assert await revoked_store.is_revoked("abc") is True


@pytest.mark.asyncio
async def test_in_memory_store_zero_ttl_is_no_op(revoked_store: InMemoryRevokedTokenStore) -> None:
    await revoked_store.revoke("zero", ttl_seconds=0)
    assert await revoked_store.is_revoked("zero") is False


@pytest.mark.asyncio
async def test_internal_provider_accepts_unrevoked_token(
    jwt_svc: JWTService, revoked_store: InMemoryRevokedTokenStore
) -> None:
    pair = jwt_svc.create_token_pair(
        user_id="user-1", email="u@example.com", roles=["viewer"], permissions=[]
    )
    provider = InternalAuthProvider(jwt_service=jwt_svc, revoked_token_store=revoked_store)
    user = await provider.verify_token(pair.access_token)
    assert user.sub == "user-1"


@pytest.mark.asyncio
async def test_internal_provider_rejects_revoked_token(
    jwt_svc: JWTService, revoked_store: InMemoryRevokedTokenStore
) -> None:
    pair = jwt_svc.create_token_pair(
        user_id="user-1", email="u@example.com", roles=["viewer"], permissions=[]
    )
    # Decode to get jti, then revoke
    claims = jwt_svc.verify_access_token(pair.access_token)
    await revoked_store.revoke(claims["jti"], ttl_seconds=60)

    provider = InternalAuthProvider(jwt_service=jwt_svc, revoked_token_store=revoked_store)
    with pytest.raises(AuthenticationError, match="revoked"):
        await provider.verify_token(pair.access_token)


@pytest.mark.asyncio
async def test_internal_provider_no_store_skips_check(jwt_svc: JWTService) -> None:
    """Backwards compat: when revoked_store is None, no revocation check happens."""
    pair = jwt_svc.create_token_pair(
        user_id="user-1", email="u@example.com", roles=["viewer"], permissions=[]
    )
    provider = InternalAuthProvider(jwt_service=jwt_svc, revoked_token_store=None)
    user = await provider.verify_token(pair.access_token)
    assert user.sub == "user-1"
