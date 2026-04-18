"""Role & KB permission management."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class RoleService:
    """Role assignment and KB-level permission management."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    # ── Roles ──

    async def get_user_roles(self, user_id: str) -> list[dict]:
        """Get all role assignments for a user (by internal ID or external_id)."""
        from src.auth.models import UserModel, UserRoleModel, RoleModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                return []

            internal_id = row[0]
            result = await session.execute(
                select(UserRoleModel, RoleModel)
                .join(RoleModel, UserRoleModel.role_id == RoleModel.id)
                .where(UserRoleModel.user_id == internal_id)
            )
            return [
                {
                    "role": role.name,
                    "display_name": role.display_name,
                    "scope_type": ur.scope_type,
                    "scope_id": ur.scope_id,
                    "expires_at": str(ur.expires_at) if ur.expires_at else None,
                }
                for ur, role in result.all()
            ]

    async def assign_role(
        self,
        user_id: str,
        role_name: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        granted_by: str | None = None,
    ) -> dict:
        """Assign a role to a user."""
        from src.auth.models import UserModel, RoleModel, UserRoleModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                raise ValueError(f"User not found: {user_id}")
            internal_user_id = row[0]

            result = await session.execute(
                select(RoleModel).where(RoleModel.name == role_name)
            )
            role = result.scalar_one_or_none()
            if not role:
                raise ValueError(f"Role not found: {role_name}")

            assignment = UserRoleModel(
                id=str(uuid.uuid4()),
                user_id=internal_user_id,
                role_id=role.id,
                scope_type=scope_type,
                scope_id=scope_id,
                granted_by=granted_by,
            )
            session.add(assignment)
            await session.commit()
            return {
                "id": assignment.id, "role": role_name,
                "scope_type": scope_type, "scope_id": scope_id,
            }

    async def revoke_role(
        self, user_id: str, role_name: str,
        scope_type: str | None = None, scope_id: str | None = None,
    ) -> bool:
        """Revoke a role from a user."""
        from src.auth.models import UserModel, RoleModel, UserRoleModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                return False
            internal_user_id = row[0]

            result = await session.execute(
                select(RoleModel.id).where(RoleModel.name == role_name)
            )
            role_row = result.first()
            if not role_row:
                return False

            q = delete(UserRoleModel).where(
                UserRoleModel.user_id == internal_user_id,
                UserRoleModel.role_id == role_row[0],
            )
            if scope_type is not None:
                q = q.where(UserRoleModel.scope_type == scope_type)
            if scope_id is not None:
                q = q.where(UserRoleModel.scope_id == scope_id)

            result = await session.execute(q)
            await session.commit()
            return result.rowcount > 0

    # ── KB Permissions ──

    async def get_kb_permission(self, user_id: str, kb_id: str) -> str | None:
        """Get user's permission level for a specific KB."""
        from src.auth.models import UserModel, KBUserPermissionModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                return None
            internal_id = row[0]

            result = await session.execute(
                select(KBUserPermissionModel.permission_level).where(
                    KBUserPermissionModel.user_id == internal_id,
                    KBUserPermissionModel.kb_id == kb_id,
                )
            )
            return result.scalar_one_or_none()

    async def set_kb_permission(
        self, user_id: str, kb_id: str, permission_level: str,
        granted_by: str | None = None,
    ) -> dict:
        """Set user's permission level for a KB (upsert)."""
        from src.auth.models import UserModel, KBUserPermissionModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                raise ValueError(f"User not found: {user_id}")
            internal_id = row[0]

            result = await session.execute(
                select(KBUserPermissionModel).where(
                    KBUserPermissionModel.user_id == internal_id,
                    KBUserPermissionModel.kb_id == kb_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.permission_level = permission_level
                existing.granted_by = granted_by
            else:
                session.add(KBUserPermissionModel(
                    id=str(uuid.uuid4()),
                    kb_id=kb_id,
                    user_id=internal_id,
                    permission_level=permission_level,
                    granted_by=granted_by,
                ))

            await session.commit()
            return {"kb_id": kb_id, "user_id": user_id, "permission_level": permission_level}

    async def list_kb_permissions(self, kb_id: str) -> list[dict]:
        """List all user permissions for a KB."""
        from src.auth.models import UserModel, KBUserPermissionModel

        async with self._session() as session:
            result = await session.execute(
                select(KBUserPermissionModel, UserModel)
                .join(UserModel, KBUserPermissionModel.user_id == UserModel.id)
                .where(KBUserPermissionModel.kb_id == kb_id)
                .order_by(KBUserPermissionModel.permission_level.desc())
            )
            return [
                {
                    "user_id": user.id,
                    "email": user.email,
                    "display_name": user.display_name,
                    "permission_level": perm.permission_level,
                    "granted_by": perm.granted_by,
                }
                for perm, user in result.all()
            ]

    async def remove_kb_permission(self, user_id: str, kb_id: str) -> bool:
        """Remove user's KB permission."""
        from src.auth.models import UserModel, KBUserPermissionModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                return False

            result = await session.execute(
                delete(KBUserPermissionModel).where(
                    KBUserPermissionModel.user_id == row[0],
                    KBUserPermissionModel.kb_id == kb_id,
                )
            )
            await session.commit()
            return result.rowcount > 0
