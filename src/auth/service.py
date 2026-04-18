"""Auth Service — facade combining user CRUD, authentication, roles, and activity logging.

Each responsibility is implemented in a dedicated module:
- user_crud.py: User sync, create, read, update, delete
- authenticator.py: Email/password login, registration, password change
- role_service.py: Role assignment, KB-level permissions
- activity_logger.py: Activity logging and querying

AuthService delegates to these modules while maintaining backward-compatible API.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.auth.activity_logger import ActivityLogger
from src.auth.authenticator import Authenticator
from src.auth.providers import AuthUser
from src.auth.role_service import RoleService
from src.auth.user_crud import UserCRUD

logger = logging.getLogger(__name__)


class AuthService:
    """Facade for all auth operations. Backward-compatible API."""

    def __init__(self, database_url: str, pool_size: int = 5, max_overflow: int = 10) -> None:
        self._engine = create_async_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        # Sub-services
        self._users = UserCRUD(self._session_factory)
        self._auth = Authenticator(self._session_factory, self._users)
        self._roles = RoleService(self._session_factory)
        self._activity = ActivityLogger(self._session_factory)

    async def close(self) -> None:
        await self._engine.dispose()

    def _session(self) -> Any:
        return self._session_factory()

    # ── User CRUD (delegates to UserCRUD) ──

    async def sync_user_from_idp(self, auth_user: AuthUser) -> dict:
        return await self._users.sync_user_from_idp(auth_user)

    async def create_user(self, email: str, display_name: str, **kwargs) -> dict:
        return await self._users.create_user(email, display_name, **kwargs)

    async def update_user(self, user_id: str, **kwargs) -> dict | None:
        return await self._users.update_user(user_id, **kwargs)

    async def delete_user(self, user_id: str) -> bool:
        return await self._users.delete_user(user_id)

    async def get_user(self, user_id: str) -> dict | None:
        return await self._users.get_user(user_id)

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        return await self._users.list_users(limit=limit, offset=offset)

    # ── Authentication (delegates to Authenticator) ──

    async def authenticate(self, email: str, password: str) -> dict | None:
        return await self._auth.authenticate(email, password)

    async def create_user_with_password(self, email: str, password: str, display_name: str, **kwargs) -> dict:
        return await self._auth.create_user_with_password(email, password, display_name, **kwargs)

    async def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        return await self._auth.change_password(user_id, old_password, new_password)

    # ── Roles & KB Permissions (delegates to RoleService) ──

    async def get_user_roles(self, user_id: str) -> list[dict]:
        return await self._roles.get_user_roles(user_id)

    async def assign_role(self, user_id: str, role_name: str, **kwargs) -> dict:
        return await self._roles.assign_role(user_id, role_name, **kwargs)

    async def revoke_role(self, user_id: str, role_name: str, scope_type=None, scope_id=None) -> bool:
        return await self._roles.revoke_role(user_id, role_name, scope_type, scope_id)

    async def get_kb_permission(self, user_id: str, kb_id: str) -> str | None:
        return await self._roles.get_kb_permission(user_id, kb_id)

    async def set_kb_permission(self, user_id: str, kb_id: str, permission_level: str, **kwargs) -> dict:
        return await self._roles.set_kb_permission(user_id, kb_id, permission_level, **kwargs)

    async def list_kb_permissions(self, kb_id: str) -> list[dict]:
        return await self._roles.list_kb_permissions(kb_id)

    async def remove_kb_permission(self, user_id: str, kb_id: str) -> bool:
        return await self._roles.remove_kb_permission(user_id, kb_id)

    # ── Activity Logging (delegates to ActivityLogger) ──

    async def log_activity(self, user_id: str, activity_type: str, resource_type: str, **kwargs) -> None:
        return await self._activity.log_activity(user_id, activity_type, resource_type, **kwargs)

    async def get_user_activities(self, user_id: str, **kwargs) -> list[dict]:
        return await self._activity.get_user_activities(user_id, **kwargs)

    async def get_activity_summary(self, user_id: str, days: int = 30) -> dict:
        return await self._activity.get_activity_summary(user_id, days=days)

    # ── Seeding ──

    async def seed_defaults(self) -> None:
        """Create default roles and permissions if they don't exist."""
        from sqlalchemy import select
        from src.auth.models import RoleModel, PermissionModel, RolePermissionModel
        from src.auth.rbac import DEFAULT_ROLES
        import uuid

        async with self._session_factory() as session:
            for role_name, role_def in DEFAULT_ROLES.items():
                result = await session.execute(
                    select(RoleModel).where(RoleModel.name == role_name)
                )
                if result.scalar_one_or_none():
                    continue

                role = RoleModel(
                    id=str(uuid.uuid4()),
                    name=role_name,
                    display_name=role_def["display_name"],
                    weight=role_def["weight"],
                    is_system=True,
                )
                session.add(role)
                await session.flush()

                for perm_str in role_def["permissions"]:
                    if perm_str == "*:*":
                        continue
                    parts = perm_str.split(":", 1)
                    if len(parts) != 2:
                        continue
                    resource, action = parts

                    result = await session.execute(
                        select(PermissionModel).where(
                            PermissionModel.resource == resource,
                            PermissionModel.action == action,
                        )
                    )
                    perm = result.scalar_one_or_none()
                    if not perm:
                        perm = PermissionModel(
                            id=str(uuid.uuid4()),
                            resource=resource,
                            action=action,
                        )
                        session.add(perm)
                        await session.flush()

                    session.add(RolePermissionModel(
                        id=str(uuid.uuid4()),
                        role_id=role.id,
                        permission_id=perm.id,
                    ))

            await session.commit()
            logger.info("Default roles and permissions seeded")

        await self._seed_internal_admin()

    async def _seed_internal_admin(self) -> None:
        """Create default admin user if AUTH_PROVIDER=internal and password is set."""
        import os
        if os.getenv("AUTH_PROVIDER", "local") != "internal":
            return

        initial_pw = os.getenv("AUTH_ADMIN_INITIAL_PASSWORD", "")
        if not initial_pw:
            logger.info("AUTH_ADMIN_INITIAL_PASSWORD not set — skipping admin seed")
            return

        from sqlalchemy import select
        from src.auth.models import UserModel

        async with self._session_factory() as session:
            result = await session.execute(
                select(UserModel).where(
                    UserModel.provider == "internal",
                    UserModel.email == "admin@knowledge.local",
                )
            )
            if result.scalar_one_or_none():
                return

        try:
            await self.create_user_with_password(
                email="admin@knowledge.local",
                password=initial_pw,
                display_name="Admin",
                role="admin",
            )
            logger.info("Default internal admin user created (admin@knowledge.local)")
        except ValueError:
            logger.debug("Default admin user already exists, skipping creation")
