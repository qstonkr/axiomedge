"""ChatPrivacyConsentRepository — accept / withdraw / re-accept lifecycle.

Real Postgres because PG-specific ON CONFLICT DO UPDATE is the conflict path
that re-accept depends on.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.stores.postgres.models import KnowledgeBase
from src.stores.postgres.repositories.consent_repo import ChatPrivacyConsentRepository


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST not set — real Postgres required",
)


@pytest.fixture
async def repo():
    url = os.environ["DATABASE_URL_TEST"]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(KnowledgeBase.metadata.drop_all)
        await conn.run_sync(KnowledgeBase.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield ChatPrivacyConsentRepository(sm)
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_accept_creates_active_row(repo):
    user_id = uuid.uuid4()
    rec = await repo.accept(
        user_id=user_id, org_id="o", policy_version="v1",
    )
    assert rec.is_active
    assert rec.withdrawn_at is None


@pytest.mark.asyncio
async def test_repeat_accept_is_idempotent(repo):
    user_id = uuid.uuid4()
    a = await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    b = await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    # Same row id; accepted_at preserved (not bumped on repeat-active)
    assert a.id == b.id
    assert a.accepted_at == b.accepted_at


@pytest.mark.asyncio
async def test_withdraw_marks_withdrawn_at(repo):
    user_id = uuid.uuid4()
    await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    rec = await repo.withdraw(user_id=user_id, policy_version="v1")
    assert rec is not None
    assert rec.withdrawn_at is not None
    assert rec.is_active is False


@pytest.mark.asyncio
async def test_withdraw_when_nothing_active_returns_none(repo):
    user_id = uuid.uuid4()
    rec = await repo.withdraw(user_id=user_id, policy_version="v1")
    assert rec is None


@pytest.mark.asyncio
async def test_withdraw_twice_second_returns_none(repo):
    """Idempotency floor — second withdraw is a no-op."""
    user_id = uuid.uuid4()
    await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    first = await repo.withdraw(user_id=user_id, policy_version="v1")
    second = await repo.withdraw(user_id=user_id, policy_version="v1")
    assert first is not None
    assert second is None  # already withdrawn


@pytest.mark.asyncio
async def test_re_accept_after_withdraw_clears_withdrawn_at(repo):
    user_id = uuid.uuid4()
    await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    await repo.withdraw(user_id=user_id, policy_version="v1")
    rec = await repo.accept(
        user_id=user_id, org_id="o", policy_version="v1",
        ip_address="10.0.0.1", user_agent="ua",
    )
    assert rec.is_active
    assert rec.withdrawn_at is None
    assert rec.ip_address == "10.0.0.1"


@pytest.mark.asyncio
async def test_get_active_for_user_filters_withdrawn(repo):
    user_id = uuid.uuid4()
    await repo.accept(user_id=user_id, org_id="o", policy_version="v1")
    assert (await repo.get_active_for_user(user_id, "v1")) is not None
    await repo.withdraw(user_id=user_id, policy_version="v1")
    assert (await repo.get_active_for_user(user_id, "v1")) is None
    # get_for_user still returns the row with the withdrawn marker
    rec = await repo.get_for_user(user_id, "v1")
    assert rec is not None
    assert rec.is_active is False
