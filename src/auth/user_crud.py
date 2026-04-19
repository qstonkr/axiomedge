"""User CRUD operations — create, read, update, delete, sync from IdP."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)


async def _ensure_personal_kb(
    user_id: str, organization_id: str | None, display_name: str,
) -> None:
    """Create the user's first personal KB on signup.

    Called from ``sync_user_from_idp`` (first time only) and ``create_user``.
    Idempotent — bails out if any personal KB already exists for the user.
    Failures are logged but do not block user creation; a missing personal
    KB is recoverable (the /my-knowledge UI will let the user create one).

    Lives at module scope so it doesn't capture ``UserCRUD`` and can be
    awaited *after* the user-creation transaction has committed (Postgres
    FK to ``auth_users`` would otherwise fail inside the same uncommitted
    session).
    """
    if not organization_id:
        return

    try:
        from src.config import get_settings
        from src.stores.postgres.repositories.kb_registry import KBRegistryRepository
    except ImportError:
        logger.debug("Settings/registry modules unavailable; skipping personal KB seed")
        return

    try:
        settings = get_settings()
        repo = KBRegistryRepository(settings.database.database_url)
        await repo.initialize()
        try:
            existing = await repo.list_by_tier(
                "personal", organization_id=organization_id, owner_id=user_id,
            )
            if existing:
                return  # already seeded — idempotent

            kb_id = f"pkb_{user_id.replace('-', '')[:12]}_001"
            await repo.create_kb({
                "id": kb_id,
                "name": f"{display_name} 의 지식",
                "description": "자동 생성된 개인 지식 베이스",
                "tier": "personal",
                "organization_id": organization_id,
                "owner_id": user_id,
                "status": "active",
                "settings": {},
                "dataset_ids_by_env": {},
                "sync_sources": [],
            })
            logger.info("Seeded personal KB for new user %s: %s", user_id, kb_id)
        finally:
            await repo.shutdown()
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Personal KB seeding failed for user %s: %s", user_id, e)


class UserCRUD:
    """User management operations backed by PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    async def sync_user_from_idp(self, auth_user: AuthUser) -> dict:
        """Create or update user from IdP token claims.

        On the first sync (new user) we also seed a personal KB so the
        Streamlit/Next.js "내 지식" page has something to show on first load.
        Subsequent syncs are idempotent — no extra KBs are created.
        """
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.external_id == auth_user.sub)
            )
            user = result.scalar_one_or_none()
            is_new_user = user is None

            if user:
                user.email = auth_user.email
                user.display_name = auth_user.display_name
                user.department = auth_user.department
                user.organization_id = auth_user.organization_id
                user.last_login_at = datetime.now(timezone.utc)
                user.metadata_ = auth_user.raw_claims
            else:
                user = UserModel(
                    id=str(uuid.uuid4()),
                    external_id=auth_user.sub,
                    provider=auth_user.provider,
                    email=auth_user.email,
                    display_name=auth_user.display_name,
                    department=auth_user.department,
                    organization_id=auth_user.organization_id,
                    last_login_at=datetime.now(timezone.utc),
                    metadata_=auth_user.raw_claims,
                )
                session.add(user)
                await self._assign_default_role(session, user.id, auth_user.roles)

            await session.commit()

            if is_new_user:
                await _ensure_personal_kb(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    display_name=user.display_name or user.email,
                )

            return {"id": user.id, "email": user.email}

    async def _assign_default_role(
        self, session: AsyncSession, user_id: str, idp_roles: list[str]
    ) -> None:
        """Assign default role based on IdP roles."""
        from src.auth.models import RoleModel, UserRoleModel

        role_name = "viewer"
        for idp_role in idp_roles:
            idp_lower = idp_role.lower()
            if "admin" in idp_lower:
                role_name = "admin"
                break
            elif "manager" in idp_lower or "관리자" in idp_lower:
                role_name = "kb_manager"
            elif "editor" in idp_lower or "편집" in idp_lower:
                role_name = "editor"
            elif "contributor" in idp_lower or "기여" in idp_lower:
                role_name = "contributor"

        result = await session.execute(
            select(RoleModel).where(RoleModel.name == role_name)
        )
        role = result.scalar_one_or_none()
        if role:
            session.add(UserRoleModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                role_id=role.id,
            ))

    async def create_user(
        self,
        email: str,
        display_name: str,
        department: str | None = None,
        organization_id: str | None = None,
        role: str = "viewer",
    ) -> dict:
        """Create a local user manually + seed a personal KB."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.email == email)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"User with email '{email}' already exists")

            user_id = str(uuid.uuid4())
            user = UserModel(
                id=user_id,
                external_id=f"local:{email}",
                provider="local",
                email=email,
                display_name=display_name,
                department=department,
                organization_id=organization_id,
            )
            session.add(user)
            await session.flush()
            await self._assign_default_role(session, user_id, [role])
            await session.commit()

        await _ensure_personal_kb(
            user_id=user_id,
            organization_id=organization_id,
            display_name=display_name,
        )
        return {"id": user_id, "email": email, "display_name": display_name, "role": role}

    async def update_user(
        self,
        user_id: str,
        display_name: str | None = None,
        department: str | None = None,
        organization_id: str | None = None,
        is_active: bool | None = None,
    ) -> dict | None:
        """Update user fields."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            user = result.scalar_one_or_none()
            if not user:
                return None

            if display_name is not None:
                user.display_name = display_name
            if department is not None:
                user.department = department
            if organization_id is not None:
                user.organization_id = organization_id
            if is_active is not None:
                user.is_active = is_active
                user.status = "active" if is_active else "inactive"

            await session.commit()
            return {"id": user.id, "email": user.email, "updated": True}

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user and all related role assignments."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            user = result.scalar_one_or_none()
            if not user:
                return False
            await session.delete(user)
            await session.commit()
            return True

    async def get_user(self, user_id: str) -> dict | None:
        """Get user by internal ID or external_id."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            user = result.scalar_one_or_none()
            if not user:
                return None
            return {
                "id": user.id,
                "external_id": user.external_id,
                "email": user.email,
                "display_name": user.display_name,
                "provider": user.provider,
                "department": user.department,
                "organization_id": user.organization_id,
                "is_active": user.is_active,
                "last_login_at": str(user.last_login_at) if user.last_login_at else None,
            }

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List users with pagination."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel)
                .order_by(UserModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                {
                    "id": u.id,
                    "email": u.email,
                    "display_name": u.display_name,
                    "provider": u.provider,
                    "department": u.department,
                    "is_active": u.is_active,
                }
                for u in result.scalars().all()
            ]
