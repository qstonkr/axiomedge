"""Auth Service - User/Role/Permission management.

Handles user sync from IdP, role assignments, KB permissions,
and activity logging.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)


# Valid bcrypt hash for constant-time comparison when user not found (timing side-channel防止)
_DUMMY_BCRYPT_HASH = "$2b$12$LJ3m4ys3Lg7VGgHepMzL2OGOCISCgrMJwBdJmkGBo7MBJe.ys/Cfi"


class AuthService:
    """Auth service for user, role, and permission management."""

    def __init__(self, database_url: str, pool_size: int = 5, max_overflow: int = 10):
        self._engine = create_async_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        await self._engine.dispose()

    def _session(self) -> AsyncSession:
        return self._session_factory()

    # =========================================================================
    # User Management
    # =========================================================================

    async def sync_user_from_idp(self, auth_user: AuthUser) -> dict:
        """Create or update user from IdP token claims."""
        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.external_id == auth_user.sub)
            )
            user = result.scalar_one_or_none()

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

                # Auto-assign default role
                await self._assign_default_role(session, user.id, auth_user.roles)

            await session.commit()
            return {"id": user.id, "email": user.email}

    async def _assign_default_role(
        self, session: AsyncSession, user_id: str, idp_roles: list[str]
    ) -> None:
        """Assign default role based on IdP roles."""
        from src.auth.models import RoleModel, UserRoleModel

        # Map IdP roles to local roles
        role_name = "viewer"  # Default
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
        """Create a local user manually."""
        from src.auth.models import UserModel

        async with self._session() as session:
            # Check duplicate email
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

            # Assign role
            await self._assign_default_role(session, user_id, [role])
            await session.commit()

            return {
                "id": user_id,
                "email": email,
                "display_name": display_name,
                "role": role,
            }

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

    # =========================================================================
    # Internal Auth (email/password)
    # =========================================================================

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
            await self._assign_default_role(session, user_id, [role])
            await session.commit()

            return {
                "id": user_id,
                "email": email,
                "display_name": display_name,
                "role": role,
            }

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

    # =========================================================================
    # Role Management
    # =========================================================================

    async def get_user_roles(self, user_id: str) -> list[dict]:
        """Get all role assignments for a user (by internal ID or external_id)."""
        from src.auth.models import UserModel, UserRoleModel, RoleModel

        async with self._session() as session:
            # Resolve external_id to internal id
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
            # Resolve user
            result = await session.execute(
                select(UserModel.id).where(
                    (UserModel.id == user_id) | (UserModel.external_id == user_id)
                )
            )
            row = result.first()
            if not row:
                raise ValueError(f"User not found: {user_id}")
            internal_user_id = row[0]

            # Resolve role
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
            return {"id": assignment.id, "role": role_name, "scope_type": scope_type, "scope_id": scope_id}

    async def revoke_role(self, user_id: str, role_name: str, scope_type: str | None = None, scope_id: str | None = None) -> bool:
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

    # =========================================================================
    # KB Permission Management
    # =========================================================================

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
            perm = result.scalar_one_or_none()
            return perm

    async def set_kb_permission(
        self,
        user_id: str,
        kb_id: str,
        permission_level: str,
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

    # =========================================================================
    # Activity Logging
    # =========================================================================

    async def log_activity(
        self,
        user_id: str,
        activity_type: str,
        resource_type: str,
        resource_id: str | None = None,
        kb_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log a user activity (non-blocking best-effort)."""
        from src.auth.models import UserActivityLogModel

        try:
            async with self._session() as session:
                session.add(UserActivityLogModel(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    activity_type=activity_type,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    kb_id=kb_id,
                    details=details or {},
                    ip_address=ip_address,
                    user_agent=user_agent,
                ))
                await session.commit()
        except Exception as e:
            logger.debug("Activity log failed: %s", e)

    async def get_user_activities(
        self,
        user_id: str,
        activity_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get user's activity history ("나의 활동")."""
        from src.auth.models import UserActivityLogModel

        async with self._session() as session:
            q = select(UserActivityLogModel).where(
                UserActivityLogModel.user_id == user_id
            )
            if activity_type:
                q = q.where(UserActivityLogModel.activity_type == activity_type)

            q = q.order_by(UserActivityLogModel.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(q)
            return [
                {
                    "id": a.id,
                    "activity_type": a.activity_type,
                    "resource_type": a.resource_type,
                    "resource_id": a.resource_id,
                    "kb_id": a.kb_id,
                    "details": a.details,
                    "created_at": str(a.created_at),
                }
                for a in result.scalars().all()
            ]

    async def get_activity_summary(self, user_id: str, days: int = 30) -> dict:
        """Get activity summary for dashboard."""
        from src.auth.models import UserActivityLogModel

        async with self._session() as session:
            cutoff = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=days)

            result = await session.execute(
                select(
                    UserActivityLogModel.activity_type,
                    func.count(UserActivityLogModel.id),
                )
                .where(
                    UserActivityLogModel.user_id == user_id,
                    UserActivityLogModel.created_at >= cutoff,
                )
                .group_by(UserActivityLogModel.activity_type)
            )
            counts = {row[0]: row[1] for row in result.all()}
            return {
                "period_days": days,
                "total": sum(counts.values()),
                "by_type": counts,
            }

    # =========================================================================
    # Seed Default Roles & Permissions
    # =========================================================================

    async def seed_defaults(self) -> None:
        """Create default roles and permissions if they don't exist."""
        from src.auth.models import RoleModel, PermissionModel, RolePermissionModel
        from src.auth.rbac import DEFAULT_ROLES

        async with self._session() as session:
            # Seed roles
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

                # Seed permissions for this role
                for perm_str in role_def["permissions"]:
                    if perm_str == "*:*":
                        continue
                    parts = perm_str.split(":", 1)
                    if len(parts) != 2:
                        continue
                    resource, action = parts

                    # Get or create permission
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

        # Seed default admin user for internal provider
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

        from src.auth.models import UserModel

        async with self._session() as session:
            result = await session.execute(
                select(UserModel).where(
                    UserModel.provider == "internal",
                    UserModel.email == "admin@knowledge.local",
                )
            )
            if result.scalar_one_or_none():
                return  # Already exists

        try:
            await self.create_user_with_password(
                email="admin@knowledge.local",
                password=initial_pw,
                display_name="Admin",
                role="admin",
            )
            logger.info("Default internal admin user created (admin@knowledge.local)")
        except ValueError:
            pass  # Already exists (race condition guard)
