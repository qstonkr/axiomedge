"""Auth user management + role + KB permission route handlers — extracted from auth.py.

Contains: user CRUD, role management, KB permissions, and activity endpoints.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from src.auth.dependencies import get_current_user, require_permission
from src.auth.providers import AuthUser
from src.api.routes.auth_helpers import (
    CreateUserRequest,
    UpdateUserRequest,
    _AUTH_NOT_INIT,
    _USER_NOT_FOUND,
    _filter_activities_by_date,
)


def _get_auth_service() -> Any:
    """Late-bound accessor through parent auth module for test patchability."""
    import src.api.routes.auth as _auth
    return _auth._get_auth_service()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & Permissions"])


# =============================================================================
# User Management (admin)
# =============================================================================


@router.get("/users")
async def list_users(
    limit: int = 50,
    offset: int = 0,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))] = None,
) -> dict[str, Any]:
    """List all users."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"users": [], "total": 0}
    users = await auth_service.list_users(limit=limit, offset=offset)
    return {"users": users, "total": len(users)}


@router.post(
    "/users",
    responses={
        409: {"description": "User already exists"},
        503: {"description": "Auth service not initialized"},
    },
)
async def create_user(
    body: CreateUserRequest,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))],
) -> dict[str, Any]:
    """Create a new local user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    try:
        result = await auth_service.create_user(
            email=body.email,
            display_name=body.display_name,
            department=body.department,
            organization_id=body.organization_id,
            role=body.role,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put(
    "/users/{user_id}",
    responses={
        404: {"description": "User not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))],
) -> dict[str, Any]:
    """Update user details."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    result = await auth_service.update_user(
        user_id=user_id,
        display_name=body.display_name,
        department=body.department,
        organization_id=body.organization_id,
        is_active=body.is_active,
    )
    if not result:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    return {"success": True, **result}


@router.delete(
    "/users/{user_id}",
    responses={
        404: {"description": "User not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def delete_user(
    user_id: str,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))],
) -> dict[str, bool]:
    """Delete a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    success = await auth_service.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    return {"success": True}


@router.get(
    "/users/{user_id}",
    responses={
        404: {"description": "User not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def get_user(
    user_id: str,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))],
) -> dict[str, Any]:
    """Get user details."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)
    user = await auth_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    roles = await auth_service.get_user_roles(user_id)
    return {**user, "roles": roles}


# =============================================================================
# Role Management
# =============================================================================


@router.get("/roles")
async def list_roles(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict[str, list[dict[str, Any]]]:
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


@router.post(
    "/users/{user_id}/roles",
    responses={
        400: {"description": "Missing required fields"},
        404: {"description": "User or role not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def assign_role(
    user_id: str,
    body: dict[str, Any],
    _user: Annotated[AuthUser, Depends(require_permission("admin", "roles"))],
) -> dict[str, Any]:
    """Assign a role to a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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


@router.delete(
    "/users/{user_id}/roles/{role_name}",
    responses={
        404: {"description": "Role assignment not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def revoke_role(
    user_id: str,
    role_name: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "roles"))] = None,
) -> dict[str, bool]:
    """Revoke a role from a user."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict[str, Any]:
    """List all user permissions for a KB."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"permissions": []}
    perms = await auth_service.list_kb_permissions(kb_id)
    return {"kb_id": kb_id, "permissions": perms}


@router.post(
    "/kb/{kb_id}/permissions",
    responses={
        400: {"description": "Invalid input"},
        404: {"description": "User not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def set_kb_permission(
    kb_id: str,
    body: dict[str, Any],
    current_user: Annotated[AuthUser, Depends(require_permission("kb", "manage"))],
) -> dict[str, Any]:
    """Set a user's permission level for a KB."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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


@router.delete(
    "/kb/{kb_id}/permissions/{user_id}",
    responses={
        404: {"description": "Permission not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def remove_kb_permission(
    kb_id: str,
    user_id: str,
    _user: Annotated[AuthUser, Depends(require_permission("kb", "manage"))],
) -> dict[str, bool]:
    """Remove a user's KB permission."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    success = await auth_service.remove_kb_permission(user_id, kb_id)
    if not success:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"success": True}


# =============================================================================
# My Activities
# =============================================================================


@router.get("/my-activities")
async def get_my_activities(
    activity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: Annotated[AuthUser, Depends(get_current_user)] = None,
) -> dict[str, list[Any]]:
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
        activities = _filter_activities_by_date(activities, date_from, date_to)

    return {"activities": activities}


@router.get("/my-activities/summary")
async def get_my_activity_summary(
    days: int = 30,
    user: Annotated[AuthUser, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    """Get activity summary for dashboard."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"period_days": days, "total": 0, "by_type": {}}
    return await auth_service.get_activity_summary(user.sub, days=days)
