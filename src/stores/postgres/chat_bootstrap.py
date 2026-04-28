"""Shared chat schema bootstrap.

API lifespan and arq worker on_startup both call this so they share one
contract for "chat tables + pgcrypto are ready". Worker uses it as a
wait-loop — `init_database()` may still be running on the API side when
the worker starts, and we don't want chat jobs to land before the schema
exists.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Tables introduced by the chat redesign. The bootstrap helper waits for
# these to appear in pg_class before allowing the caller to proceed.
EXPECTED_CHAT_TABLES = (
    "chat_conversations",
    "chat_messages",
    "chat_privacy_consents",
)


async def ensure_pgcrypto(engine: AsyncEngine) -> None:
    """Idempotent — no-op if extension already present. Used by API init_db
    and the worker so a fresh dev DB doesn't surprise either side.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


async def chat_tables_exist(engine: AsyncEngine) -> bool:
    """True only when *all* expected chat tables are present in public schema."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT array_agg(tablename) "
                "FROM pg_tables "
                "WHERE schemaname = 'public' "
                "  AND tablename = ANY(:names)"
            ),
            {"names": list(EXPECTED_CHAT_TABLES)},
        )
        present = result.scalar_one() or []
    missing = set(EXPECTED_CHAT_TABLES) - set(present)
    if missing:
        logger.debug("chat schema check: missing %s", sorted(missing))
        return False
    return True


async def wait_for_chat_schema(
    engine: AsyncEngine,
    *,
    timeout_seconds: float = 60.0,
    poll_seconds: float = 2.0,
) -> bool:
    """Block until ``chat_tables_exist`` returns True, then ensure pgcrypto.

    Used by the worker so chat_jobs (auto_title, purge_sweep) never queue
    against a half-built schema. Returns True on success, False on timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        if await chat_tables_exist(engine):
            await ensure_pgcrypto(engine)
            return True
        if asyncio.get_event_loop().time() >= deadline:
            logger.error(
                "chat schema not ready after %.0fs — chat jobs will fail "
                "until init_database() completes on the API side",
                timeout_seconds,
            )
            return False
        await asyncio.sleep(poll_seconds)
