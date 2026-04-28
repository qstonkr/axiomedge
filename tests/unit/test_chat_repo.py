import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.stores.postgres.models import KnowledgeBase
from src.stores.postgres.repositories.chat_repo import ChatRepository


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
    yield ChatRepository(sm, encryption_key="test-key-32-bytes-padded--------")
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_conversation_returns_id(repo):
    user_id = uuid.uuid4()
    conv_id = await repo.create_conversation(
        user_id=user_id, org_id="default-org", kb_ids=["g-espa"],
    )
    assert isinstance(conv_id, uuid.UUID)


@pytest.mark.asyncio
async def test_append_message_round_trip_encrypted(repo):
    user_id = uuid.uuid4()
    conv_id = await repo.create_conversation(
        user_id=user_id, org_id="default-org", kb_ids=[],
    )
    msg_id = await repo.append_message(
        conversation_id=conv_id,
        role="user",
        content="신촌점 차주 점검 알려줘",
        chunks=[],
        meta={},
    )
    msgs = await repo.list_messages(conv_id)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "신촌점 차주 점검 알려줘"
    assert msgs[0].id == msg_id


@pytest.mark.asyncio
async def test_list_conversations_excludes_deleted(repo):
    user_id = uuid.uuid4()
    keep = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    drop = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    await repo.soft_delete_conversation(drop, user_id)
    convs = await repo.list_conversations(user_id, limit=10, offset=0)
    ids = [c.id for c in convs]
    assert keep in ids
    assert drop not in ids


@pytest.mark.asyncio
async def test_rename_conversation_owner_only(repo):
    user_id = uuid.uuid4()
    other = uuid.uuid4()
    conv_id = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    ok = await repo.rename_conversation(conv_id, other, "hijack")
    assert ok is False
    ok = await repo.rename_conversation(conv_id, user_id, "신촌 점검")
    assert ok is True


@pytest.mark.asyncio
async def test_purge_older_than_hard_deletes(repo):
    """purge_older_than removes rows whose updated_at is older than cutoff."""
    user_id = uuid.uuid4()
    old = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    async with repo._session_maker() as session:
        await session.execute(
            text(
                "UPDATE chat_conversations "
                "SET created_at = now() - interval '100 days', "
                "    updated_at = now() - interval '100 days' "
                "WHERE id = :id",
            ),
            {"id": old},
        )
        await session.commit()
    deleted = await repo.purge_older_than(days=90)
    assert deleted >= 1


@pytest.mark.asyncio
async def test_purge_older_than_keeps_recently_active(repo):
    """A row whose created_at is old but updated_at is recent must survive."""
    user_id = uuid.uuid4()
    keep = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    async with repo._session_maker() as session:
        await session.execute(
            text(
                "UPDATE chat_conversations "
                "SET created_at = now() - interval '100 days' "
                "WHERE id = :id",
            ),
            {"id": keep},
        )
        await session.commit()
    deleted = await repo.purge_older_than(days=90)
    assert deleted == 0


@pytest.mark.asyncio
async def test_purge_older_than_rejects_short_retention(repo):
    """retention floor — refuse days < 7 to prevent accidents."""
    with pytest.raises(ValueError, match="retention floor"):
        await repo.purge_older_than(days=3)


@pytest.mark.asyncio
async def test_set_title_if_empty_skips_deleted(repo):
    """Auto-title race vs delete: deleted conv should not get a title set."""
    user_id = uuid.uuid4()
    conv_id = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    await repo.soft_delete_conversation(conv_id, user_id)
    ok = await repo.set_title_if_empty(conv_id, "should-not-stick")
    assert ok is False


@pytest.mark.asyncio
async def test_list_messages_user_id_blocks_other(repo):
    """Defense-in-depth: passing wrong user_id returns empty even if conv id is right."""
    owner = uuid.uuid4()
    other = uuid.uuid4()
    conv_id = await repo.create_conversation(user_id=owner, org_id="o", kb_ids=[])
    await repo.append_message(
        conversation_id=conv_id, role="user", content="hi", chunks=[], meta={},
    )
    own_msgs = await repo.list_messages(conv_id, user_id=owner)
    assert len(own_msgs) == 1
    other_msgs = await repo.list_messages(conv_id, user_id=other)
    assert other_msgs == []
