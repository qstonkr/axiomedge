"""Real PG round-trip — FeatureFlagRepository (C1 / P1-6).

upsert / get / list_all / delete + scope precedence. ``feature_flags:invalidate``
publish 는 별도 unit test (test_feature_flag_invalidation.py) 가 검증.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete

from src.stores.postgres.models import FeatureFlagModel
from src.stores.postgres.repositories.feature_flags import (
    FeatureFlagRepository,
)

pytestmark = pytest.mark.requires_postgres


async def _cleanup(pg_session_maker, name: str) -> None:
    async with pg_session_maker() as session:
        await session.execute(
            delete(FeatureFlagModel).where(FeatureFlagModel.name == name)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_upsert_and_get_global_scope(pg_session_maker):
    repo = FeatureFlagRepository(pg_session_maker)
    name = f"_TEST_FF_{datetime.now().timestamp():.0f}"
    try:
        ok = await repo.upsert(
            name=name, scope="_global", enabled=True,
            payload={"workers": 8}, updated_by="test",
        )
        assert ok is True

        row = await repo.get(name=name, scope="_global")
        assert row is not None
        assert row["enabled"] is True
        assert row["payload"] == {"workers": 8}
        assert row["updated_by"] == "test"
    finally:
        await _cleanup(pg_session_maker, name)


@pytest.mark.asyncio
async def test_upsert_idempotent_overwrite(pg_session_maker):
    repo = FeatureFlagRepository(pg_session_maker)
    name = f"_TEST_FF_OVR_{datetime.now().timestamp():.0f}"
    try:
        await repo.upsert(name=name, enabled=False, payload={"v": 1})
        await repo.upsert(name=name, enabled=True, payload={"v": 2})

        row = await repo.get(name=name, scope="_global")
        assert row["enabled"] is True
        assert row["payload"] == {"v": 2}
    finally:
        await _cleanup(pg_session_maker, name)


@pytest.mark.asyncio
async def test_scope_isolation(pg_session_maker):
    repo = FeatureFlagRepository(pg_session_maker)
    name = f"_TEST_FF_SCOPE_{datetime.now().timestamp():.0f}"
    try:
        await repo.upsert(name=name, scope="_global", enabled=False)
        await repo.upsert(name=name, scope="kb:k1", enabled=True)
        await repo.upsert(name=name, scope="org:o1", enabled=False)

        all_rows = [
            r for r in (await repo.list_all()) if r["name"] == name
        ]
        assert len(all_rows) == 3

        kb_row = await repo.get(name=name, scope="kb:k1")
        assert kb_row["enabled"] is True
        global_row = await repo.get(name=name, scope="_global")
        assert global_row["enabled"] is False
    finally:
        await _cleanup(pg_session_maker, name)


@pytest.mark.asyncio
async def test_delete_one(pg_session_maker):
    repo = FeatureFlagRepository(pg_session_maker)
    name = f"_TEST_FF_DEL_{datetime.now().timestamp():.0f}"
    try:
        await repo.upsert(name=name, scope="_global", enabled=True)
        n = await repo.delete_one(name=name, scope="_global")
        assert n == 1
        row = await repo.get(name=name, scope="_global")
        assert row is None
    finally:
        await _cleanup(pg_session_maker, name)
