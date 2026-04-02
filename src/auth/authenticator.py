"""Internal authentication — email/password login, registration, password change."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.auth.user_crud import UserCRUD

logger = logging.getLogger(__name__)

# Valid bcrypt hash for constant-time comparison when user not found
_DUMMY_BCRYPT_HASH = "$2b$12$LJ3m4ys3Lg7VGgHepMzL2OGOCISCgrMJwBdJmkGBo7MBJe.ys/Cfi"


class Authenticator:
    """Email/password authentication for internal provider."""

    def __init__(self, session_factory: async_sessionmaker, user_crud: UserCRUD):
        self._session = session_factory
        self._user_crud = user_crud

    async def authenticate(self, email: str, password: str) -> dict | None:
        """Verify email/password. Returns user dict or None."""
        from src.auth.models import UserModel
        from src.auth.password import verify_password

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    UserModel.email == email,
                    UserModel.provider == "internal",
                    UserModel.is_active.is_(True),
                    UserModel.status == "active",
                )
            )
            user = result.scalar_one_or_none()
            if not user or not user.password_hash:
                verify_password(password, _DUMMY_BCRYPT_HASH)  # Constant-time
                return None
            if not verify_password(password, user.password_hash):
                return None

            user.last_login_at = datetime.now(timezone.utc)
            await session.commit()

            return {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "department": user.department,
                "organization_id": user.organization_id,
            }

    async def create_user_with_password(
        self,
        email: str,
        password: str,
        display_name: str,
        department: str | None = None,
        organization_id: str | None = None,
        role: str = "viewer",
    ) -> dict:
        """Create a user with email/password (internal provider)."""
        from src.auth.models import UserModel
        from src.auth.password import hash_password

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.email == email)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"User with email '{email}' already exists")

            user_id = str(uuid.uuid4())
            user = UserModel(
                id=user_id,
                external_id=f"internal:{email}",
                provider="internal",
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status="active",
                is_active=True,
                department=department,
                organization_id=organization_id,
            )
            session.add(user)
            await session.flush()
            await self._user_crud._assign_default_role(session, user_id, [role])
            await session.commit()

            return {"id": user_id, "email": email, "display_name": display_name, "role": role}

    async def change_password(
        self, user_id: str, old_password: str, new_password: str
    ) -> bool:
        """Change password for internal user. Verifies old password first."""
        from src.auth.models import UserModel
        from src.auth.password import verify_password, hash_password

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    UserModel.id == user_id,
                    UserModel.provider == "internal",
                    UserModel.is_active.is_(True),
                    UserModel.status == "active",
                )
            )
            user = result.scalar_one_or_none()
            if not user or not user.password_hash:
                return False
            if not verify_password(old_password, user.password_hash):
                return False
            user.password_hash = hash_password(new_password)
            await session.commit()
            return True
