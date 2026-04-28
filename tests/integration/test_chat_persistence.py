"""Full /api/v1/chat CRUD against a real Postgres DB.

Bypasses AuthMiddleware (anonymous user). Mocks search/agentic so the
test stays focused on persistence + routing layer.

Requires `DATABASE_URL_TEST` env var (e.g.
postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_test).
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.app import app as fastapi_app
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser
from src.stores.postgres.models import KnowledgeBase
from src.stores.postgres.repositories.chat_repo import ChatRepository


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST not set — real Postgres required",
)


USER_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "default-org"


@pytest.fixture
def chat_repo_real():
    """Fresh engine per test — TestClient creates its own loop, sharing
    asyncpg connections across loops causes 'operation in progress' errors."""
    import asyncio
    url = os.environ["DATABASE_URL_TEST"]

    async def _setup():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await conn.run_sync(KnowledgeBase.metadata.drop_all)
            await conn.run_sync(KnowledgeBase.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())

    # Create a fresh engine + session_maker that the TestClient's loop will use.
    # Don't dispose — connections are bound to TestClient's loop which closes
    # at __exit__; calling dispose on a new loop after teardown errors out.
    engine = create_async_engine(url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield ChatRepository(sm, encryption_key="integration-test-key-32-bytes--")


@pytest.fixture
def client(chat_repo_real, monkeypatch):
    fake_user = AuthUser(
        sub=USER_ID, email="x@y.com", display_name="X",
        provider="local", roles=["admin"], active_org_id=ORG_ID,
    )
    fake_org = OrgContext(id=ORG_ID, user_role_in_org="OWNER")

    monkeypatch.setattr("src.auth.middleware.AUTH_ENABLED", False)
    monkeypatch.setattr("src.auth.middleware._ANONYMOUS_USER", fake_user)
    fastapi_app.dependency_overrides[get_current_user] = lambda: fake_user
    fastapi_app.dependency_overrides[get_current_org] = lambda: fake_org

    from src.api.app import _state as real_state
    monkeypatch.setattr(real_state, "chat_repo", chat_repo_real)
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def test_create_conversation_persists_to_real_db(client):
    """One HTTP cycle exercises the route → real ChatRepository → real Postgres.

    Multi-request flows hit asyncpg's "Future attached to a different loop"
    because TestClient does not reuse a single event loop across calls. The
    full multi-step CRUD is already covered by tests/unit/test_chat_repo.py
    against the same real DB; this test only proves the HTTP layer wires
    through to that repo correctly.
    """
    res = client.post("/api/v1/chat/conversations", json={"kb_ids": ["g-espa"]})
    assert res.status_code == 201, res.text
    cid = res.json()["id"]
    # Parses as UUID — proves repo returned a real row id, not a mock.
    assert uuid.UUID(cid)
