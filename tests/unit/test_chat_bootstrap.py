"""chat_bootstrap shared helper — pgcrypto + chat table existence wait."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.stores.postgres.chat_bootstrap import (
    EXPECTED_CHAT_TABLES,
    chat_tables_exist,
    ensure_pgcrypto,
    wait_for_chat_schema,
)
from src.stores.postgres.models import KnowledgeBase


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="real Postgres required",
)


@pytest.fixture
async def empty_engine():
    url = os.environ["DATABASE_URL_TEST"]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(KnowledgeBase.metadata.drop_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def ready_engine():
    url = os.environ["DATABASE_URL_TEST"]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(KnowledgeBase.metadata.drop_all)
        await conn.run_sync(KnowledgeBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_expected_tables_includes_consent():
    """Trip-wire: if a future chat table is added, the bootstrap must know."""
    assert "chat_conversations" in EXPECTED_CHAT_TABLES
    assert "chat_messages" in EXPECTED_CHAT_TABLES
    assert "chat_privacy_consents" in EXPECTED_CHAT_TABLES


@pytest.mark.asyncio
async def test_chat_tables_exist_returns_false_on_empty(empty_engine):
    assert await chat_tables_exist(empty_engine) is False


@pytest.mark.asyncio
async def test_chat_tables_exist_returns_true_after_create(ready_engine):
    assert await chat_tables_exist(ready_engine) is True


@pytest.mark.asyncio
async def test_ensure_pgcrypto_idempotent(ready_engine):
    # Second call must not raise even though extension already installed.
    await ensure_pgcrypto(ready_engine)
    await ensure_pgcrypto(ready_engine)


@pytest.mark.asyncio
async def test_wait_for_chat_schema_succeeds_when_ready(ready_engine):
    ok = await wait_for_chat_schema(ready_engine, timeout_seconds=2.0)
    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_chat_schema_times_out_when_empty(empty_engine):
    ok = await wait_for_chat_schema(
        empty_engine, timeout_seconds=0.5, poll_seconds=0.1,
    )
    assert ok is False
