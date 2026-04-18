"""Organization & membership service.

Tenant ownership is the SSOT for multi-tenant scoping (B-0). Every authenticated
request resolves to one ``active_org_id`` — either from the JWT claim or, if the
user belongs to a single org, automatically.

This module deliberately stays narrow: membership lookup + grant. User CRUD
lives in :mod:`src.auth.user_crud`, role/permission grants in
:mod:`src.auth.role_service`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = "default-org"


class OrgService:
    """Organization membership management."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    async def list_user_memberships(self, user_id: str) -> list[dict]:
        """Return all active org memberships for a user.

        Each entry: {organization_id, role, status, joined_at}.
        """
        from src.stores.postgres.models import OrgMembershipModel

        async with self._session() as session:
            result = await session.execute(
                select(OrgMembershipModel)
                .where(
                    OrgMembershipModel.user_id == user_id,
                    OrgMembershipModel.status == "active",
                )
            )
            return [
                {
                    "organization_id": m.organization_id,
                    "role": m.role,
                    "status": m.status,
                    "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                }
                for m in result.scalars().all()
            ]

    async def is_member(self, user_id: str, organization_id: str) -> bool:
        """Check whether user has an active membership in the org."""
        from src.stores.postgres.models import OrgMembershipModel

        async with self._session() as session:
            result = await session.execute(
                select(OrgMembershipModel.id)
                .where(
                    OrgMembershipModel.user_id == user_id,
                    OrgMembershipModel.organization_id == organization_id,
                    OrgMembershipModel.status == "active",
                )
                .limit(1)
            )
            return result.first() is not None

    async def add_member(
        self,
        user_id: str,
        organization_id: str,
        role: str = "MEMBER",
        invited_by: str | None = None,
    ) -> dict:
        """Add user to org. Idempotent — returns existing row if already a member."""
        from src.stores.postgres.models import OrgMembershipModel

        async with self._session() as session:
            existing = await session.execute(
                select(OrgMembershipModel).where(
                    OrgMembershipModel.user_id == user_id,
                    OrgMembershipModel.organization_id == organization_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                return {
                    "id": row.id,
                    "user_id": row.user_id,
                    "organization_id": row.organization_id,
                    "role": row.role,
                    "status": row.status,
                }

            now = datetime.now(timezone.utc)
            membership = OrgMembershipModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                invited_by=invited_by,
                invited_at=now,
                joined_at=now,
                status="active",
            )
            session.add(membership)
            await session.commit()
            return {
                "id": membership.id,
                "user_id": user_id,
                "organization_id": organization_id,
                "role": role,
                "status": "active",
            }

    async def resolve_active_org_id(
        self,
        user_id: str,
        requested_org_id: str | None = None,
    ) -> str | None:
        """Pick the org this session should be scoped to.

        Priority:
          1. ``requested_org_id`` — caller-supplied (e.g., switch-org); validated.
          2. Single membership — auto-resolved.
          3. Multiple memberships — None (caller must prompt user to select).
          4. Zero memberships — None.

        Returning None signals "no org context"; callers decide whether that
        is fatal (most multi-tenant routes will 403/409) or fine (e.g., the
        org-list endpoint).
        """
        if requested_org_id:
            if await self.is_member(user_id, requested_org_id):
                return requested_org_id
            return None

        memberships = await self.list_user_memberships(user_id)
        if len(memberships) == 1:
            return memberships[0]["organization_id"]
        return None
