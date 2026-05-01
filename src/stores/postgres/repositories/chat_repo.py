"""Chat conversations + messages repository.

Encrypts message body via pgcrypto pgp_sym_encrypt. If encryption_key is empty
(dev), stores plaintext bytes prefixed with PLAINTEXT_SENTINEL so reads can
distinguish plaintext from encrypted blobs in mixed-write deployments.
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

# Magic prefix for plaintext rows so a row written with an empty key can be
# read back even after encryption is later turned on. Encrypted blobs from
# pgp_sym_encrypt always start with the pgp framing byte 0xc3, never with this
# ASCII prefix, so detection is unambiguous.
PLAINTEXT_SENTINEL = b"\x00plain:"

# Hard floor for retention purge — refuse delete if days < this. Prevents a
# misconfigured cron or ad-hoc call from nuking an entire user base.
MIN_RETENTION_DAYS = 7


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


def _decode_plain(content_enc: bytes) -> str:
    """Decode plaintext-fallback bytes; tolerates rows written before the
    sentinel was introduced (raw utf-8 prefix)."""
    if content_enc.startswith(PLAINTEXT_SENTINEL):
        return content_enc[len(PLAINTEXT_SENTINEL):].decode("utf-8")
    return content_enc.decode("utf-8")


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
        """Set title only if currently empty AND not soft-deleted — used by
        auto-title task. Skipping deleted rows avoids a race where the user
        deletes a conversation while the title job is still in flight."""
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(ChatConversationModel)
                    .where(
                        ChatConversationModel.id == conv_id,
                        ChatConversationModel.title == "",
                        ChatConversationModel.deleted_at.is_(None),
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
                    # Sentinel-prefixed plaintext so reads can distinguish from
                    # pgp_sym_encrypt blobs after a future encryption rollout.
                    enc = PLAINTEXT_SENTINEL + content.encode("utf-8")
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
                # bump conversation updated_at — keeps active-chat retention
                # accurate (purge uses updated_at, not created_at).
                await session.execute(
                    update(ChatConversationModel)
                    .where(ChatConversationModel.id == conversation_id)
                    .values(updated_at=datetime.now(UTC))
                )
                return row.id

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> list[DecodedMessage]:
        """Decrypt + return messages for a conversation.

        ``user_id`` is optional but recommended — when provided, the join with
        ``chat_conversations`` enforces ownership at the repo layer (defense in
        depth on top of the route's existing get_conversation check).
        """
        async with self._session_maker() as session:
            # Defense-in-depth: confirm conversation belongs to the requesting
            # user before reading message bodies.
            if user_id is not None:
                owner = await session.scalar(
                    select(ChatConversationModel.user_id).where(
                        ChatConversationModel.id == conversation_id,
                        ChatConversationModel.deleted_at.is_(None),
                    )
                )
                if owner != user_id:
                    return []

            # Per-row decode handles mixed plaintext + encrypted blobs (e.g.
            # rows written before encryption was enabled). Encrypted rows go
            # through pgp_sym_decrypt; plaintext rows skip it.
            result = await session.execute(
                select(ChatMessageModel).where(
                    ChatMessageModel.conversation_id == conversation_id,
                ).order_by(ChatMessageModel.created_at.asc())
            )
            rows = list(result.scalars().all())

        decoded: list[DecodedMessage] = []
        for r in rows:
            blob: bytes = r.content_enc
            if blob.startswith(PLAINTEXT_SENTINEL) or not self._key:
                content = _decode_plain(blob)
            else:
                # encrypted — decrypt via pgcrypto round-trip
                async with self._session_maker() as session:
                    content = await session.scalar(
                        select(text("pgp_sym_decrypt(:body, :key)").bindparams(
                            body=blob, key=self._key,
                        ))
                    ) or ""
            decoded.append(
                DecodedMessage(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    role=r.role,
                    content=content,
                    chunks=r.chunks,
                    meta=r.meta,
                    trace_id=r.trace_id,
                    created_at=r.created_at,
                )
            )
        return decoded

    # --- retention ------------------------------------------------------

    async def purge_older_than(self, days: int) -> int:
        """Hard delete conversations whose last activity (updated_at) is older
        than ``days``. Cascades to messages.

        Uses ``updated_at`` rather than ``created_at`` so an active 90+ day
        chat keeps living. ``days < MIN_RETENTION_DAYS`` is rejected to guard
        against misconfigured cron or ad-hoc calls.
        """
        if days < MIN_RETENTION_DAYS:
            raise ValueError(
                f"retention floor is {MIN_RETENTION_DAYS} days; got days={days}",
            )
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ChatConversationModel).where(
                        ChatConversationModel.updated_at < cutoff,
                    )
                )
                return result.rowcount or 0
