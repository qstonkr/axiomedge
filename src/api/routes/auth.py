"""Auth & Permission Management API Routes.

Endpoints:
    - GET  /auth/me                    - Current user info
    - GET  /auth/users                 - List users (admin)
    - GET  /auth/users/{user_id}       - Get user details (admin)

    - GET  /auth/roles                 - List all roles
    - POST /auth/users/{user_id}/roles - Assign role
    - DELETE /auth/users/{user_id}/roles/{role_name} - Revoke role

    - GET  /auth/kb/{kb_id}/permissions     - List KB permissions
    - POST /auth/kb/{kb_id}/permissions     - Set KB permission
    - DELETE /auth/kb/{kb_id}/permissions/{user_id} - Remove KB permission

    - GET  /auth/my-activities              - My activity log
    - GET  /auth/my-activities/summary      - My activity summary

    - GET  /auth/abac/policies              - List ABAC policies (admin)
    - POST /auth/abac/policies              - Create ABAC policy (admin)
    - PUT  /auth/abac/policies/{policy_id}  - Update ABAC policy (admin)
    - DELETE /auth/abac/policies/{policy_id} - Delete ABAC policy (admin)

    - GET  /auth/system/stats               - System auth stats (admin)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.auth.dependencies import get_current_user, require_permission
from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & Permissions"])


def _get_auth_service():
    from src.api.app import _get_state
    return _get_state().get("auth_service")


# =============================================================================
# Current User
# =============================================================================


@router.get("/me")
async def get_me(user: AuthUser = Depends(get_current_user)):
    """Get current user info and permissions."""
    auth_service = _get_auth_service()
    roles = []
    if auth_service:
        roles = await auth_service.get_user_roles(user.sub)

    from src.api.app import _get_state
    rbac = _get_state().get("rbac_engine")
    permissions = []
    if rbac:
        permissions = sorted(rbac.get_effective_permissions(roles))

    return {
        "sub": user.sub,
        "email": user.email,
        "display_name": user.display_name,
        "provider": user.provider,
        "department": user.department,
        "organization_id": user.organization_id,
        "roles": roles,
        "permissions": permissions,
    }


# =============================================================================
# User Management (admin)
# =============================================================================


@router.get("/users")
async def list_users(
    limit: int = 50,
    offset: int = 0,
    _user: AuthUser = Depends(require_permission("admin", "users")),
):
    """List all users."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"users": [], "total": 0}
    users = await auth_service.list_users(limit=limit, offset=offset)
    return {"users": users, "total": len(users)}


@router.post("/users")
async def create_user(
    body: dict[str, Any],
    _user: AuthUser = Depends(require_permission("admin", "users")),
):
    """Create a new local user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    email = body.get("email")
    display_name = body.get("display_name")
    if not email or not display_name:
        raise HTTPException(status_code=400, detail="'email' and 'display_name' are required")

    try:
        result = await auth_service.create_user(
            email=email,
            display_name=display_name,
            department=body.get("department"),
            organization_id=body.get("organization_id"),
            role=body.get("role", "viewer"),
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: dict[str, Any],
    _user: AuthUser = Depends(require_permission("admin", "users")),
):
    """Update user details."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    result = await auth_service.update_user(
        user_id=user_id,
        display_name=body.get("display_name"),
        department=body.get("department"),
        organization_id=body.get("organization_id"),
        is_active=body.get("is_active"),
    )
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, **result}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    _user: AuthUser = Depends(require_permission("admin", "users")),
):
    """Delete a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    success = await auth_service.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    _user: AuthUser = Depends(require_permission("admin", "users")),
):
    """Get user details."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")
    user = await auth_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    roles = await auth_service.get_user_roles(user_id)
    return {**user, "roles": roles}


# =============================================================================
# Role Management
# =============================================================================


@router.get("/roles")
async def list_roles(user: AuthUser = Depends(get_current_user)):
    """List all available roles."""
    from src.auth.rbac import DEFAULT_ROLES
    return {
        "roles": [
            {
                "name": name,
                "display_name": role_def["display_name"],
                "weight": role_def["weight"],
                "permissions": role_def["permissions"],
            }
            for name, role_def in DEFAULT_ROLES.items()
        ]
    }


@router.post("/users/{user_id}/roles")
async def assign_role(
    user_id: str,
    body: dict[str, Any],
    _user: AuthUser = Depends(require_permission("admin", "roles")),
):
    """Assign a role to a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    role_name = body.get("role")
    if not role_name:
        raise HTTPException(status_code=400, detail="Missing 'role' field")

    try:
        result = await auth_service.assign_role(
            user_id=user_id,
            role_name=role_name,
            scope_type=body.get("scope_type"),
            scope_id=body.get("scope_id"),
            granted_by=_user.sub,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/users/{user_id}/roles/{role_name}")
async def revoke_role(
    user_id: str,
    role_name: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    _user: AuthUser = Depends(require_permission("admin", "roles")),
):
    """Revoke a role from a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    success = await auth_service.revoke_role(user_id, role_name, scope_type, scope_id)
    if not success:
        raise HTTPException(status_code=404, detail="Role assignment not found")
    return {"success": True}


# =============================================================================
# KB Permission Management
# =============================================================================


@router.get("/kb/{kb_id}/permissions")
async def list_kb_permissions(
    kb_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """List all user permissions for a KB."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"permissions": []}
    perms = await auth_service.list_kb_permissions(kb_id)
    return {"kb_id": kb_id, "permissions": perms}


@router.post("/kb/{kb_id}/permissions")
async def set_kb_permission(
    kb_id: str,
    body: dict[str, Any],
    current_user: AuthUser = Depends(require_permission("kb", "manage")),
):
    """Set a user's permission level for a KB."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    user_id = body.get("user_id")
    permission_level = body.get("permission_level", "reader")

    if not user_id:
        raise HTTPException(status_code=400, detail="Missing 'user_id'")
    if permission_level not in ("reader", "contributor", "manager", "owner"):
        raise HTTPException(status_code=400, detail="Invalid permission_level")

    try:
        result = await auth_service.set_kb_permission(
            user_id=user_id,
            kb_id=kb_id,
            permission_level=permission_level,
            granted_by=current_user.sub,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/kb/{kb_id}/permissions/{user_id}")
async def remove_kb_permission(
    kb_id: str,
    user_id: str,
    _user: AuthUser = Depends(require_permission("kb", "manage")),
):
    """Remove a user's KB permission."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    success = await auth_service.remove_kb_permission(user_id, kb_id)
    if not success:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"success": True}


# =============================================================================
# My Activities ("나의 활동")
# =============================================================================


@router.get("/my-activities")
async def get_my_activities(
    activity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: AuthUser = Depends(get_current_user),
):
    """Get current user's activity log."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"activities": []}
    activities = await auth_service.get_user_activities(
        user_id=user.sub,
        activity_type=activity_type,
        limit=limit,
        offset=offset,
    )

    # Filter by date range if provided
    if date_from or date_to:

        filtered = []
        for act in activities:
            created = act.get("created_at", act.get("timestamp", ""))
            if not created:
                continue
            try:
                act_date = str(created)[:10]  # YYYY-MM-DD
                if date_from and act_date < date_from:
                    continue
                if date_to and act_date > date_to:
                    continue
                filtered.append(act)
            except (ValueError, TypeError):
                filtered.append(act)
        activities = filtered

    return {"activities": activities}


@router.get("/my-activities/summary")
async def get_my_activity_summary(
    days: int = 30,
    user: AuthUser = Depends(get_current_user),
):
    """Get activity summary for dashboard."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"period_days": days, "total": 0, "by_type": {}}
    return await auth_service.get_activity_summary(user.sub, days=days)


# =============================================================================
# ABAC Policy Management (admin)
# =============================================================================


@router.get("/abac/policies")
async def list_abac_policies(
    _user: AuthUser = Depends(require_permission("admin", "system")),
):
    """List all ABAC policies."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import select

    state = _get_state()
    auth_service = state.get("auth_service")
    if not auth_service:
        return {"policies": []}

    async with auth_service._session() as session:
        result = await session.execute(
            select(ABACPolicyModel).order_by(ABACPolicyModel.priority.desc())
        )
        policies = [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "resource_type": p.resource_type,
                "action": p.action,
                "conditions": p.conditions,
                "effect": p.effect,
                "priority": p.priority,
                "is_active": p.is_active,
            }
            for p in result.scalars().all()
        ]
    return {"policies": policies}


@router.post("/abac/policies")
async def create_abac_policy(
    body: dict[str, Any],
    _user: AuthUser = Depends(require_permission("admin", "system")),
):
    """Create a new ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    required = ["name", "resource_type", "action", "conditions", "effect"]
    for field in required:
        if field not in body:
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")

    async with auth_service._session() as session:
        policy = ABACPolicyModel(
            id=str(uuid.uuid4()),
            name=body["name"],
            description=body.get("description"),
            resource_type=body["resource_type"],
            action=body["action"],
            conditions=body["conditions"],
            effect=body["effect"],
            priority=body.get("priority", 0),
            is_active=body.get("is_active", True),
            created_by=_user.sub,
        )
        session.add(policy)
        await session.commit()
        return {"success": True, "id": policy.id}


@router.put("/abac/policies/{policy_id}")
async def update_abac_policy(
    policy_id: str,
    body: dict[str, Any],
    _user: AuthUser = Depends(require_permission("admin", "system")),
):
    """Update an ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import select

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    async with auth_service._session() as session:
        result = await session.execute(
            select(ABACPolicyModel).where(ABACPolicyModel.id == policy_id)
        )
        policy = result.scalar_one_or_none()
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")

        for field in ("name", "description", "resource_type", "action", "conditions", "effect", "priority", "is_active"):
            if field in body:
                setattr(policy, field, body[field])

        await session.commit()
        return {"success": True}


@router.delete("/abac/policies/{policy_id}")
async def delete_abac_policy(
    policy_id: str,
    _user: AuthUser = Depends(require_permission("admin", "system")),
):
    """Delete an ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import delete

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    async with auth_service._session() as session:
        result = await session.execute(
            delete(ABACPolicyModel).where(ABACPolicyModel.id == policy_id)
        )
        await session.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Policy not found")
        return {"success": True}


# =============================================================================
# System Auth Stats (admin - "시스템운영")
# =============================================================================


@router.get("/system/stats")
async def get_system_auth_stats(
    _user: AuthUser = Depends(require_permission("admin", "system")),
):
    """System-level auth statistics for operations dashboard."""
    from src.auth.models import UserModel, UserRoleModel, KBUserPermissionModel, UserActivityLogModel, ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import select, func
    from datetime import timedelta, datetime, timezone

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        return {}

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    async with auth_service._session() as session:
        total_users = (await session.execute(select(func.count(UserModel.id)))).scalar() or 0
        active_users = (await session.execute(
            select(func.count(UserModel.id)).where(UserModel.is_active.is_(True))
        )).scalar() or 0
        total_role_assignments = (await session.execute(select(func.count(UserRoleModel.id)))).scalar() or 0
        total_kb_permissions = (await session.execute(select(func.count(KBUserPermissionModel.id)))).scalar() or 0
        active_policies = (await session.execute(
            select(func.count(ABACPolicyModel.id)).where(ABACPolicyModel.is_active.is_(True))
        )).scalar() or 0

        # Activity stats
        activities_24h = (await session.execute(
            select(func.count(UserActivityLogModel.id)).where(UserActivityLogModel.created_at >= day_ago)
        )).scalar() or 0
        activities_7d = (await session.execute(
            select(func.count(UserActivityLogModel.id)).where(UserActivityLogModel.created_at >= week_ago)
        )).scalar() or 0
        unique_users_24h = (await session.execute(
            select(func.count(func.distinct(UserActivityLogModel.user_id))).where(
                UserActivityLogModel.created_at >= day_ago
            )
        )).scalar() or 0

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "active_24h": unique_users_24h,
        },
        "roles": {
            "total_assignments": total_role_assignments,
        },
        "kb_permissions": {
            "total": total_kb_permissions,
        },
        "abac_policies": {
            "active": active_policies,
        },
        "activities": {
            "last_24h": activities_24h,
            "last_7d": activities_7d,
        },
    }
