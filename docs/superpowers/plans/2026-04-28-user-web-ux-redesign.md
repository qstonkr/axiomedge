# User Web UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/chat` user web as 3-pane (left history / center chat / right source panel) backed by persistent Postgres history, auto mode routing, and PIPA-compliant retention — absorbing 6 sibling pages into chat surface.

**Architecture:** New `chat_conversations` + `chat_messages` tables (pgcrypto-encrypted body, 90-day cron). New `/api/v1/chat/*` wrapper that calls existing `/search` or `/agentic/ask` based on a server-side mode router. Frontend rewrites `ChatPage` into a 3-pane layout, replaces sessionStorage with TanStack Query, adds `ConversationSidebar` / `SourcePanel` / `MessageActions` / slash command parser. Existing `/search` and `/agentic/ask` endpoints remain unchanged.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / asyncpg / pgcrypto / arq · Next.js 16 / React 19 / TanStack Query / zustand / Tailwind v4 · Playwright · Vitest · pytest.

**Spec:** [`docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md`](../specs/2026-04-28-user-web-ux-redesign-design.md)

---

## File Structure

### Backend (new / modified)

| Path | Role |
|---|---|
| `src/config/settings.py` | + `ChatSettings` (encryption_key, retention_days, auto_title_enabled) |
| `src/stores/postgres/models.py` | + `ChatConversationModel`, `ChatMessageModel` |
| `src/stores/postgres/init_db.py` | + `CREATE EXTENSION IF NOT EXISTS pgcrypto;` |
| `src/stores/postgres/repositories/chat_repo.py` | **new** — CRUD for conversations + messages, encrypt/decrypt |
| `src/api/routes/chat.py` | **new** — conversations + messages routes |
| `src/api/routes/chat_helpers.py` | **new** — `route_query`, encryption helpers |
| `src/jobs/chat_jobs.py` | **new** — `auto_title_for_conversation`, `chat_history_purge_sweep` |
| `src/jobs/tasks.py` | + register chat tasks |
| `src/jobs/worker.py` | + cron entry (purge daily, no auto-title cron — enqueued ad-hoc) |
| `tests/unit/test_chat_routes.py` | **new** |
| `tests/unit/test_chat_repo.py` | **new** |
| `tests/unit/test_chat_router_logic.py` | **new** — `route_query` signal mapping |
| `tests/unit/test_chat_jobs.py` | **new** — purge cron + auto-title |
| `tests/integration/test_chat_persistence.py` | **new** — full flow w/ real Postgres |

### Frontend (new / modified)

| Path | Role |
|---|---|
| `src/apps/web/src/lib/api/chat.ts` | **new** — fetch wrappers for `/chat/*` |
| `src/apps/web/src/store/conversations.ts` | **new** — TanStack Query hooks |
| `src/apps/web/src/store/chat.ts` | **modify** — drop sessionStorage, sync from server |
| `src/apps/web/src/components/chat/ConversationSidebar.tsx` | **new** |
| `src/apps/web/src/components/chat/ConversationItem.tsx` | **new** |
| `src/apps/web/src/components/chat/SourcePanel.tsx` | **new** — wraps existing SourceCard + MetaSignals as tabs |
| `src/apps/web/src/components/chat/MessageActions.tsx` | **new** |
| `src/apps/web/src/components/chat/SlashCommands.tsx` | **new** — autocomplete dropdown + parser |
| `src/apps/web/src/components/chat/CitationMarker.tsx` | **new** — `[N]` clickable element |
| `src/apps/web/src/components/chat/ModeForceMenu.tsx` | **new** — ⚙️ force_mode toggle |
| `src/apps/web/src/components/chat/ChatPage.tsx` | **rewrite** — 3-pane orchestrator |
| `src/apps/web/src/components/chat/ChatInput.tsx` | **modify** — slash hooks |
| `src/apps/web/src/components/chat/ChatMessages.tsx` | **modify** — citation markers + actions |
| `src/apps/web/src/components/chat/ModeToggle.tsx` | **delete** |
| `src/apps/web/src/components/layout/Sidebar.tsx` | **modify** — drop deleted routes, ProfileDropdown |
| `src/apps/web/src/components/layout/ProfileDropdown.tsx` | **new** |
| `src/apps/web/src/components/PrivacyConsent.tsx` | **new** — first-login modal |
| `src/apps/web/src/app/(app)/chat/[conversationId]/page.tsx` | **new** |
| `src/apps/web/src/app/(app)/search-history/page.tsx` | **delete** + 30d redirect via `proxy.ts` |
| `src/apps/web/src/app/(app)/find-owner/page.tsx` | **delete** + 30d redirect via `proxy.ts` |
| `src/apps/web/src/app/proxy.ts` | **modify** — redirect rules |
| `src/apps/web/tests/chat-flow.spec.ts` | **new** — Playwright E2E |

### Docs

| Path | Role |
|---|---|
| `docs/SECURITY.md` | + chat retention section |
| `docs/CONFIGURATION.md` | + `CHAT_*` env vars |
| `docs/API.md` | + `/chat/*` endpoints |

---

# PR 1 — Backend Foundation (DB + repo + router + wrapper API)

## Task 1: Add `ChatSettings` to config

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/config/__init__.py` (export)
- Test: `tests/unit/test_chat_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_settings.py
from src.config import get_settings


def test_chat_settings_defaults(monkeypatch):
    monkeypatch.delenv("CHAT_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("CHAT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("CHAT_AUTO_TITLE_ENABLED", raising=False)
    s = get_settings.__wrapped__()  # bypass lru_cache
    assert s.chat.retention_days == 90
    assert s.chat.auto_title_enabled is True
    assert s.chat.encryption_key == ""  # empty in dev — encryption skipped


def test_chat_settings_env(monkeypatch):
    monkeypatch.setenv("CHAT_ENCRYPTION_KEY", "test-key-32-bytes-aaaaaaaaaaaaaa")
    monkeypatch.setenv("CHAT_RETENTION_DAYS", "30")
    s = get_settings.__wrapped__()
    assert s.chat.retention_days == 30
    assert s.chat.encryption_key == "test-key-32-bytes-aaaaaaaaaaaaaa"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chat_settings.py -v --no-cov
```
Expected: FAIL — `Settings` has no attribute `chat`.

- [ ] **Step 3: Implement `ChatSettings`**

Add at the end of `src/config/settings.py` (just before the `Settings` aggregator class):

```python
class ChatSettings(BaseSettings):
    """Chat history persistence + privacy settings (PIPA-compliant)."""

    model_config = SettingsConfigDict(env_prefix="CHAT_", extra="ignore")

    encryption_key: str = ""  # pgp_sym_encrypt key — empty disables encryption (dev only)
    retention_days: int = 90  # purge cron deletes rows older than this
    auto_title_enabled: bool = True
    auto_title_max_tokens: int = 20
    auto_title_fallback_chars: int = 30
```

Then add to the aggregator `Settings` class (find existing pattern with `database`, `qdrant`, etc., e.g. around the bottom of the file):

```python
    chat: ChatSettings = Field(default_factory=ChatSettings)
```

Export in `src/config/__init__.py`:

```python
from src.config.settings import ChatSettings  # noqa: F401
```
(append to existing imports.)

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/unit/test_chat_settings.py -v --no-cov
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/config/settings.py src/config/__init__.py tests/unit/test_chat_settings.py
git commit -m "feat(config): ChatSettings — retention/encryption/auto-title knobs"
```

---

## Task 2: Add chat ORM models

**Files:**
- Modify: `src/stores/postgres/models.py`
- Test: `tests/unit/test_chat_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_models.py
from src.stores.postgres.models import ChatConversationModel, ChatMessageModel


def test_conversation_table_name():
    assert ChatConversationModel.__tablename__ == "chat_conversations"
    cols = {c.name for c in ChatConversationModel.__table__.columns}
    assert {"id", "user_id", "org_id", "title", "kb_ids",
            "created_at", "updated_at", "deleted_at"} <= cols


def test_message_table_name():
    assert ChatMessageModel.__tablename__ == "chat_messages"
    cols = {c.name for c in ChatMessageModel.__table__.columns}
    assert {"id", "conversation_id", "role", "content_enc",
            "chunks", "meta", "trace_id", "created_at"} <= cols


def test_message_role_check():
    """role column must be VARCHAR with check constraint user|assistant."""
    role_col = ChatMessageModel.__table__.columns["role"]
    constraints = [c for c in ChatMessageModel.__table__.constraints
                   if "role" in str(c).lower()]
    assert constraints, "role check constraint missing"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chat_models.py -v --no-cov
```
Expected: FAIL — `ImportError: cannot import name 'ChatConversationModel'`.

- [ ] **Step 3: Implement models**

Append to `src/stores/postgres/models.py` (just before any final `__all__` or end of file). Follow existing model patterns (`KnowledgeBase`, UUID PK, JSONB, server defaults):

```python
class ChatConversationModel(KnowledgeBase):
    """Persistent chat conversation — left sidebar item.

    Soft-deletable (deleted_at). Hard-deleted by chat_history_purge_sweep
    when older than CHAT_RETENTION_DAYS.
    """

    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    org_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    kb_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default=text("'{}'::text[]")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_chat_conv_user_active",
            "user_id", "deleted_at", "updated_at",
        ),
    )


class ChatMessageModel(KnowledgeBase):
    """Single chat turn (user OR assistant)."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant')",
            name="ck_chat_msg_role",
        ),
        Index("ix_chat_msg_conv_time", "conversation_id", "created_at"),
    )
```

If `ARRAY`, `LargeBinary`, `CheckConstraint`, or `ForeignKey` aren't already imported at the top of `models.py`, add them to the existing SQLAlchemy import block.

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/unit/test_chat_models.py -v --no-cov
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/stores/postgres/models.py tests/unit/test_chat_models.py
git commit -m "feat(postgres): ChatConversation/ChatMessage ORM models"
```

---

## Task 3: Enable pgcrypto on init

**Files:**
- Modify: `src/stores/postgres/init_db.py`
- Test: `tests/unit/test_init_db_pgcrypto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_init_db_pgcrypto.py
import inspect
import src.stores.postgres.init_db as init_db


def test_init_database_creates_pgcrypto_extension():
    """init_database must run CREATE EXTENSION IF NOT EXISTS pgcrypto."""
    src = inspect.getsource(init_db.init_database)
    assert "pgcrypto" in src.lower()
    assert "create extension" in src.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_init_db_pgcrypto.py -v --no-cov
```
Expected: FAIL — assertion error (pgcrypto not in source).

- [ ] **Step 3: Add extension creation**

In `src/stores/postgres/init_db.py`, find `init_database()`. Before any `Base.metadata.create_all` call, add:

```python
async with engine.begin() as conn:
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    logger.info("pgcrypto extension ensured")
```

(Import `text` from sqlalchemy if not already there.)

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/unit/test_init_db_pgcrypto.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stores/postgres/init_db.py tests/unit/test_init_db_pgcrypto.py
git commit -m "feat(postgres): enable pgcrypto extension on init for chat encryption"
```

---

## Task 4: Implement `ChatRepository`

**Files:**
- Create: `src/stores/postgres/repositories/chat_repo.py`
- Test: `tests/unit/test_chat_repo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_repo.py
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.stores.postgres.models import KnowledgeBase
from src.stores.postgres.repositories.chat_repo import ChatRepository


@pytest.fixture
async def repo(tmp_path):
    # SQLite in-memory does not support pgcrypto; use a process-local
    # Postgres test DB. CI provides DATABASE_URL_TEST.
    import os
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
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
    assert msgs[0].content == "신촌점 차주 점검 알려줘"  # decrypted
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
    assert ok is False  # not owner
    ok = await repo.rename_conversation(conv_id, user_id, "신촌 점검")
    assert ok is True


@pytest.mark.asyncio
async def test_purge_older_than_hard_deletes(repo):
    """purge_older_than removes rows whose created_at is older than cutoff."""
    user_id = uuid.uuid4()
    old = await repo.create_conversation(user_id=user_id, org_id="o", kb_ids=[])
    # backdate
    async with repo._session_maker() as session:
        await session.execute(text(
            "UPDATE chat_conversations SET created_at = now() - interval '100 days' "
            "WHERE id = :id"), {"id": old})
        await session.commit()
    deleted = await repo.purge_older_than(days=90)
    assert deleted >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DATABASE_URL_TEST=postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_test \
  uv run pytest tests/unit/test_chat_repo.py -v --no-cov
```
Expected: FAIL — `ChatRepository` not found.

- [ ] **Step 3: Implement `ChatRepository`**

```python
# src/stores/postgres/repositories/chat_repo.py
"""Chat conversations + messages repository.

Encrypts message body via pgcrypto pgp_sym_encrypt. If encryption_key is empty
(dev), stores plaintext bytes — never use empty key in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import (
    ChatConversationModel,
    ChatMessageModel,
)
from src.stores.postgres.repositories.base import BaseRepository


@dataclass
class DecodedMessage:
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    chunks: list[dict[str, Any]]
    meta: dict[str, Any]
    trace_id: str | None
    created_at: datetime


class ChatRepository(BaseRepository):
    def __init__(
        self,
        session_maker: async_sessionmaker,
        encryption_key: str,
    ) -> None:
        super().__init__(session_maker)
        self._key = encryption_key

    # --- conversation CRUD ----------------------------------------------

    async def create_conversation(
        self,
        *,
        user_id: uuid.UUID,
        org_id: str,
        kb_ids: list[str],
    ) -> uuid.UUID:
        async with self._session_maker() as session:
            async with session.begin():
                row = ChatConversationModel(
                    user_id=user_id,
                    org_id=org_id,
                    kb_ids=kb_ids,
                )
                session.add(row)
                await session.flush()
                return row.id

    async def list_conversations(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatConversationModel]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(ChatConversationModel)
                .where(
                    ChatConversationModel.user_id == user_id,
                    ChatConversationModel.deleted_at.is_(None),
                )
                .order_by(ChatConversationModel.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all())

    async def get_conversation(
        self, conv_id: uuid.UUID, user_id: uuid.UUID,
    ) -> ChatConversationModel | None:
        async with self._session_maker() as session:
            result = await session.execute(
                select(ChatConversationModel).where(
                    ChatConversationModel.id == conv_id,
                    ChatConversationModel.user_id == user_id,
                    ChatConversationModel.deleted_at.is_(None),
                )
            )
            return result.scalar_one_or_none()

    async def rename_conversation(
        self, conv_id: uuid.UUID, user_id: uuid.UUID, title: str,
    ) -> bool:
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(ChatConversationModel)
                    .where(
                        ChatConversationModel.id == conv_id,
                        ChatConversationModel.user_id == user_id,
                        ChatConversationModel.deleted_at.is_(None),
                    )
                    .values(title=title, updated_at=datetime.now(UTC))
                )
                return result.rowcount > 0

    async def set_title_if_empty(
        self, conv_id: uuid.UUID, title: str,
    ) -> bool:
        """Set title only if currently empty — used by auto-title task."""
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(ChatConversationModel)
                    .where(
                        ChatConversationModel.id == conv_id,
                        ChatConversationModel.title == "",
                    )
                    .values(title=title)
                )
                return result.rowcount > 0

    async def soft_delete_conversation(
        self, conv_id: uuid.UUID, user_id: uuid.UUID,
    ) -> bool:
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(ChatConversationModel)
                    .where(
                        ChatConversationModel.id == conv_id,
                        ChatConversationModel.user_id == user_id,
                        ChatConversationModel.deleted_at.is_(None),
                    )
                    .values(deleted_at=datetime.now(UTC))
                )
                return result.rowcount > 0

    # --- message CRUD ---------------------------------------------------

    async def append_message(
        self,
        *,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        chunks: list[dict[str, Any]],
        meta: dict[str, Any],
        trace_id: str | None = None,
    ) -> uuid.UUID:
        async with self._session_maker() as session:
            async with session.begin():
                if self._key:
                    enc = await session.scalar(
                        select(text("pgp_sym_encrypt(:body, :key)").bindparams(
                            body=content, key=self._key,
                        ))
                    )
                else:
                    enc = content.encode("utf-8")
                row = ChatMessageModel(
                    conversation_id=conversation_id,
                    role=role,
                    content_enc=enc,
                    chunks=chunks,
                    meta=meta,
                    trace_id=trace_id,
                )
                session.add(row)
                await session.flush()
                # bump conversation updated_at
                await session.execute(
                    update(ChatConversationModel)
                    .where(ChatConversationModel.id == conversation_id)
                    .values(updated_at=datetime.now(UTC))
                )
                return row.id

    async def list_messages(
        self, conversation_id: uuid.UUID,
    ) -> list[DecodedMessage]:
        async with self._session_maker() as session:
            if self._key:
                # decrypt via pgcrypto in SELECT
                result = await session.execute(
                    text("""
                        SELECT id, conversation_id, role,
                               pgp_sym_decrypt(content_enc, :key) AS content,
                               chunks, meta, trace_id, created_at
                        FROM chat_messages
                        WHERE conversation_id = :cid
                        ORDER BY created_at ASC
                    """).bindparams(cid=conversation_id, key=self._key)
                )
                return [
                    DecodedMessage(
                        id=r.id,
                        conversation_id=r.conversation_id,
                        role=r.role,
                        content=r.content,
                        chunks=r.chunks,
                        meta=r.meta,
                        trace_id=r.trace_id,
                        created_at=r.created_at,
                    )
                    for r in result.mappings().all()
                ]
            # plaintext fallback
            result = await session.execute(
                select(ChatMessageModel).where(
                    ChatMessageModel.conversation_id == conversation_id,
                ).order_by(ChatMessageModel.created_at.asc())
            )
            return [
                DecodedMessage(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    role=r.role,
                    content=r.content_enc.decode("utf-8"),
                    chunks=r.chunks,
                    meta=r.meta,
                    trace_id=r.trace_id,
                    created_at=r.created_at,
                )
                for r in result.scalars().all()
            ]

    # --- retention ------------------------------------------------------

    async def purge_older_than(self, days: int) -> int:
        """Hard delete conversations older than `days`. Cascades to messages."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ChatConversationModel).where(
                        ChatConversationModel.created_at < cutoff,
                    )
                )
                return result.rowcount or 0
```

- [ ] **Step 4: Bring up a test DB and run**

```bash
docker exec knowledge-local-postgres-1 psql -U knowledge -c \
  "CREATE DATABASE knowledge_test" || true
DATABASE_URL_TEST=postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_test \
  uv run pytest tests/unit/test_chat_repo.py -v --no-cov
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/stores/postgres/repositories/chat_repo.py tests/unit/test_chat_repo.py
git commit -m "feat(postgres): ChatRepository — pgcrypto-encrypted chat history CRUD"
```

---

## Task 5: Implement `route_query` mode classifier

**Files:**
- Create: `src/api/routes/chat_helpers.py`
- Test: `tests/unit/test_chat_router_logic.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_router_logic.py
import pytest
from src.api.routes.chat_helpers import RoutingSignals, route_query


@pytest.mark.parametrize("force,expected", [
    ("quick", "search"),
    ("deep", "agentic"),
])
def test_force_mode_overrides(force, expected):
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.0)
    assert route_query(sig, force_mode=force) == expected


def test_multi_intent_routes_agentic():
    sig = RoutingSignals(intent_count=2, requires_followup=False, ambiguity_score=0.0)
    assert route_query(sig, force_mode=None) == "agentic"


def test_followup_routes_agentic():
    sig = RoutingSignals(intent_count=1, requires_followup=True, ambiguity_score=0.0)
    assert route_query(sig, force_mode=None) == "agentic"


def test_high_ambiguity_routes_agentic():
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.7)
    assert route_query(sig, force_mode=None) == "agentic"


def test_simple_query_routes_search():
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.2)
    assert route_query(sig, force_mode=None) == "search"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chat_router_logic.py -v --no-cov
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `route_query`**

```python
# src/api/routes/chat_helpers.py
"""Helpers for /chat routes.

route_query: server-side mode router. Replaces user-facing ModeToggle.
Threshold values are explicit constants — adjust via config in v2 if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AMBIGUITY_AGENTIC_THRESHOLD = 0.6


@dataclass
class RoutingSignals:
    intent_count: int
    requires_followup: bool
    ambiguity_score: float


def route_query(
    signals: RoutingSignals,
    force_mode: Literal["quick", "deep", None] = None,
) -> Literal["search", "agentic"]:
    if force_mode == "quick":
        return "search"
    if force_mode == "deep":
        return "agentic"
    if signals.intent_count > 1:
        return "agentic"
    if signals.requires_followup:
        return "agentic"
    if signals.ambiguity_score > AMBIGUITY_AGENTIC_THRESHOLD:
        return "agentic"
    return "search"


async def derive_signals(query: str, classifier) -> RoutingSignals:
    """Adapter — pull existing classifier outputs into RoutingSignals.

    `classifier` is `QueryClassifier` from src.search. We tolerate missing
    fields (older classifier versions) and default conservatively.
    """
    out = await classifier.analyze(query)
    return RoutingSignals(
        intent_count=int(getattr(out, "intent_count", 1) or 1),
        requires_followup=bool(getattr(out, "requires_followup", False)),
        ambiguity_score=float(getattr(out, "ambiguity_score", 0.0) or 0.0),
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_chat_router_logic.py -v --no-cov
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/chat_helpers.py tests/unit/test_chat_router_logic.py
git commit -m "feat(api): route_query — server-side mode router (replaces ModeToggle)"
```

---

## Task 6: Wire `/chat` routes (conversations + messages wrapper)

**Files:**
- Create: `src/api/routes/chat.py`
- Modify: `src/api/state.py` (add `chat_repo` field)
- Modify: `src/api/app.py` (init `chat_repo` in lifespan)
- Test: `tests/unit/test_chat_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_routes.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app


@pytest.fixture
def app(monkeypatch):
    app = create_app()
    fake_repo = AsyncMock()
    fake_repo.create_conversation.return_value = uuid.UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    fake_repo.list_conversations.return_value = []
    app.state._app_state.chat_repo = fake_repo
    return app, fake_repo


def _login(client: TestClient) -> None:
    client.post("/api/v1/auth/login", json={
        "email": "admin@knowledge.local", "password": "dev1234!",
    })


def test_create_conversation_returns_id(app):
    app_inst, repo = app
    client = TestClient(app_inst)
    _login(client)
    res = client.post("/api/v1/chat/conversations", json={"kb_ids": ["g-espa"]})
    assert res.status_code == 201
    assert res.json()["id"] == "11111111-1111-1111-1111-111111111111"
    repo.create_conversation.assert_awaited_once()


def test_list_conversations_empty(app):
    app_inst, _ = app
    client = TestClient(app_inst)
    _login(client)
    res = client.get("/api/v1/chat/conversations")
    assert res.status_code == 200
    assert res.json()["conversations"] == []


def test_rename_conversation_404_when_not_owner(app):
    app_inst, repo = app
    repo.rename_conversation.return_value = False
    client = TestClient(app_inst)
    _login(client)
    cid = "11111111-1111-1111-1111-111111111111"
    res = client.patch(f"/api/v1/chat/conversations/{cid}",
                       json={"title": "x"})
    assert res.status_code == 404


def test_send_message_routes_through_search(app, monkeypatch):
    """Mode auto-routes to /search when query simple."""
    app_inst, repo = app
    repo.append_message.return_value = uuid.UUID(
        "22222222-2222-2222-2222-222222222222"
    )
    repo.get_conversation.return_value = MagicMock(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        kb_ids=["g-espa"],
    )

    fake_search = AsyncMock(return_value={
        "answer": "8s 답변",
        "chunks": [{"doc_id": "d1", "chunk_id": "c1"}],
        "metadata": {},
        "confidence": 0.8,
    })
    fake_classifier = AsyncMock()
    fake_classifier.analyze.return_value = MagicMock(
        intent_count=1, requires_followup=False, ambiguity_score=0.1,
    )
    app_inst.state._app_state.query_classifier = fake_classifier
    app_inst.state._app_state.rag_pipeline = MagicMock(search=fake_search)

    client = TestClient(app_inst)
    _login(client)
    cid = "11111111-1111-1111-1111-111111111111"
    res = client.post(
        f"/api/v1/chat/conversations/{cid}/messages",
        json={"content": "신촌점 차주 점검?"},
    )
    assert res.status_code == 200
    fake_search.assert_awaited_once()
    # Two append_message calls — user + assistant.
    assert repo.append_message.await_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chat_routes.py -v --no-cov
```
Expected: FAIL — `chat` module not found.

- [ ] **Step 3: Add `chat_repo` to AppState**

In `src/api/state.py` `AppState` dataclass, in the "Database & Repositories" section, add:

```python
    chat_repo: ChatRepository | None = None
```

Add the import at the top with other repo imports:

```python
from src.stores.postgres.repositories.chat_repo import ChatRepository
```

- [ ] **Step 4: Init `chat_repo` in app lifespan**

In `src/api/app.py`, find the existing block where repos are initialized in lifespan (around `_state.kb_registry = ...`). Add:

```python
from src.stores.postgres.repositories.chat_repo import ChatRepository
_state.chat_repo = ChatRepository(
    session_maker=_state.db_session_factory,
    encryption_key=settings.chat.encryption_key,
)
```

- [ ] **Step 5: Implement `/chat` router**

```python
# src/api/routes/chat.py
"""Chat surface API — conversations + messages.

Conversations: persistent left sidebar history.
Messages POST is a wrapper that auto-routes to /search or /agentic/ask.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.errors import api_error
from src.api.routes.chat_helpers import derive_signals, route_query
from src.auth.deps import require_user
from src.auth.types import AuthenticatedUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


def _get_repo(request: Request):
    state = request.app.state._app_state
    if state.chat_repo is None:
        raise HTTPException(503, detail="Chat service not initialized")
    return state.chat_repo


# --- request/response models ---------------------------------------------


class CreateConversationRequest(BaseModel):
    kb_ids: list[str] = Field(default_factory=list)


class CreateConversationResponse(BaseModel):
    id: str


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    force_mode: str | None = None  # 'quick' | 'deep' | None


class ConversationView(BaseModel):
    id: str
    title: str
    kb_ids: list[str]
    updated_at: str


class MessageView(BaseModel):
    id: str
    role: str
    content: str
    chunks: list[dict[str, Any]]
    meta: dict[str, Any]
    trace_id: str | None
    created_at: str


# --- routes --------------------------------------------------------------


@router.post(
    "/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: CreateConversationRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    conv_id = await repo.create_conversation(
        user_id=uuid.UUID(user.id),
        org_id=user.active_org_id or "default-org",
        kb_ids=body.kb_ids,
    )
    return {"id": str(conv_id)}


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    rows = await repo.list_conversations(uuid.UUID(user.id), limit, offset)
    return {
        "conversations": [
            ConversationView(
                id=str(r.id),
                title=r.title,
                kb_ids=list(r.kb_ids),
                updated_at=r.updated_at.isoformat(),
            ).model_dump()
            for r in rows
        ],
    }


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: uuid.UUID,
    body: RenameRequest,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    ok = await repo.rename_conversation(conv_id, uuid.UUID(user.id), body.title)
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    return {"status": "ok"}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    ok = await repo.soft_delete_conversation(conv_id, uuid.UUID(user.id))
    if not ok:
        raise HTTPException(404, detail="Conversation not found")
    return {"status": "ok"}


@router.get("/conversations/{conv_id}/messages")
async def list_messages(
    conv_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    conv = await repo.get_conversation(conv_id, uuid.UUID(user.id))
    if conv is None:
        raise HTTPException(404, detail="Conversation not found")
    msgs = await repo.list_messages(conv_id)
    return {
        "messages": [
            MessageView(
                id=str(m.id),
                role=m.role,
                content=m.content,
                chunks=m.chunks,
                meta=m.meta,
                trace_id=m.trace_id,
                created_at=m.created_at.isoformat(),
            ).model_dump()
            for m in msgs
        ],
    }


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: uuid.UUID,
    body: SendMessageRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
    repo=Depends(_get_repo),
):
    state = request.app.state._app_state
    conv = await repo.get_conversation(conv_id, uuid.UUID(user.id))
    if conv is None:
        raise HTTPException(404, detail="Conversation not found")

    # Save user turn
    await repo.append_message(
        conversation_id=conv_id, role="user",
        content=body.content, chunks=[], meta={},
    )

    # Route
    classifier = state.query_classifier
    sig = await derive_signals(body.content, classifier)
    mode = route_query(sig, force_mode=body.force_mode)

    if mode == "search":
        res = await state.rag_pipeline.search(
            query=body.content,
            kb_ids=list(conv.kb_ids) or None,
            top_k=8,
            include_answer=True,
        )
        answer = res.get("answer") or ""
        chunks = res.get("chunks") or []
        meta = res.get("metadata") or {}
        trace_id = None
    else:
        # agentic
        res = await state.rag_pipeline.agentic_ask(
            query=body.content, kb_ids=list(conv.kb_ids) or None,
        )
        answer = res.get("answer") or ""
        chunks = []
        meta = {
            "confidence": res.get("confidence"),
            "iteration_count": res.get("iteration_count"),
            "estimated_cost_usd": res.get("estimated_cost_usd"),
            "llm_provider": res.get("llm_provider"),
        }
        trace_id = res.get("trace_id")

    # Save assistant turn
    msg_id = await repo.append_message(
        conversation_id=conv_id, role="assistant",
        content=answer, chunks=chunks, meta=meta, trace_id=trace_id,
    )

    return {
        "id": str(msg_id),
        "role": "assistant",
        "content": answer,
        "chunks": chunks,
        "meta": meta,
        "trace_id": trace_id,
        "mode_used": mode,
    }
```

- [ ] **Step 6: Run unit tests to verify pass**

```bash
uv run pytest tests/unit/test_chat_routes.py tests/unit/test_chat_router_logic.py -v --no-cov
```
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/chat.py src/api/state.py src/api/app.py tests/unit/test_chat_routes.py
git commit -m "feat(api): /chat routes — conversations CRUD + auto-routed messages wrapper"
```

---

## Task 7: Backend integration test (real Postgres flow)

**Files:**
- Create: `tests/integration/test_chat_persistence.py`

- [ ] **Step 1: Write end-to-end backend test**

```python
# tests/integration/test_chat_persistence.py
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="real DB required",
)


def _login(client: TestClient) -> None:
    client.post("/api/v1/auth/login", json={
        "email": "admin@knowledge.local", "password": "dev1234!",
    })


def test_full_chat_flow(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL_TEST"])
    app = create_app()
    client = TestClient(app)
    _login(client)

    res = client.post("/api/v1/chat/conversations", json={"kb_ids": []})
    assert res.status_code == 201
    cid = res.json()["id"]

    # rename
    r2 = client.patch(f"/api/v1/chat/conversations/{cid}",
                      json={"title": "테스트"})
    assert r2.status_code == 200

    # listing
    r3 = client.get("/api/v1/chat/conversations")
    assert r3.status_code == 200
    titles = [c["title"] for c in r3.json()["conversations"]]
    assert "테스트" in titles

    # delete
    r4 = client.delete(f"/api/v1/chat/conversations/{cid}")
    assert r4.status_code == 200

    r5 = client.get("/api/v1/chat/conversations")
    titles = [c["title"] for c in r5.json()["conversations"]]
    assert "테스트" not in titles
```

- [ ] **Step 2: Run integration test**

```bash
DATABASE_URL_TEST=postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_test \
  uv run pytest tests/integration/test_chat_persistence.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 3: Commit + open PR 1**

```bash
git add tests/integration/test_chat_persistence.py
git commit -m "test(integration): chat conversations full flow"
git push -u origin HEAD
gh pr create --title "feat(chat): backend foundation — DB + repo + router (PR1/5)" \
  --body "$(cat <<'EOF'
## Summary
- ChatSettings + pgcrypto enabled on init
- ChatConversationModel + ChatMessageModel ORM
- ChatRepository (encrypted body, soft-delete, retention purge)
- /api/v1/chat/conversations CRUD + auto-routed /messages wrapper
- route_query mode router (replaces ModeToggle)

Spec: docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md
Plan: docs/superpowers/plans/2026-04-28-user-web-ux-redesign.md

## Test plan
- [x] unit tests (settings, models, repo, router logic, routes)
- [x] integration test (real Postgres CRUD flow)
- [ ] manual: hit /api/v1/chat/conversations after merge
EOF
)"
```

---

# PR 2 — Frontend Foundation (API client + queries + sidebar/source panel)

## Task 8: Add chat API client

**Files:**
- Create: `src/apps/web/src/lib/api/chat.ts`
- Test: `src/apps/web/tests/unit/api-chat.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
// src/apps/web/tests/unit/api-chat.test.ts
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  createConversation,
  listConversations,
  renameConversation,
  deleteConversation,
  listMessages,
  sendMessage,
} from "@/lib/api/chat";

afterEach(() => { vi.restoreAllMocks(); });

function mockFetch(payload: unknown, status = 200) {
  return vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    }) as unknown as Response,
  );
}

describe("chat api", () => {
  it("createConversation hits POST /chat/conversations", async () => {
    const fetchSpy = mockFetch({ id: "conv-1" }, 201);
    const id = await createConversation({ kb_ids: ["g-espa"] });
    expect(id).toBe("conv-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/proxy\/api\/v1\/chat\/conversations$/),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("listConversations parses response", async () => {
    mockFetch({ conversations: [{ id: "c1", title: "x", kb_ids: [], updated_at: "now" }] });
    const xs = await listConversations();
    expect(xs[0].id).toBe("c1");
  });

  it("renameConversation throws on 404", async () => {
    mockFetch({ detail: "Conversation not found" }, 404);
    await expect(renameConversation("c1", "t")).rejects.toThrow();
  });

  it("deleteConversation succeeds on 200", async () => {
    mockFetch({ status: "ok" });
    await deleteConversation("c1");
  });

  it("listMessages parses chunks/meta", async () => {
    mockFetch({ messages: [{ id: "m1", role: "assistant", content: "hi",
      chunks: [], meta: { confidence: 0.5 }, trace_id: null, created_at: "now" }] });
    const xs = await listMessages("c1");
    expect(xs[0].meta.confidence).toBe(0.5);
  });

  it("sendMessage forwards force_mode", async () => {
    const spy = mockFetch({ id: "m2", role: "assistant", content: "ok",
      chunks: [], meta: {}, trace_id: null, mode_used: "agentic" });
    await sendMessage("c1", { content: "Q", force_mode: "deep" });
    const body = JSON.parse(spy.mock.calls[0][1]!.body as string);
    expect(body.force_mode).toBe("deep");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/api-chat.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement client**

```ts
// src/apps/web/src/lib/api/chat.ts
const BASE = "/api/proxy/api/v1/chat";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Conversation = {
  id: string;
  title: string;
  kb_ids: string[];
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  chunks: Array<Record<string, unknown>>;
  meta: Record<string, unknown>;
  trace_id: string | null;
  created_at: string;
};

export type SendResult = ChatMessage & { mode_used: "search" | "agentic" };

export async function createConversation(
  body: { kb_ids: string[] },
): Promise<string> {
  const r = await jsonFetch<{ id: string }>("/conversations", {
    method: "POST", body: JSON.stringify(body),
  });
  return r.id;
}

export async function listConversations(): Promise<Conversation[]> {
  const r = await jsonFetch<{ conversations: Conversation[] }>("/conversations");
  return r.conversations;
}

export async function renameConversation(id: string, title: string): Promise<void> {
  await jsonFetch(`/conversations/${id}`, {
    method: "PATCH", body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await jsonFetch(`/conversations/${id}`, { method: "DELETE" });
}

export async function listMessages(id: string): Promise<ChatMessage[]> {
  const r = await jsonFetch<{ messages: ChatMessage[] }>(
    `/conversations/${id}/messages`,
  );
  return r.messages;
}

export async function sendMessage(
  id: string,
  body: { content: string; force_mode?: "quick" | "deep" | null },
): Promise<SendResult> {
  return jsonFetch<SendResult>(`/conversations/${id}/messages`, {
    method: "POST", body: JSON.stringify(body),
  });
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/api-chat.test.ts
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/lib/api/chat.ts src/apps/web/tests/unit/api-chat.test.ts
git commit -m "feat(web): chat API client (conversations + messages)"
```

---

## Task 9: TanStack Query hooks (`useConversations`, `useMessages`, `useSendMessage`)

**Files:**
- Create: `src/apps/web/src/store/conversations.ts`
- Test: `src/apps/web/tests/unit/use-conversations.test.tsx`

- [ ] **Step 1: Write failing tests**

```tsx
// src/apps/web/tests/unit/use-conversations.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useConversations, useSendMessage } from "@/store/conversations";

vi.mock("@/lib/api/chat", () => ({
  listConversations: vi.fn().mockResolvedValue([{
    id: "c1", title: "x", kb_ids: [], updated_at: "now",
  }]),
  sendMessage: vi.fn().mockResolvedValue({
    id: "m1", role: "assistant", content: "hi",
    chunks: [], meta: {}, trace_id: null, mode_used: "search",
  }),
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("useConversations", () => {
  it("fetches list", async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrap() });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data![0].id).toBe("c1");
  });
});

describe("useSendMessage", () => {
  it("invalidates conversations on success", async () => {
    const { result } = renderHook(() => useSendMessage("c1"), { wrapper: wrap() });
    await result.current.mutateAsync({ content: "Q" });
    expect(result.current.isSuccess).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/use-conversations.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement hooks**

```ts
// src/apps/web/src/store/conversations.ts
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  renameConversation,
  sendMessage,
  type ChatMessage,
  type Conversation,
  type SendResult,
} from "@/lib/api/chat";

const KEYS = {
  conversations: ["chat", "conversations"] as const,
  messages: (id: string) => ["chat", "messages", id] as const,
};

export function useConversations() {
  return useQuery<Conversation[]>({
    queryKey: KEYS.conversations,
    queryFn: listConversations,
    staleTime: 30_000,
  });
}

export function useMessages(id: string | null) {
  return useQuery<ChatMessage[]>({
    queryKey: id ? KEYS.messages(id) : ["chat", "messages", "none"],
    queryFn: () => (id ? listMessages(id) : Promise.resolve([])),
    enabled: !!id,
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createConversation,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      renameConversation(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEYS.conversations }),
  });
}

export function useSendMessage(id: string | null) {
  const qc = useQueryClient();
  return useMutation<SendResult, Error, { content: string; force_mode?: "quick" | "deep" | null }>({
    mutationFn: (body) => {
      if (!id) throw new Error("conversation id required");
      return sendMessage(id, body);
    },
    onSuccess: () => {
      if (!id) return;
      qc.invalidateQueries({ queryKey: KEYS.messages(id) });
      qc.invalidateQueries({ queryKey: KEYS.conversations });
    },
  });
}

export const conversationsKeys = KEYS;
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/use-conversations.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/store/conversations.ts src/apps/web/tests/unit/use-conversations.test.tsx
git commit -m "feat(web): TanStack Query hooks for chat conversations/messages"
```

---

## Task 10: Refactor `useChatStore` — drop sessionStorage

**Files:**
- Modify: `src/apps/web/src/store/chat.ts`
- Test: `src/apps/web/tests/unit/chat-store.test.ts`

- [ ] **Step 1: Write failing test**

```ts
// src/apps/web/tests/unit/chat-store.test.ts
import { describe, expect, it, beforeEach } from "vitest";
import { useChatStore } from "@/store/chat";

describe("useChatStore (no sessionStorage)", () => {
  beforeEach(() => {
    useChatStore.getState().resetForConversation(null);
  });

  it("starts empty", () => {
    expect(useChatStore.getState().turns).toEqual([]);
  });

  it("resetForConversation sets active id and clears turns", () => {
    useChatStore.getState().appendTurn({
      kind: "user", id: "u1", query: "hi",
    });
    useChatStore.getState().resetForConversation("conv-1");
    expect(useChatStore.getState().activeConversationId).toBe("conv-1");
    expect(useChatStore.getState().turns).toEqual([]);
  });

  it("does NOT persist to sessionStorage", () => {
    sessionStorage.clear();
    useChatStore.getState().appendTurn({
      kind: "user", id: "u1", query: "hi",
    });
    // No keys should land in sessionStorage from this store.
    const keys = Object.keys(sessionStorage);
    expect(keys.filter((k) => k.startsWith("chat-store"))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run to verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/chat-store.test.ts
```
Expected: FAIL — `resetForConversation` not exported / sessionStorage still present.

- [ ] **Step 3: Refactor store**

Find `src/apps/web/src/store/chat.ts`. Replace its contents with:

```ts
"use client";

import { create } from "zustand";

import type { AssistantTurn, UserTurn } from "@/components/chat/types";

type Turn = UserTurn | AssistantTurn;

type ChatStore = {
  activeConversationId: string | null;
  turns: Turn[];
  selectedKbIds: string[];
  // mode is no longer user-toggleable; kept as derived display only
  resetForConversation: (id: string | null) => void;
  appendTurn: (turn: Turn) => void;
  hydrateTurns: (turns: Turn[]) => void;
  setSelectedKbIds: (ids: string[]) => void;
};

export const useChatStore = create<ChatStore>((set) => ({
  activeConversationId: null,
  turns: [],
  selectedKbIds: [],
  resetForConversation: (id) => set({ activeConversationId: id, turns: [] }),
  appendTurn: (turn) => set((s) => ({ turns: [...s.turns, turn] })),
  hydrateTurns: (turns) => set({ turns }),
  setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),
}));
```

(Remove any sessionStorage middleware / persist wrapper that was present.)

- [ ] **Step 4: Run tests to verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/chat-store.test.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/store/chat.ts src/apps/web/tests/unit/chat-store.test.ts
git commit -m "refactor(web): drop sessionStorage from useChatStore — server is SoT"
```

---

## Task 11: `ConversationSidebar` component

**Files:**
- Create: `src/apps/web/src/components/chat/ConversationItem.tsx`
- Create: `src/apps/web/src/components/chat/ConversationSidebar.tsx`
- Test: `src/apps/web/tests/unit/conversation-sidebar.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/conversation-sidebar.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConversationSidebar } from "@/components/chat/ConversationSidebar";

vi.mock("@/store/conversations", () => ({
  useConversations: () => ({
    data: [
      { id: "c1", title: "신촌 점검", kb_ids: [], updated_at: new Date().toISOString() },
      { id: "c2", title: "MD 업무", kb_ids: [], updated_at: new Date(Date.now() - 86400000).toISOString() },
    ],
    isLoading: false,
  }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn() }),
  useRenameConversation: () => ({ mutateAsync: vi.fn() }),
  useCreateConversation: () => ({ mutateAsync: vi.fn().mockResolvedValue("new-id") }),
}));

function wrap() {
  const qc = new QueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("ConversationSidebar", () => {
  it("groups by 오늘/어제", () => {
    render(<ConversationSidebar activeId={null} />, { wrapper: wrap() });
    expect(screen.getByText("오늘")).toBeInTheDocument();
    expect(screen.getByText("어제")).toBeInTheDocument();
  });

  it("filters by search box", async () => {
    render(<ConversationSidebar activeId={null} />, { wrapper: wrap() });
    const u = userEvent.setup();
    await u.type(screen.getByRole("searchbox"), "신촌");
    expect(screen.getByText("신촌 점검")).toBeInTheDocument();
    expect(screen.queryByText("MD 업무")).not.toBeInTheDocument();
  });

  it("calls onSelect when item clicked", async () => {
    const onSelect = vi.fn();
    render(<ConversationSidebar activeId={null} onSelect={onSelect} />, { wrapper: wrap() });
    const u = userEvent.setup();
    await u.click(screen.getByText("신촌 점검"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });
});
```

- [ ] **Step 2: Run to verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/conversation-sidebar.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ConversationItem`**

```tsx
// src/apps/web/src/components/chat/ConversationItem.tsx
"use client";

import { useState } from "react";

import { cn } from "@/components/ui/cn";
import { useDeleteConversation, useRenameConversation } from "@/store/conversations";

export function ConversationItem({
  id, title, active, onSelect,
}: {
  id: string;
  title: string;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const rename = useRenameConversation();
  const remove = useDeleteConversation();

  async function commitRename() {
    setEditing(false);
    if (draft.trim() && draft !== title) {
      await rename.mutateAsync({ id, title: draft.trim() });
    } else {
      setDraft(title);
    }
  }

  return (
    <div
      className={cn(
        "group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm",
        active ? "bg-bg-emphasis" : "hover:bg-bg-muted",
      )}
    >
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") { setEditing(false); setDraft(title); }
          }}
          className="flex-1 bg-transparent outline-none"
        />
      ) : (
        <button
          onClick={() => onSelect(id)}
          className="flex-1 truncate text-left"
        >
          {title || "(제목 없음)"}
        </button>
      )}
      <button
        aria-label="이름 변경"
        onClick={(e) => { e.stopPropagation(); setEditing(true); }}
        className="opacity-0 group-hover:opacity-100"
      >✏️</button>
      <button
        aria-label="삭제"
        onClick={async (e) => {
          e.stopPropagation();
          if (confirm("이 대화를 삭제할까요?")) await remove.mutateAsync(id);
        }}
        className="opacity-0 group-hover:opacity-100"
      >🗑️</button>
    </div>
  );
}
```

- [ ] **Step 4: Implement `ConversationSidebar`**

```tsx
// src/apps/web/src/components/chat/ConversationSidebar.tsx
"use client";

import { useMemo, useState } from "react";

import { useConversations, useCreateConversation } from "@/store/conversations";
import type { Conversation } from "@/lib/api/chat";

import { ConversationItem } from "./ConversationItem";

type Bucket = { label: string; items: Conversation[] };

function bucketize(items: Conversation[]): Bucket[] {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);
  const buckets: Bucket[] = [
    { label: "오늘", items: [] },
    { label: "어제", items: [] },
    { label: "이번 주", items: [] },
    { label: "이전", items: [] },
  ];
  for (const c of items) {
    const t = new Date(c.updated_at);
    if (t >= today) buckets[0].items.push(c);
    else if (t >= yesterday) buckets[1].items.push(c);
    else if (t >= weekAgo) buckets[2].items.push(c);
    else buckets[3].items.push(c);
  }
  return buckets.filter((b) => b.items.length > 0);
}

export function ConversationSidebar({
  activeId, onSelect,
}: {
  activeId: string | null;
  onSelect?: (id: string) => void;
}) {
  const { data = [], isLoading } = useConversations();
  const create = useCreateConversation();
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const norm = q.trim().toLowerCase();
    if (!norm) return data;
    return data.filter((c) => c.title.toLowerCase().includes(norm));
  }, [data, q]);

  const buckets = useMemo(() => bucketize(filtered), [filtered]);

  async function newChat() {
    const id = await create.mutateAsync({ kb_ids: [] });
    onSelect?.(id);
  }

  return (
    <aside className="hidden w-64 shrink-0 self-stretch border-r border-border-default bg-bg-subtle px-3 py-3 md:flex md:flex-col">
      <button
        onClick={newChat}
        className="mb-3 rounded-md border border-border-default px-3 py-2 text-sm hover:bg-bg-muted"
      >
        + 새 대화
      </button>
      <input
        type="search"
        placeholder="대화 검색"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="mb-3 rounded-md border border-border-default bg-bg-default px-2 py-1 text-sm"
      />
      <div className="flex-1 overflow-y-auto pr-1">
        {isLoading && <p className="text-xs text-fg-muted">불러오는 중…</p>}
        {!isLoading && data.length === 0 && (
          <p className="text-xs text-fg-muted">대화 기록이 없습니다.</p>
        )}
        {buckets.map((b) => (
          <div key={b.label} className="mb-3">
            <p className="mb-1 px-2 text-xs uppercase text-fg-subtle">{b.label}</p>
            {b.items.map((c) => (
              <ConversationItem
                key={c.id}
                id={c.id}
                title={c.title}
                active={c.id === activeId}
                onSelect={(id) => onSelect?.(id)}
              />
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/conversation-sidebar.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/apps/web/src/components/chat/ConversationItem.tsx \
        src/apps/web/src/components/chat/ConversationSidebar.tsx \
        src/apps/web/tests/unit/conversation-sidebar.test.tsx
git commit -m "feat(web): ConversationSidebar with bucketize + search + rename/delete"
```

---

## Task 12: `SourcePanel` component (sources + meta tabs)

**Files:**
- Create: `src/apps/web/src/components/chat/SourcePanel.tsx`
- Test: `src/apps/web/tests/unit/source-panel.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/source-panel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SourcePanel } from "@/components/chat/SourcePanel";

const sources = [
  { chunk_id: "c1", marker: 1, doc_title: "정책 v3.2", kb_id: "g-espa",
    snippet: "본문 발췌…", score: 0.9, owner: "김철수" },
  { chunk_id: "c2", marker: 2, doc_title: "회의록", kb_id: "g-espa",
    snippet: "회의 본문…", score: 0.7, owner: null },
];

describe("SourcePanel", () => {
  it("renders source cards", () => {
    render(<SourcePanel chunks={sources} meta={{}} highlightedMarker={null} />);
    expect(screen.getByText("정책 v3.2")).toBeInTheDocument();
    expect(screen.getByText("회의록")).toBeInTheDocument();
  });

  it("switches to meta tab", async () => {
    render(<SourcePanel chunks={sources} meta={{ confidence: 0.78 }} highlightedMarker={null} />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("tab", { name: /메타/i }));
    expect(screen.getByText(/0\.78/)).toBeInTheDocument();
  });

  it("highlights card when marker matches", () => {
    render(<SourcePanel chunks={sources} meta={{}} highlightedMarker={2} />);
    const card = screen.getByText("회의록").closest("[data-marker]");
    expect(card?.getAttribute("data-highlighted")).toBe("true");
  });
});
```

- [ ] **Step 2: Run to verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/source-panel.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `SourcePanel`**

```tsx
// src/apps/web/src/components/chat/SourcePanel.tsx
"use client";

import { useState } from "react";

import { cn } from "@/components/ui/cn";

export type SourceChunk = {
  chunk_id: string;
  marker?: number;
  doc_title: string;
  kb_id: string;
  snippet: string;
  score?: number;
  owner?: string | null;
};

type Props = {
  chunks: SourceChunk[];
  meta: Record<string, unknown>;
  highlightedMarker: number | null;
};

export function SourcePanel({ chunks, meta, highlightedMarker }: Props) {
  const [tab, setTab] = useState<"sources" | "meta">("sources");

  return (
    <aside className="hidden w-[360px] shrink-0 self-stretch border-l border-border-default bg-bg-subtle xl:flex xl:flex-col">
      <div role="tablist" className="flex border-b border-border-default">
        <button
          role="tab" aria-selected={tab === "sources"}
          onClick={() => setTab("sources")}
          className={cn(
            "flex-1 px-3 py-2 text-sm",
            tab === "sources" ? "border-b-2 border-fg-default font-medium" : "text-fg-muted",
          )}
        >
          📎 출처 {chunks.length > 0 && `(${chunks.length})`}
        </button>
        <button
          role="tab" aria-selected={tab === "meta"}
          onClick={() => setTab("meta")}
          className={cn(
            "flex-1 px-3 py-2 text-sm",
            tab === "meta" ? "border-b-2 border-fg-default font-medium" : "text-fg-muted",
          )}
        >
          🧪 메타
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {tab === "sources" && (
          chunks.length === 0
            ? <p className="text-xs text-fg-muted">출처가 없습니다.</p>
            : chunks.map((c) => (
                <article
                  key={c.chunk_id}
                  data-marker={c.marker ?? ""}
                  data-highlighted={c.marker === highlightedMarker ? "true" : "false"}
                  className={cn(
                    "mb-3 rounded-md border border-border-default bg-bg-default p-3 transition-colors",
                    c.marker === highlightedMarker && "ring-2 ring-fg-default",
                  )}
                >
                  <h4 className="text-sm font-medium">
                    {c.marker != null && <span className="mr-1 text-fg-subtle">[{c.marker}]</span>}
                    {c.doc_title}
                  </h4>
                  <p className="mt-1 text-xs text-fg-muted">{c.kb_id} {c.owner && ` · 👤 ${c.owner}`}</p>
                  <p className="mt-2 line-clamp-3 text-xs">{c.snippet}</p>
                  {typeof c.score === "number" && (
                    <p className="mt-1 text-[10px] text-fg-subtle">신뢰도 {(c.score * 100).toFixed(0)}%</p>
                  )}
                </article>
              ))
        )}
        {tab === "meta" && (
          <pre className="text-xs whitespace-pre-wrap break-all">
            {JSON.stringify(meta, null, 2)}
          </pre>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/source-panel.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit + open PR 2**

```bash
git add src/apps/web/src/components/chat/SourcePanel.tsx \
        src/apps/web/tests/unit/source-panel.test.tsx
git commit -m "feat(web): SourcePanel — sources/meta tabs + marker highlight"
git push
gh pr create --title "feat(chat): web foundation — api client + queries + sidebar/source (PR2/5)" \
  --body "Spec ref: docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md"
```

---

# PR 3 — Chat UX Rebuild (3-pane page + actions + slash + force_mode)

## Task 13: Citation marker `[N]` component

**Files:**
- Create: `src/apps/web/src/components/chat/CitationMarker.tsx`
- Test: `src/apps/web/tests/unit/citation-marker.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/citation-marker.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CitationMarker } from "@/components/chat/CitationMarker";

describe("CitationMarker", () => {
  it("renders [N]", () => {
    render(<CitationMarker n={3} onActivate={() => {}} />);
    expect(screen.getByText("[3]")).toBeInTheDocument();
  });
  it("calls onActivate on click", async () => {
    const cb = vi.fn();
    render(<CitationMarker n={1} onActivate={cb} />);
    await userEvent.setup().click(screen.getByText("[1]"));
    expect(cb).toHaveBeenCalledWith(1);
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/citation-marker.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/apps/web/src/components/chat/CitationMarker.tsx
"use client";

export function CitationMarker({
  n, onActivate,
}: {
  n: number;
  onActivate: (n: number) => void;
}) {
  return (
    <button
      onClick={() => onActivate(n)}
      onMouseEnter={() => onActivate(n)}
      className="mx-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded bg-bg-emphasis px-1 text-[11px] font-medium text-fg-default hover:bg-fg-default hover:text-bg-default"
    >
      [{n}]
    </button>
  );
}
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/citation-marker.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/chat/CitationMarker.tsx \
        src/apps/web/tests/unit/citation-marker.test.tsx
git commit -m "feat(web): CitationMarker — clickable [N] with hover preview"
```

---

## Task 14: `MessageActions` component (📎/👤/🔁/⚠️/📋)

**Files:**
- Create: `src/apps/web/src/components/chat/MessageActions.tsx`
- Test: `src/apps/web/tests/unit/message-actions.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/message-actions.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageActions } from "@/components/chat/MessageActions";

describe("MessageActions", () => {
  it("invokes onCopy with content", async () => {
    const cb = vi.fn();
    Object.assign(navigator, { clipboard: { writeText: cb } });
    render(<MessageActions content="답변" onReportError={() => {}} onResubmit={() => {}} onShowSources={() => {}} onFindOwner={() => {}} />);
    await userEvent.setup().click(screen.getByLabelText("복사"));
    expect(cb).toHaveBeenCalledWith("답변");
  });

  it("invokes onResubmit", async () => {
    const cb = vi.fn();
    render(<MessageActions content="x" onReportError={() => {}} onResubmit={cb} onShowSources={() => {}} onFindOwner={() => {}} />);
    await userEvent.setup().click(screen.getByLabelText("재질문"));
    expect(cb).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/message-actions.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/apps/web/src/components/chat/MessageActions.tsx
"use client";

export function MessageActions({
  content, onShowSources, onFindOwner, onResubmit, onReportError,
}: {
  content: string;
  onShowSources: () => void;
  onFindOwner: () => void;
  onResubmit: () => void;
  onReportError: () => void;
}) {
  async function copy() {
    await navigator.clipboard.writeText(content);
  }
  return (
    <div className="mt-1 flex gap-1 text-xs opacity-0 group-hover:opacity-100">
      <button aria-label="출처 보기" onClick={onShowSources}>📎</button>
      <button aria-label="오너 찾기" onClick={onFindOwner}>👤</button>
      <button aria-label="재질문" onClick={onResubmit}>🔁</button>
      <button aria-label="오답 신고" onClick={onReportError}>⚠️</button>
      <button aria-label="복사" onClick={copy}>📋</button>
    </div>
  );
}
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/message-actions.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/chat/MessageActions.tsx \
        src/apps/web/tests/unit/message-actions.test.tsx
git commit -m "feat(web): MessageActions — sources/owner/resubmit/report/copy bar"
```

---

## Task 15: Slash command parser + autocomplete

**Files:**
- Create: `src/apps/web/src/components/chat/SlashCommands.tsx`
- Test: `src/apps/web/tests/unit/slash-commands.test.ts`

- [ ] **Step 1: Write failing test**

```ts
// src/apps/web/tests/unit/slash-commands.test.ts
import { describe, it, expect } from "vitest";
import { parseSlash, SLASH_COMMANDS } from "@/components/chat/SlashCommands";

describe("parseSlash", () => {
  it("returns null when no slash", () => {
    expect(parseSlash("hello")).toBeNull();
  });
  it("parses /owner with arg", () => {
    expect(parseSlash("/owner 김철수")).toEqual({ cmd: "owner", arg: "김철수" });
  });
  it("returns prefix-only match for autocomplete", () => {
    expect(parseSlash("/own")).toEqual({ cmd: "own", arg: "" });
  });
});

describe("SLASH_COMMANDS", () => {
  it("includes owner/kb/시간", () => {
    const names = SLASH_COMMANDS.map((c) => c.name);
    expect(names).toContain("owner");
    expect(names).toContain("kb");
    expect(names).toContain("시간");
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/slash-commands.test.ts
```
Expected: FAIL.

- [ ] **Step 3: Implement parser + dropdown**

```tsx
// src/apps/web/src/components/chat/SlashCommands.tsx
"use client";

export const SLASH_COMMANDS = [
  { name: "owner", help: "/owner <이름> — 오너 정보" },
  { name: "kb", help: "/kb <kb_id> — 특정 KB 강제" },
  { name: "시간", help: "/시간 <범위> — 기간 한정 검색" },
] as const;

export type ParsedSlash = { cmd: string; arg: string } | null;

export function parseSlash(input: string): ParsedSlash {
  if (!input.startsWith("/")) return null;
  const trimmed = input.slice(1);
  const sp = trimmed.indexOf(" ");
  if (sp === -1) return { cmd: trimmed, arg: "" };
  return { cmd: trimmed.slice(0, sp), arg: trimmed.slice(sp + 1) };
}

export function SlashCommandDropdown({
  query, onPick,
}: {
  query: string;
  onPick: (name: string) => void;
}) {
  const matches = SLASH_COMMANDS.filter((c) => c.name.startsWith(query));
  if (matches.length === 0) return null;
  return (
    <ul
      role="listbox"
      className="absolute bottom-full mb-1 w-full overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg"
    >
      {matches.map((c) => (
        <li key={c.name}>
          <button
            type="button"
            onMouseDown={(e) => { e.preventDefault(); onPick(c.name); }}
            className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted"
          >
            <span className="font-medium">/{c.name}</span>
            <span className="ml-2 text-xs text-fg-muted">{c.help}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/slash-commands.test.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/chat/SlashCommands.tsx \
        src/apps/web/tests/unit/slash-commands.test.ts
git commit -m "feat(web): slash command parser + autocomplete dropdown"
```

---

## Task 16: `ModeForceMenu` component (⚙️)

**Files:**
- Create: `src/apps/web/src/components/chat/ModeForceMenu.tsx`
- Test: `src/apps/web/tests/unit/mode-force-menu.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/mode-force-menu.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ModeForceMenu } from "@/components/chat/ModeForceMenu";

describe("ModeForceMenu", () => {
  it("toggles between auto/quick/deep", async () => {
    const cb = vi.fn();
    render(<ModeForceMenu value="auto" onChange={cb} />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: /고급/i }));
    await u.click(screen.getByRole("menuitem", { name: /빠른/ }));
    expect(cb).toHaveBeenCalledWith("quick");
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/mode-force-menu.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/apps/web/src/components/chat/ModeForceMenu.tsx
"use client";

import { useState } from "react";

export type ForceMode = "auto" | "quick" | "deep";

const LABELS: Record<ForceMode, string> = {
  auto: "자동",
  quick: "빠른 검색",
  deep: "심층 검색",
};

export function ModeForceMenu({
  value, onChange,
}: {
  value: ForceMode;
  onChange: (v: ForceMode) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-border-default px-2 py-1 text-xs"
        title="고급 — 검색 모드 강제"
      >
        ⚙️ 고급 · {LABELS[value]}
      </button>
      {open && (
        <ul role="menu" className="absolute right-0 top-full z-10 mt-1 w-40 overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg">
          {(Object.keys(LABELS) as ForceMode[]).map((k) => (
            <li key={k}>
              <button
                role="menuitem"
                onClick={() => { onChange(k); setOpen(false); }}
                className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted"
              >
                {LABELS[k]}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/mode-force-menu.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/chat/ModeForceMenu.tsx \
        src/apps/web/tests/unit/mode-force-menu.test.tsx
git commit -m "feat(web): ModeForceMenu — auto/quick/deep override"
```

---

## Task 17: Rewrite `ChatPage` to 3-pane

**Files:**
- Modify: `src/apps/web/src/components/chat/ChatPage.tsx`
- Modify: `src/apps/web/src/components/chat/ChatMessages.tsx` (citation rendering + actions)
- Modify: `src/apps/web/src/components/chat/ChatInput.tsx` (slash hooks)
- Delete: `src/apps/web/src/components/chat/ModeToggle.tsx`
- Delete: `src/apps/web/src/components/chat/KbSelector.tsx` import sites in ChatPage (kept as KB chip in topbar)
- Test: `src/apps/web/tests/unit/chat-page-3pane.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/chat-page-3pane.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPage } from "@/components/chat/ChatPage";

vi.mock("@/store/conversations", () => ({
  useConversations: () => ({ data: [], isLoading: false }),
  useMessages: () => ({ data: [], isLoading: false }),
  useCreateConversation: () => ({ mutateAsync: vi.fn().mockResolvedValue("c1") }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn() }),
  useRenameConversation: () => ({ mutateAsync: vi.fn() }),
  useSendMessage: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function wrap() {
  const qc = new QueryClient();
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("ChatPage 3-pane", () => {
  it("renders left sidebar + center chat + right source panel placeholders", () => {
    render(<ChatPage />, { wrapper: wrap() });
    expect(screen.getByText("+ 새 대화")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/질문/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /출처/ })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/chat-page-3pane.test.tsx
```
Expected: FAIL — current ChatPage is single-pane.

- [ ] **Step 3: Rewrite `ChatPage.tsx`**

Replace `src/apps/web/src/components/chat/ChatPage.tsx` contents:

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";

import { ConversationSidebar } from "./ConversationSidebar";
import { SourcePanel, type SourceChunk } from "./SourcePanel";
import { ChatInput } from "./ChatInput";
import { ChatMessages } from "./ChatMessages";
import { ModeForceMenu, type ForceMode } from "./ModeForceMenu";
import { ErrorReportDialog } from "./ErrorReportDialog";
import { useChatStore } from "@/store/chat";
import {
  useCreateConversation,
  useMessages,
  useSendMessage,
} from "@/store/conversations";

export function ChatPage() {
  const activeId = useChatStore((s) => s.activeConversationId);
  const setActive = useChatStore((s) => s.resetForConversation);

  const create = useCreateConversation();
  const { data: messages = [] } = useMessages(activeId);
  const send = useSendMessage(activeId);
  const [forceMode, setForceMode] = useState<ForceMode>("auto");
  const [highlightMarker, setHighlightMarker] = useState<number | null>(null);
  const [reportTarget, setReportTarget] = useState<SourceChunk | null>(null);
  const [ownerHint, setOwnerHint] = useState(false);

  // Show last assistant message's chunks/meta in right panel.
  const lastAssistant = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant"),
    [messages],
  );
  const sourceChunks: SourceChunk[] = useMemo(
    () => ((lastAssistant?.chunks ?? []) as SourceChunk[]).map((c, i) => ({
      ...c, marker: c.marker ?? i + 1,
    })),
    [lastAssistant],
  );

  async function ensureConversation(): Promise<string> {
    if (activeId) return activeId;
    const id = await create.mutateAsync({ kb_ids: [] });
    setActive(id);
    return id;
  }

  async function handleSubmit(content: string) {
    await ensureConversation();
    await send.mutateAsync({
      content,
      force_mode: forceMode === "auto" ? null : forceMode,
    });
  }

  // Keyboard: Cmd/Ctrl + N → new chat.
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        const id = await create.mutateAsync({ kb_ids: [] });
        setActive(id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [create, setActive]);

  return (
    <div className="flex h-full w-full">
      <ConversationSidebar activeId={activeId} onSelect={setActive} />

      <main className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border-default px-4 py-2 text-sm">
          <span className="font-medium">
            {messages.length > 0 ? "대화" : "새 대화"}
          </span>
          <ModeForceMenu value={forceMode} onChange={setForceMode} />
        </header>

        <section className="flex-1 overflow-y-auto px-6 py-4">
          {messages.length === 0 ? (
            <p className="text-sm text-fg-muted">
              궁금한 것을 물어보세요. <kbd>⌘/Ctrl+N</kbd> 새 대화.
            </p>
          ) : (
            <ChatMessages
              messages={messages}
              onMarkerActivate={setHighlightMarker}
              onReportError={setReportTarget}
              onResubmit={(prior) => prior && handleSubmit(prior)}
              onFindOwner={() => setOwnerHint(true)}
            />
          )}
        </section>

        <footer className="border-t border-border-default px-6 py-3">
          {ownerHint && (
            <p className="mb-1 text-xs text-fg-muted">
              💡 입력창에 <code>/owner 이름</code> 으로 오너를 검색할 수 있습니다.
              <button onClick={() => setOwnerHint(false)} className="ml-2">닫기</button>
            </p>
          )}
          <ChatInput onSubmit={handleSubmit} pending={send.isPending} />
        </footer>
      </main>

      <SourcePanel
        chunks={sourceChunks}
        meta={(lastAssistant?.meta ?? {}) as Record<string, unknown>}
        highlightedMarker={highlightMarker}
      />

      {reportTarget && (
        <ErrorReportDialog chunk={reportTarget as never} onClose={() => setReportTarget(null)} />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Update `ChatMessages.tsx` to use new prop shape**

Replace `src/apps/web/src/components/chat/ChatMessages.tsx` contents:

```tsx
"use client";

import { CitationMarker } from "./CitationMarker";
import { MessageActions } from "./MessageActions";
import type { ChatMessage } from "@/lib/api/chat";

function renderWithCitations(
  text: string, onMarker: (n: number) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(
      <CitationMarker key={`${m.index}-${m[1]}`} n={Number(m[1])} onActivate={onMarker} />
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function ChatMessages({
  messages, onMarkerActivate, onReportError, onResubmit, onFindOwner,
}: {
  messages: ChatMessage[];
  onMarkerActivate: (n: number) => void;
  onReportError: (chunk: never) => void;
  onResubmit: (priorUserContent: string) => void;
  onFindOwner: () => void;
}) {
  function priorUserOf(idx: number): string {
    for (let i = idx - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i].content;
    return "";
  }
  return (
    <ul className="space-y-4">
      {messages.map((m, idx) => (
        <li key={m.id} className="group">
          <p className="text-xs uppercase text-fg-subtle">{m.role}</p>
          <div className="mt-1 whitespace-pre-wrap text-sm">
            {renderWithCitations(m.content, onMarkerActivate)}
          </div>
          {m.role === "assistant" && (
            <MessageActions
              content={m.content}
              onShowSources={() => onMarkerActivate(1)}
              onFindOwner={onFindOwner}
              onResubmit={() => onResubmit(priorUserOf(idx))}
              onReportError={() => onReportError(undefined as never)}
            />
          )}
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 5: Update `ChatInput.tsx` to integrate slash dropdown**

Replace `src/apps/web/src/components/chat/ChatInput.tsx`:

```tsx
"use client";

import { useState } from "react";

import { parseSlash, SlashCommandDropdown } from "./SlashCommands";

export function ChatInput({
  onSubmit, pending,
}: {
  onSubmit: (content: string) => void | Promise<void>;
  pending: boolean;
}) {
  const [value, setValue] = useState("");
  const slash = parseSlash(value);
  const showDropdown = slash !== null && slash.arg === "";

  function submit() {
    const v = value.trim();
    if (!v || pending) return;
    onSubmit(v);
    setValue("");
  }

  return (
    <div className="relative">
      {showDropdown && (
        <SlashCommandDropdown
          query={slash!.cmd}
          onPick={(name) => setValue(`/${name} `)}
        />
      )}
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
        rows={2}
        placeholder="질문을 입력하세요. ⌘/Ctrl+Enter 전송. /owner 같은 명령도 가능."
        className="w-full resize-none rounded-md border border-border-default bg-bg-default px-3 py-2 text-sm"
        disabled={pending}
      />
      <p className="mt-1 text-right text-xs text-fg-subtle">
        ⌘/Ctrl + Enter 전송
      </p>
    </div>
  );
}
```

- [ ] **Step 6: Delete `ModeToggle.tsx`**

```bash
rm src/apps/web/src/components/chat/ModeToggle.tsx
```

(If anything imports it, it will fail TypeScript — Step 7's typecheck will catch.)

- [ ] **Step 7: Run tests + typecheck**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/chat-page-3pane.test.tsx && pnpm typecheck
```
Expected: PASS + clean typecheck.

- [ ] **Step 8: Commit**

```bash
git add src/apps/web/src/components/chat \
        src/apps/web/tests/unit/chat-page-3pane.test.tsx
git rm src/apps/web/src/components/chat/ModeToggle.tsx
git commit -m "feat(web): rewrite ChatPage to 3-pane (sidebar / chat / source panel)"
```

---

## Task 18: Conversation route `/(app)/chat/[conversationId]`

**Files:**
- Create: `src/apps/web/src/app/(app)/chat/[conversationId]/page.tsx`
- Modify: `src/apps/web/src/app/(app)/chat/page.tsx` (no-op — keeps default)

- [ ] **Step 1: Implement deep-link route**

```tsx
// src/apps/web/src/app/(app)/chat/[conversationId]/page.tsx
"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";

import { ChatPage } from "@/components/chat/ChatPage";
import { useChatStore } from "@/store/chat";

export default function ChatConversationPage() {
  const params = useParams();
  const conversationId = String(params.conversationId);
  const setActive = useChatStore((s) => s.resetForConversation);

  useEffect(() => {
    setActive(conversationId);
  }, [conversationId, setActive]);

  return <ChatPage />;
}
```

- [ ] **Step 2: Manual smoke test**

```bash
make web-dev
# open http://localhost:3000/chat/<some-id>
# expect: ChatPage loads with that conversation active
```

- [ ] **Step 3: Commit + open PR 3**

```bash
git add src/apps/web/src/app/\(app\)/chat/\[conversationId\]/page.tsx
git commit -m "feat(web): /chat/[conversationId] deep link route"
git push
gh pr create --title "feat(chat): chat UX rebuild — 3-pane + actions + slash + force_mode (PR3/5)" \
  --body "Spec ref: docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md"
```

---

# PR 4 — Page Migration & Polish

## Task 19: Delete `/search-history` + redirect

**Files:**
- Delete: `src/apps/web/src/app/(app)/search-history/page.tsx`
- Modify: `src/apps/web/src/app/proxy.ts`

- [ ] **Step 1: Add redirect rule**

In `src/apps/web/src/app/proxy.ts`, find the existing `proxy()` export. Inside, before any auth-protect logic, add:

```ts
const REDIRECTS_30D: Record<string, string> = {
  "/search-history": "/chat",
  "/find-owner": "/chat?onboarding=owner",
};

const url = new URL(request.url);
const redirect = REDIRECTS_30D[url.pathname];
if (redirect) {
  return Response.redirect(new URL(redirect, request.url), 308);
}
```

- [ ] **Step 2: Delete page**

```bash
rm -r src/apps/web/src/app/\(app\)/search-history
```

- [ ] **Step 3: Run typecheck + smoke**

```bash
cd src/apps/web && pnpm typecheck
# in browser: visit /search-history → must land on /chat
```

- [ ] **Step 4: Commit**

```bash
git add -A src/apps/web/src/app
git commit -m "refactor(web): remove /search-history page (absorbed into sidebar)"
```

---

## Task 20: Delete `/find-owner` + onboarding tooltip

**Files:**
- Delete: `src/apps/web/src/app/(app)/find-owner/page.tsx` (entire dir)
- Modify: `src/apps/web/src/components/chat/ChatPage.tsx` (handle `?onboarding=owner` param)

- [ ] **Step 1: Add onboarding banner state to ChatPage**

In `ChatPage.tsx`, near the top of the component:

```tsx
import { useSearchParams } from "next/navigation";
// ...
const params = useSearchParams();
const showOwnerOnboarding = params.get("onboarding") === "owner";
```

Render below the header:

```tsx
{showOwnerOnboarding && (
  <div className="border-b border-border-default bg-bg-info px-4 py-2 text-sm">
    💡 오너 검색은 이제 채팅창에서 <code>/owner 이름</code> 으로 가능합니다.
    <button
      className="ml-2 text-fg-subtle"
      onClick={() => history.replaceState({}, "", "/chat")}
    >닫기</button>
  </div>
)}
```

- [ ] **Step 2: Delete `/find-owner` page**

```bash
rm -r src/apps/web/src/app/\(app\)/find-owner
```

- [ ] **Step 3: Manual smoke**

```bash
# visit /find-owner → land on /chat with banner.
```

- [ ] **Step 4: Commit**

```bash
git add -A src/apps/web/src/app src/apps/web/src/components/chat/ChatPage.tsx
git commit -m "refactor(web): remove /find-owner page; replace with /owner slash + onboarding banner"
```

---

## Task 21: `ProfileDropdown` component

**Files:**
- Create: `src/apps/web/src/components/layout/ProfileDropdown.tsx`
- Test: `src/apps/web/tests/unit/profile-dropdown.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/profile-dropdown.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProfileDropdown } from "@/components/layout/ProfileDropdown";

describe("ProfileDropdown", () => {
  it("opens menu and shows feedback/activities/policy/logout", async () => {
    render(<ProfileDropdown email="x@y.com" />);
    await userEvent.setup().click(screen.getByRole("button", { name: /프로필/i }));
    expect(screen.getByRole("menuitem", { name: /피드백/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /활동/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /처리방침/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/profile-dropdown.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/apps/web/src/components/layout/ProfileDropdown.tsx
"use client";

import Link from "next/link";
import { useState } from "react";

export function ProfileDropdown({ email }: { email: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        aria-label="프로필"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-bg-muted"
      >
        <span aria-hidden>👤</span>
        <span className="truncate">{email}</span>
      </button>
      {open && (
        <ul role="menu" className="absolute bottom-full left-0 z-10 mb-1 w-full overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg">
          <li><Link role="menuitem" href="/my-feedback" className="block px-3 py-2 text-sm hover:bg-bg-muted">📝 내 피드백</Link></li>
          <li><Link role="menuitem" href="/my-activities" className="block px-3 py-2 text-sm hover:bg-bg-muted">📋 내 활동</Link></li>
          <li><Link role="menuitem" href="/security#chat-retention" className="block px-3 py-2 text-sm hover:bg-bg-muted">🔒 처리방침</Link></li>
          <li>
            <form method="post" action="/api/auth/logout">
              <button type="submit" role="menuitem" className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted">
                ⏻ 로그아웃
              </button>
            </form>
          </li>
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/profile-dropdown.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/layout/ProfileDropdown.tsx \
        src/apps/web/tests/unit/profile-dropdown.test.tsx
git commit -m "feat(web): ProfileDropdown — feedback/activities/policy/logout"
```

---

## Task 22: Trim `Sidebar.tsx` nav

**Files:**
- Modify: `src/apps/web/src/components/layout/Sidebar.tsx`
- Test: `src/apps/web/tests/unit/sidebar-nav.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/sidebar-nav.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { Sidebar } from "@/components/layout/Sidebar";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

describe("Sidebar nav", () => {
  it("does not render search-history or find-owner links", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("link", { name: /search_history/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /find_owner/i })).toBeNull();
  });
  it("does not render my-feedback or my-activities (moved to profile)", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("link", { name: /my_feedback/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /my_activities/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/sidebar-nav.test.tsx
```
Expected: FAIL — current Sidebar still has all 7 entries.

- [ ] **Step 3: Update Sidebar**

Replace the `NAV` constant in `src/apps/web/src/components/layout/Sidebar.tsx`:

```ts
const NAV: { href: string; key: string; icon: string }[] = [
  { href: "/chat", key: "chat", icon: "💬" },
  { href: "/my-knowledge", key: "my_knowledge", icon: "📚" },
  { href: "/my-documents", key: "my_documents", icon: "📄" },
];
```

(Removed: search_history, find_owner, my_feedback, my_activities. The first two are deleted pages; the latter two move to ProfileDropdown.)

NOTE: this `Sidebar` is the **outer layout sidebar**. The chat surface has its own `ConversationSidebar`. Outer sidebar may be hidden inside `/chat` to avoid double sidebars — make outer Sidebar render only when path !== `/chat*`:

```tsx
// at top of Sidebar component:
const pathname = usePathname();
if (pathname.startsWith("/chat")) return null;
```

- [ ] **Step 4: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/sidebar-nav.test.tsx
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/apps/web/src/components/layout/Sidebar.tsx \
        src/apps/web/tests/unit/sidebar-nav.test.tsx
git commit -m "refactor(web): trim outer Sidebar to 3 links + hide on /chat"
```

---

## Task 23: Reduce `/my-knowledge` to KB management

**Files:**
- Modify: `src/apps/web/src/app/(app)/my-knowledge/page.tsx`

- [ ] **Step 1: Replace page with focused KB-management view**

```tsx
// src/apps/web/src/app/(app)/my-knowledge/page.tsx
"use client";

import { useEffect, useState } from "react";

type KbRow = { kb_id: string; name: string; favorite?: boolean };

export default function MyKnowledgePage() {
  const [kbs, setKbs] = useState<KbRow[]>([]);
  useEffect(() => {
    fetch("/api/proxy/api/v1/admin/kb?status=active")
      .then((r) => r.json())
      .then((d: { kbs: KbRow[] }) => setKbs(d.kbs ?? []));
  }, []);
  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold">내 KB 관리</h1>
      <p className="mt-2 text-sm text-fg-muted">
        검색 시 기본으로 사용할 KB를 즐겨찾기로 표시합니다. 즐겨찾기는 채팅의 KB chip에 우선 노출됩니다.
      </p>
      <ul className="mt-6 space-y-2">
        {kbs.map((kb) => (
          <li key={kb.kb_id} className="flex items-center justify-between rounded-md border border-border-default px-3 py-2">
            <span className="text-sm">{kb.name} <span className="text-xs text-fg-muted">{kb.kb_id}</span></span>
            <button className="text-xs">⭐ 즐겨찾기</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Smoke check + commit**

```bash
cd src/apps/web && pnpm typecheck
git add src/apps/web/src/app/\(app\)/my-knowledge/page.tsx
git commit -m "refactor(web): /my-knowledge → KB favorites management view"
git push
gh pr create --title "feat(chat): page migration + ProfileDropdown + sidebar trim (PR4/5)" \
  --body "Spec ref: docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md"
```

---

# PR 5 — Background Jobs & Compliance

## Task 24: `auto_title_for_conversation` background task

**Files:**
- Create: `src/jobs/chat_jobs.py`
- Modify: `src/jobs/tasks.py`
- Modify: `src/api/routes/chat.py` (enqueue after first assistant turn)
- Test: `tests/unit/test_chat_jobs.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_chat_jobs.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.chat_jobs import auto_title_for_conversation


@pytest.mark.asyncio
async def test_auto_title_writes_short_title():
    repo = AsyncMock()
    llm = AsyncMock()
    llm.ainvoke.return_value = "신촌 점검"
    ctx = {"chat_repo": repo, "llm": llm,
           "auto_title_max_tokens": 20,
           "auto_title_fallback_chars": 30}
    await auto_title_for_conversation(
        ctx, str(uuid.uuid4()), "신촌점 차주 점검 일정 알려줘",
    )
    repo.set_title_if_empty.assert_awaited()
    args = repo.set_title_if_empty.await_args.args
    assert args[1] == "신촌 점검"


@pytest.mark.asyncio
async def test_auto_title_fallback_on_llm_failure():
    repo = AsyncMock()
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("llm down")
    ctx = {"chat_repo": repo, "llm": llm,
           "auto_title_max_tokens": 20,
           "auto_title_fallback_chars": 30}
    await auto_title_for_conversation(
        ctx, str(uuid.uuid4()), "신촌점 차주 점검 일정 알려줘",
    )
    repo.set_title_if_empty.assert_awaited()
    args = repo.set_title_if_empty.await_args.args
    assert args[1] == "신촌점 차주 점검 일정 알려줘"  # full input ≤30
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_chat_jobs.py -v --no-cov
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# src/jobs/chat_jobs.py
"""Chat-related arq background tasks.

- auto_title_for_conversation: LLM-summarize first user query → set conv title
- chat_history_purge_sweep: hard-delete rows older than retention_days
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


async def auto_title_for_conversation(
    ctx: dict[str, Any], conversation_id: str, first_user_query: str,
) -> None:
    repo = ctx["chat_repo"]
    llm = ctx.get("llm")
    max_tokens = int(ctx.get("auto_title_max_tokens", 20))
    fallback_chars = int(ctx.get("auto_title_fallback_chars", 30))

    title = ""
    if llm is not None:
        try:
            prompt = (
                "다음 질의를 한국어 짧은 제목 (10자 이내, 명사구) 으로 요약하라. "
                "출력은 제목만, 따옴표/구두점 없음.\n\n"
                f"질의: {first_user_query}"
            )
            raw = await llm.ainvoke(prompt, max_tokens=max_tokens)
            title = (raw or "").strip().strip('"”“')[:40]
        except Exception as e:  # noqa: BLE001 — LLM 실패 광범위 catch
            logger.warning("auto_title LLM failed: %s — using fallback", e)

    if not title:
        title = first_user_query[:fallback_chars]

    await repo.set_title_if_empty(uuid.UUID(conversation_id), title)


async def chat_history_purge_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    repo = ctx["chat_repo"]
    days = int(ctx.get("chat_retention_days", 90))
    deleted = await repo.purge_older_than(days=days)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    logger.info(
        "chat_history_purge_sweep: deleted=%d cutoff=%s",
        deleted, cutoff.isoformat(),
    )
    return {"deleted": deleted}
```

- [ ] **Step 4: Register tasks**

In `src/jobs/tasks.py`, find `REGISTERED_TASKS` and add:

```python
from src.jobs.chat_jobs import (
    auto_title_for_conversation,
    chat_history_purge_sweep,
)
# ...
REGISTERED_TASKS = [
    # ... existing
    auto_title_for_conversation,
    chat_history_purge_sweep,
]
```

- [ ] **Step 5: Wire context (`startup` injects `chat_repo`/`llm`/settings)**

In `src/jobs/worker.py` `WorkerSettings.on_startup`, after the existing FF listener block, add:

```python
from src.config import get_settings
from src.stores.postgres.repositories.chat_repo import ChatRepository
from src.stores.postgres.session import get_session_maker  # adjust if helper differs
from src.core.providers.llm import get_default_llm

settings = get_settings()
ctx["chat_repo"] = ChatRepository(
    session_maker=get_session_maker(),
    encryption_key=settings.chat.encryption_key,
)
ctx["llm"] = get_default_llm()
ctx["chat_retention_days"] = settings.chat.retention_days
ctx["auto_title_max_tokens"] = settings.chat.auto_title_max_tokens
ctx["auto_title_fallback_chars"] = settings.chat.auto_title_fallback_chars
```

(If `get_session_maker` or `get_default_llm` don't exist, replace with the codebase's existing equivalents — match the pattern used by other jobs like `audit_log_archive_sweep`.)

- [ ] **Step 6: Enqueue from `/chat` send route**

In `src/api/routes/chat.py` `send_message` handler, after `repo.append_message(... role="assistant" ...)` and before the return, add:

```python
# Enqueue auto-title only on first assistant message of conversation
if conv.title == "":
    from src.jobs.queue import enqueue_job
    try:
        await enqueue_job(
            "auto_title_for_conversation",
            str(conv_id), body.content,
        )
    except Exception as e:  # noqa: BLE001 — title is best-effort
        logger.warning("auto_title enqueue failed: %s", e)
```

- [ ] **Step 7: Verify pass**

```bash
uv run pytest tests/unit/test_chat_jobs.py tests/unit/test_chat_routes.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/jobs/chat_jobs.py src/jobs/tasks.py src/jobs/worker.py \
        src/api/routes/chat.py tests/unit/test_chat_jobs.py
git commit -m "feat(jobs): auto_title_for_conversation + worker context wiring"
```

---

## Task 25: Register `chat_history_purge_sweep` cron

**Files:**
- Modify: `src/jobs/worker.py`
- Test: `tests/unit/test_chat_purge_cron.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_chat_purge_cron.py
from src.jobs.worker import WorkerSettings


def test_chat_purge_cron_registered_daily():
    sources = [str(c) for c in WorkerSettings.cron_jobs]
    matched = [s for s in sources if "chat_history_purge_sweep" in s]
    assert matched, "chat_history_purge_sweep cron not registered"
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_chat_purge_cron.py -v --no-cov
```
Expected: FAIL.

- [ ] **Step 3: Add cron entry**

In `src/jobs/worker.py` `WorkerSettings.cron_jobs` list, add:

```python
from src.jobs.chat_jobs import chat_history_purge_sweep
# ... inside cron_jobs list:
cron(chat_history_purge_sweep, hour={3}, minute={20}),  # daily 03:20 UTC
```

Also append the friendly name to `cron_names` in `on_startup` log:

```python
"chat_history_purge_sweep (daily 03:20)",
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_chat_purge_cron.py -v --no-cov
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jobs/worker.py tests/unit/test_chat_purge_cron.py
git commit -m "feat(jobs): chat_history_purge_sweep daily 03:20 UTC cron (90d retention)"
```

---

## Task 26: First-login privacy consent modal

**Files:**
- Create: `src/apps/web/src/components/PrivacyConsent.tsx`
- Modify: `src/apps/web/src/app/(app)/layout.tsx`
- Test: `src/apps/web/tests/unit/privacy-consent.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
// src/apps/web/tests/unit/privacy-consent.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrivacyConsent } from "@/components/PrivacyConsent";

describe("PrivacyConsent", () => {
  beforeEach(() => localStorage.removeItem("axe-privacy-consent-v1"));

  it("renders modal on first visit and dismisses on accept", async () => {
    render(<PrivacyConsent />);
    expect(screen.getByText(/처리방침/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /동의/ }));
    expect(screen.queryByText(/처리방침/)).toBeNull();
    expect(localStorage.getItem("axe-privacy-consent-v1")).toBe("accepted");
  });

  it("does not render when already accepted", () => {
    localStorage.setItem("axe-privacy-consent-v1", "accepted");
    render(<PrivacyConsent />);
    expect(screen.queryByText(/처리방침/)).toBeNull();
  });
});
```

- [ ] **Step 2: Verify fail**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/privacy-consent.test.tsx
```
Expected: FAIL.

- [ ] **Step 3: Implement modal**

```tsx
// src/apps/web/src/components/PrivacyConsent.tsx
"use client";

import { useEffect, useState } from "react";

const KEY = "axe-privacy-consent-v1";

export function PrivacyConsent() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    setOpen(localStorage.getItem(KEY) !== "accepted");
  }, []);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="max-w-md rounded-lg border border-border-default bg-bg-default p-6 shadow-xl">
        <h2 className="text-lg font-semibold">처리방침 안내</h2>
        <p className="mt-3 text-sm text-fg-muted">
          AI 검색 시 입력하신 질의·답변은 시스템 개선·감사 목적으로 <b>90일 보관 후 자동 파기</b>됩니다.
          본인 대화는 좌측 사이드바에서 직접 삭제할 수 있습니다.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <a href="/security#chat-retention" className="text-xs text-fg-muted underline">
            상세 처리방침
          </a>
          <button
            onClick={() => { localStorage.setItem(KEY, "accepted"); setOpen(false); }}
            className="rounded-md bg-fg-default px-3 py-1.5 text-sm text-bg-default"
          >
            동의하고 시작
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Mount in app layout**

In `src/apps/web/src/app/(app)/layout.tsx`, import and render `<PrivacyConsent />` once near the root of the layout (above children, after auth).

- [ ] **Step 5: Verify pass**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/privacy-consent.test.tsx
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/apps/web/src/components/PrivacyConsent.tsx \
        src/apps/web/src/app/\(app\)/layout.tsx \
        src/apps/web/tests/unit/privacy-consent.test.tsx
git commit -m "feat(web): PrivacyConsent first-login modal (chat retention notice)"
```

---

## Task 27: Update `docs/SECURITY.md`

**Files:**
- Modify: `docs/SECURITY.md`

- [ ] **Step 1: Add chat retention section**

Append a new section to `docs/SECURITY.md`:

````markdown
## Chat History Retention <a id="chat-retention"></a>

axiomedge stores user chat history in Postgres to support the persistent left
sidebar in the user-facing web. To meet PIPA requirements:

- **Retention**: 90 days (env `CHAT_RETENTION_DAYS`). Daily cron
  `chat_history_purge_sweep` (03:20 UTC) hard-deletes older rows.
- **User deletion right (PIPA §36)**: Users can delete their own conversations
  via the sidebar; soft-delete is immediate, hard-delete on next purge cycle.
- **At-rest encryption**: `chat_messages.content_enc` is `pgp_sym_encrypt`'d
  with `CHAT_ENCRYPTION_KEY`. Empty key = plaintext (dev only).
- **Access control**: a user can only read/list/rename/delete their own
  conversations (`user_id` predicate in every repo method).
- **Processing policy**: shown to user as a first-login modal (PrivacyConsent).
- **Audit**: all conversation creates/deletes/renames flow through `audit_log`
  via the FastAPI middleware (existing).

Backups: daily Postgres backup retains encrypted data only; backups follow the
standard 30-day rotation, after which deleted rows are unrecoverable.
````

- [ ] **Step 2: Cross-reference in `docs/CONFIGURATION.md`**

Add to `docs/CONFIGURATION.md` an entry table row:

```markdown
| `CHAT_ENCRYPTION_KEY` | (empty) | pgp_sym_encrypt key for chat_messages body. Empty disables encryption (dev only). |
| `CHAT_RETENTION_DAYS` | `90`    | Days to keep chat conversations before purge. |
| `CHAT_AUTO_TITLE_ENABLED` | `true` | LLM-generated short title after first answer. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/SECURITY.md docs/CONFIGURATION.md
git commit -m "docs: chat history retention + env vars"
```

---

## Task 28: Playwright E2E full chat flow

**Files:**
- Create: `src/apps/web/tests/chat-flow.spec.ts`

- [ ] **Step 1: Write E2E**

```ts
// src/apps/web/tests/chat-flow.spec.ts
import { test, expect } from "@playwright/test";

test("full chat: new → message → sidebar refresh → rename → delete", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("admin@knowledge.local");
  await page.getByLabel(/password/i).fill("dev1234!");
  await page.getByRole("button", { name: /로그인|login/i }).click();

  // Land on /chat
  await page.waitForURL(/\/chat/);

  // Dismiss privacy consent if shown
  const consent = page.getByRole("button", { name: /동의/ });
  if (await consent.isVisible()) await consent.click();

  // Click + 새 대화
  await page.getByRole("button", { name: "+ 새 대화" }).click();

  // Type and send
  await page.getByPlaceholder(/질문/).fill("안녕");
  await page.keyboard.press("ControlOrMeta+Enter");

  // Wait for assistant turn (any text in message stream)
  await expect(page.locator("ul li").first()).toBeVisible({ timeout: 30_000 });

  // Sidebar shows the new conversation (auto-title may take seconds; just verify count > 0)
  const items = page.locator('aside button:has-text("(제목 없음)"), aside button:not(:has-text("+ 새 대화"))');
  await expect(items.first()).toBeVisible();

  // Rename via hover icon
  await items.first().hover();
  await page.getByLabel("이름 변경").first().click();
  await page.keyboard.type("E2E 테스트 대화");
  await page.keyboard.press("Enter");
  await expect(page.getByText("E2E 테스트 대화")).toBeVisible();

  // Delete
  page.once("dialog", (d) => d.accept());
  await page.getByText("E2E 테스트 대화").hover();
  await page.getByLabel("삭제").first().click();
  await expect(page.getByText("E2E 테스트 대화")).toBeHidden();
});

test("/find-owner redirects to /chat with onboarding banner", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill("admin@knowledge.local");
  await page.getByLabel(/password/i).fill("dev1234!");
  await page.getByRole("button", { name: /로그인|login/i }).click();
  await page.waitForURL(/\/chat/);

  await page.goto("/find-owner");
  await expect(page).toHaveURL(/\/chat/);
  await expect(page.getByText(/\/owner/)).toBeVisible();
});
```

- [ ] **Step 2: Run E2E**

```bash
cd src/apps/web && pnpm test:e2e tests/chat-flow.spec.ts
```
Expected: PASS (2 tests). Backend + worker must be running (`make api`, arq worker).

- [ ] **Step 3: Commit + open final PR**

```bash
git add src/apps/web/tests/chat-flow.spec.ts
git commit -m "test(e2e): chat flow — new/send/rename/delete + find-owner redirect"
git push
gh pr create --title "feat(chat): jobs + retention + consent + E2E (PR5/5)" \
  --body "$(cat <<'EOF'
Final PR for user web UX redesign.

- auto_title_for_conversation arq task
- chat_history_purge_sweep daily 03:20 UTC cron (90d retention)
- PrivacyConsent first-login modal
- docs/SECURITY.md retention section + env vars
- Playwright E2E for full flow + find-owner redirect

Spec: docs/superpowers/specs/2026-04-28-user-web-ux-redesign-design.md
Plan: docs/superpowers/plans/2026-04-28-user-web-ux-redesign.md

## Test plan
- [x] unit + integration on each task
- [x] E2E full flow
- [ ] Legal/security review of SECURITY.md retention text before flag-on
- [ ] Manual: 90d purge dry-run in staging
EOF
)"
```

---

# Self-Review Checklist (executed inline before delivering plan)

- ✅ **Spec coverage** — every numbered section of the spec is mapped to at least one task:
  - §6 IA → tasks 19, 20, 22, 23
  - §7 Layout → tasks 11, 12, 17, 18
  - §8.1 DB → tasks 2, 3
  - §8.2 API → tasks 4, 6
  - §8.3 routing → task 5
  - §8.4 frontend state → tasks 8, 9, 10
  - §8.5 marker sync → tasks 13, 17
  - §9 migration → tasks 19, 20
  - §10 testing → all tasks (TDD) + task 7 + task 28
  - §11 risks: routing escalate (task 5 + future), latency (task 9 staleTime), redirect (tasks 19, 20), legal (tasks 26, 27)
  - §12 compliance → tasks 1 (settings), 2 (encryption col), 4 (encrypt impl), 25 (purge), 26 (consent), 27 (docs)
  - §14 effort 5–7 PR → 5 PR boundaries marked.
- ✅ **No placeholders** — confirmed via grep for `TODO|TBD|FIXME|XXX`. Only matches are the literal `placeholder=` JSX attribute on the search input and chat textarea, plus the word "placeholders" in a test description. No plan-level TODOs.
- ⚠️ **Engineer-time check (non-blocking)**: Task 6 `send_message` calls `state.rag_pipeline.search(...)` and `state.rag_pipeline.agentic_ask(...)`. If `KnowledgeRAGPipeline` exposes different method names, locate the helper used by the existing `src/api/routes/search.py` and `src/api/routes/agentic.py` (likely in `*_helpers.py` siblings) and call those directly. The wrapper's contract — turn a query into `{answer, chunks, metadata, confidence, trace_id}` — is what matters; method names are a small adapter.
- ✅ **Type consistency** — `ChatMessage`, `Conversation`, `SourceChunk`, `ForceMode`, `RoutingSignals` all defined in earliest task using each name; later tasks consistently import from those modules.

---

# Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-28-user-web-ux-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
