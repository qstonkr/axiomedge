"""Chat conversations + messages repository.

Encrypts message body via pgcrypto pgp_sym_encrypt. If encryption_key is empty
(dev), stores plaintext bytes — never use empty key in production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
                result = await session.execute(
                    text(
                        """
                        SELECT id, conversation_id, role,
                               pgp_sym_decrypt(content_enc, :key) AS content,
                               chunks, meta, trace_id, created_at
                        FROM chat_messages
                        WHERE conversation_id = :cid
                        ORDER BY created_at ASC
                        """
                    ).bindparams(cid=conversation_id, key=self._key)
                )
                return [
                    DecodedMessage(
                        id=r["id"],
                        conversation_id=r["conversation_id"],
                        role=r["role"],
                        content=r["content"],
                        chunks=r["chunks"],
                        meta=r["meta"],
                        trace_id=r["trace_id"],
                        created_at=r["created_at"],
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
