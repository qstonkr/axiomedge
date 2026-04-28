"""Chat privacy consent — server-side legal trail (PIPA).

Complements the client-side PrivacyConsent modal. Idempotent on
(user_id, policy_version): repeat accepts return the existing row, never
create a duplicate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy import select
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
    ip_address: str | None
    user_agent: str | None


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
        """Idempotent accept — ON CONFLICT DO NOTHING + SELECT existing row.

        Returns either the freshly-inserted row or the prior accept; either
        way the caller knows the user has consented to ``policy_version``.
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
                    .on_conflict_do_nothing(
                        index_elements=["user_id", "policy_version"],
                    )
                )
                await session.execute(stmt)
                # SELECT current row (whether just inserted or pre-existing).
                result = await session.execute(
                    select(ChatPrivacyConsentModel).where(
                        ChatPrivacyConsentModel.user_id == user_id,
                        ChatPrivacyConsentModel.policy_version == policy_version,
                    )
                )
                row = result.scalar_one()
                return ConsentRecord(
                    id=row.id,
                    user_id=row.user_id,
                    org_id=row.org_id,
                    policy_version=row.policy_version,
                    accepted_at=row.accepted_at,
                    ip_address=row.ip_address,
                    user_agent=row.user_agent,
                )

    async def get_for_user(
        self, user_id: uuid.UUID, policy_version: str,
    ) -> ConsentRecord | None:
        async with self._session_maker() as session:
            result = await session.execute(
                select(ChatPrivacyConsentModel).where(
                    ChatPrivacyConsentModel.user_id == user_id,
                    ChatPrivacyConsentModel.policy_version == policy_version,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return ConsentRecord(
                id=row.id,
                user_id=row.user_id,
                org_id=row.org_id,
                policy_version=row.policy_version,
                accepted_at=row.accepted_at,
                ip_address=row.ip_address,
                user_agent=row.user_agent,
            )
