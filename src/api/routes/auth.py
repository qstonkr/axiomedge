"""Auth & Permission Management API Routes.

Endpoints:
    Login/Session:
    - POST /auth/login                 - Email/password login (public)
    - POST /auth/refresh               - Refresh access token (public)
    - POST /auth/logout                - Logout (clear tokens)
    - POST /auth/register              - Create user with password (admin)
    - POST /auth/change-password       - Change own password

    User Info:
    - GET  /auth/me                    - Current user info
    - GET  /auth/users                 - List users (admin)
    - GET  /auth/users/{user_id}       - Get user details (admin)

    Roles:
    - GET  /auth/roles                 - List all roles
    - POST /auth/users/{user_id}/roles - Assign role
    - DELETE /auth/users/{user_id}/roles/{role_name} - Revoke role

    KB Permissions:
    - GET  /auth/kb/{kb_id}/permissions     - List KB permissions
    - POST /auth/kb/{kb_id}/permissions     - Set KB permission
    - DELETE /auth/kb/{kb_id}/permissions/{user_id} - Remove KB permission

    Activity:
    - GET  /auth/my-activities              - My activity log
    - GET  /auth/my-activities/summary      - My activity summary

    ABAC (admin):
    - GET  /auth/abac/policies              - List ABAC policies
    - POST /auth/abac/policies              - Create ABAC policy
    - PUT  /auth/abac/policies/{policy_id}  - Update ABAC policy
    - DELETE /auth/abac/policies/{policy_id} - Delete ABAC policy

    System:
    - GET  /auth/system/stats               - System auth stats (admin)
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from src.auth.dependencies import get_current_user, require_permission
from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & Permissions"])

_AUTH_NOT_INIT = "Auth service not initialized"
_REFRESH_PATH = "/api/v1/auth/refresh"
_USER_NOT_FOUND = "User not found"


def _get_auth_service():
    from src.api.app import _get_state
    return _get_state().get("auth_service")


def _get_state():
    from src.api.app import _get_state
    return _get_state()


def _is_cookie_secure() -> bool:
    """Determine cookie Secure flag from config (handles reverse proxy correctly)."""
    import os
    return os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"


# =============================================================================
# Request/Response Models
# =============================================================================


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    department: str | None = None
    organization_id: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# =============================================================================
# Login / Logout / Refresh
# =============================================================================


@router.post(
    "/login",
    responses={
        401: {"description": "Invalid email or password"},
        503: {"description": "Service not initialized"},
    },
)
async def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate with email/password, return JWT tokens in HttpOnly cookies."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    user = await auth_service.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    rbac = state.get("rbac_engine")

    if not jwt_service:
        raise HTTPException(status_code=503, detail="JWT service not initialized (AUTH_PROVIDER=internal required)")

    roles_list = await auth_service.get_user_roles(user["id"])
    role_names = [r["role"] for r in roles_list]
    permissions = sorted(rbac.get_effective_permissions(roles_list)) if rbac else []

    token_pair = jwt_service.create_token_pair(
        user_id=user["id"],
        email=user["email"],
        roles=role_names,
        permissions=permissions,
        display_name=user.get("display_name", ""),
    )

    # Store refresh token in DB
    if token_store:
        refresh_claims = jwt_service.decode_refresh_token(token_pair.refresh_token)
        await token_store.store_refresh_token(
            jti=refresh_claims["jti"],
            user_id=user["id"],
            family_id=refresh_claims["family_id"],
            rotation_count=0,
            token_raw=token_pair.refresh_token,
            expires_at=token_pair.refresh_expires_at,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
        )

    # Set HttpOnly cookies
    is_secure = _is_cookie_secure()
    response.set_cookie(
        key="access_token",
        value=token_pair.access_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.access_expire_seconds,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=token_pair.refresh_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.refresh_expire_seconds,
        path=_REFRESH_PATH,
    )

    return {
        "success": True,
        "user": user,
        "roles": role_names,
        "token_type": "Bearer",
        "expires_in": jwt_service.access_expire_seconds,
    }


@router.post(
    "/refresh",
    responses={
        401: {"description": "Invalid or revoked refresh token"},
        503: {"description": "Service not initialized"},
    },
)
async def refresh_token(request: Request, response: Response):
    """Refresh access token using refresh token from cookie or body."""
    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    auth_service = state.get("auth_service")
    rbac = state.get("rbac_engine")

    if not jwt_service:
        raise HTTPException(status_code=503, detail="JWT service not initialized")

    # Get refresh token from cookie or body
    refresh_token_raw = request.cookies.get("refresh_token", "")
    if not refresh_token_raw:
        try:
            body = await request.json()
            refresh_token_raw = body.get("refresh_token", "")
        except Exception:
            pass

    if not refresh_token_raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    from src.auth.providers import AuthenticationError
    try:
        claims = jwt_service.decode_refresh_token(refresh_token_raw)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.detail)

    # Validate in DB and rotate (required for security)
    if not token_store:
        raise HTTPException(status_code=503, detail="Token store not initialized")
    token_meta = await token_store.validate_and_rotate(
        jti=claims["jti"], token_raw=refresh_token_raw
    )
    if not token_meta:
        await token_store.revoke_family(claims["family_id"])
        raise HTTPException(status_code=401, detail="Refresh token revoked")

    # Get fresh user roles/permissions
    user_id = claims["sub"]
    roles_list = await auth_service.get_user_roles(user_id) if auth_service else []
    role_names = [r["role"] for r in roles_list]
    permissions = sorted(rbac.get_effective_permissions(roles_list)) if rbac else []

    user_info = await auth_service.get_user(user_id) if auth_service else {}
    email = user_info.get("email", "") if user_info else ""
    display_name = user_info.get("display_name", "") if user_info else ""

    new_pair = jwt_service.create_token_pair(
        user_id=user_id,
        email=email,
        roles=role_names,
        permissions=permissions,
        family_id=claims["family_id"],
        rotation_count=claims.get("rotation_count", 0) + 1,
        display_name=display_name,
    )

    # Store new refresh token
    if token_store:
        new_refresh_claims = jwt_service.decode_refresh_token(new_pair.refresh_token)
        await token_store.store_refresh_token(
            jti=new_refresh_claims["jti"],
            user_id=user_id,
            family_id=claims["family_id"],
            rotation_count=claims.get("rotation_count", 0) + 1,
            token_raw=new_pair.refresh_token,
            expires_at=new_pair.refresh_expires_at,
        )

    is_secure = _is_cookie_secure()
    response.set_cookie(
        key="access_token",
        value=new_pair.access_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.access_expire_seconds,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=new_pair.refresh_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.refresh_expire_seconds,
        path=_REFRESH_PATH,
    )

    return {
        "success": True,
        "token_type": "Bearer",
        "expires_in": jwt_service.access_expire_seconds,
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Revoke tokens and clear cookies."""
    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")

    refresh_token_raw = request.cookies.get("refresh_token", "")
    if refresh_token_raw and jwt_service and token_store:
        try:
            claims = jwt_service.decode_refresh_token(refresh_token_raw)
            await token_store.revoke_family(claims["family_id"])
        except Exception:
            pass  # Token may already be expired/invalid

    is_secure = _is_cookie_secure()
    response.delete_cookie("access_token", path="/", secure=is_secure, samesite="lax")
    response.delete_cookie("refresh_token", path=_REFRESH_PATH, secure=is_secure, samesite="lax")

    return {"success": True}


@router.post(
    "/register",
    responses={
        400: {"description": "Invalid input"},
        409: {"description": "User already exists"},
        503: {"description": "Auth service not initialized"},
    },
)
async def register(
    body: RegisterRequest,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))],
):
    """Register a new internal user (admin only)."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    try:
        result = await auth_service.create_user_with_password(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            department=body.department,
            organization_id=body.organization_id,
        )
        return {"success": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post(
    "/change-password",
    responses={
        400: {"description": "Invalid password"},
        503: {"description": "Auth service not initialized"},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    user: Annotated[AuthUser, Depends(get_current_user)],
):
    """Change current user's password. Revokes all existing sessions."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    success = await auth_service.change_password(
        user_id=user.sub, old_password=body.old_password, new_password=body.new_password
    )
    if not success:
        raise HTTPException(status_code=400, detail="Invalid current password")

    # Revoke all existing sessions
    token_store = _get_state().get("token_store")
    if token_store:
        await token_store.revoke_all_user_tokens(user.sub)

    return {"success": True, "message": "Password changed. Please login again."}


# =============================================================================
# Current User
# =============================================================================


@router.get("/me")
async def get_me(user: Annotated[AuthUser, Depends(get_current_user)]):
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
    _user: Annotated[AuthUser, Depends(require_permission("admin", "users"))] = None,
):
    """List all users."""
    auth_service = _get_auth_service()
    if not auth_service:
        return {"users": [], "total": 0}
    users = await auth_service.list_users(limit=limit, offset=offset)
    return {"users": users, "total": len(users)}


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    department: str | None = None
    organization_id: str | None = None
    role: str = "viewer"


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
):
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


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    department: str | None = None
    organization_id: str | None = None
    is_active: bool | None = None


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
):
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
):
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
):
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
async def list_roles(user: Annotated[AuthUser, Depends(get_current_user)]):
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
):
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
):
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
):
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
):
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
):
    """Remove a user's KB permission."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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
    user: Annotated[AuthUser, Depends(get_current_user)] = None,
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
    user: Annotated[AuthUser, Depends(get_current_user)] = None,
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
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
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


@router.post(
    "/abac/policies",
    responses={
        400: {"description": "Missing required fields"},
        503: {"description": "Auth service not initialized"},
    },
)
async def create_abac_policy(
    body: dict[str, Any],
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
):
    """Create a new ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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


@router.put(
    "/abac/policies/{policy_id}",
    responses={
        404: {"description": "Policy not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def update_abac_policy(
    policy_id: str,
    body: dict[str, Any],
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
):
    """Update an ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import select

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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


@router.delete(
    "/abac/policies/{policy_id}",
    responses={
        404: {"description": "Policy not found"},
        503: {"description": "Auth service not initialized"},
    },
)
async def delete_abac_policy(
    policy_id: str,
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
):
    """Delete an ABAC policy."""
    from src.auth.models import ABACPolicyModel
    from src.api.app import _get_state
    from sqlalchemy import delete

    auth_service = _get_state().get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

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
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
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
