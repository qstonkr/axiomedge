"""Chat privacy consent — server-side legal trail (PIPA).

Complements the client-side PrivacyConsent modal. Records:
- when the user accepted a given policy_version
- whether (and when) the user later withdrew that consent (PIPA §37)

State machine on (user_id, policy_version):
    missing                  → never accepted
    withdrawn_at IS NULL     → active
    withdrawn_at IS NOT NULL → withdrawn

Re-accept after withdrawal updates the same row in place — single legal-
trail row per user × version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import ChatPrivacyConsentModel
from src.stores.postgres.repositories.base import BaseRepository


@dataclass
class ConsentRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    org_id: str
    policy_version: str
    accepted_at: datetime
    withdrawn_at: datetime | None
    ip_address: str | None
    user_agent: str | None

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None


def _record_from_row(row: ChatPrivacyConsentModel) -> ConsentRecord:
    return ConsentRecord(
        id=row.id,
        user_id=row.user_id,
        org_id=row.org_id,
        policy_version=row.policy_version,
        accepted_at=row.accepted_at,
        withdrawn_at=row.withdrawn_at,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
    )


class ChatPrivacyConsentRepository(BaseRepository):
    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)

    async def accept(
        self,
        *,
        user_id: uuid.UUID,
        org_id: str,
        policy_version: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ConsentRecord:
        """Idempotent accept — INSERT or UPDATE.

        - first accept   → INSERT new row
        - re-accept      → no-op (row already active)
        - re-accept post-withdraw → clears withdrawn_at + bumps accepted_at
        """
        async with self._session_maker() as session:
            async with session.begin():
                stmt = (
                    pg_insert(ChatPrivacyConsentModel)
                    .values(
                        user_id=user_id,
                        org_id=org_id,
                        policy_version=policy_version,
                        ip_address=ip_address,
                        user_agent=user_agent,
                    )
                    .on_conflict_do_update(
                        index_elements=["user_id", "policy_version"],
                        # Only refresh accepted_at + clear withdrawn_at when the
                        # row was previously withdrawn. An already-active row is
                        # left untouched (preserves original accepted_at).
                        set_={
                            "accepted_at": datetime.now(UTC),
                            "withdrawn_at": None,
                            "ip_address": ip_address,
                            "user_agent": user_agent,
                        },
                        where=ChatPrivacyConsentModel.withdrawn_at.is_not(None),
                    )
                )
                await session.execute(stmt)
                result = await session.execute(
                    select(ChatPrivacyConsentModel).where(
                        ChatPrivacyConsentModel.user_id == user_id,
                        ChatPrivacyConsentModel.policy_version == policy_version,
                    )
                )
                return _record_from_row(result.scalar_one())

    async def withdraw(
        self, *, user_id: uuid.UUID, policy_version: str,
    ) -> ConsentRecord | None:
        """PIPA §37 — set ``withdrawn_at = now()`` on the user's row.

        Returns the updated record, or None if there's nothing to withdraw
        (no row, or already withdrawn).
        """
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(ChatPrivacyConsentModel)
                    .where(
                        ChatPrivacyConsentModel.user_id == user_id,
                        ChatPrivacyConsentModel.policy_version == policy_version,
                        ChatPrivacyConsentModel.withdrawn_at.is_(None),
                    )
                    .values(withdrawn_at=datetime.now(UTC))
                    .returning(ChatPrivacyConsentModel)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                return _record_from_row(row)

    async def get_for_user(
        self, user_id: uuid.UUID, policy_version: str,
    ) -> ConsentRecord | None:
        """Return the row regardless of state. Use ``record.is_active``
        to distinguish active vs withdrawn at the call site."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(ChatPrivacyConsentModel).where(
                    ChatPrivacyConsentModel.user_id == user_id,
                    ChatPrivacyConsentModel.policy_version == policy_version,
                )
            )
            row = result.scalar_one_or_none()
            return _record_from_row(row) if row is not None else None

    async def get_active_for_user(
        self, user_id: uuid.UUID, policy_version: str,
    ) -> ConsentRecord | None:
        """Convenience: return the row only if currently active."""
        record = await self.get_for_user(user_id, policy_version)
        if record is None or not record.is_active:
            return None
        return record
