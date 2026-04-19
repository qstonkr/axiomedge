"""Auth & Permission Management API Routes — facade.

User/role/KB/activity routes are in ``_auth_users.py``.
ABAC policy and system stats routes are in ``_auth_abac.py``.
Helper functions and models are in ``auth_helpers.py``.
This module keeps login/logout/register/change-password/me.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.auth.dependencies import get_current_user, require_permission
from src.auth.providers import AuthUser

# Import helpers and re-export for backward compatibility
from src.api.routes.auth_helpers import (  # noqa: F401 — re-exports
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    RegisterRequest,
    UpdateUserRequest,
    _AUTH_NOT_INIT,
    _REFRESH_PATH,
    _USER_NOT_FOUND,
    _filter_activities_by_date,
    _get_auth_service,
    _get_state,
    _is_cookie_secure,
    build_login_tokens,
    rotate_refresh_token,
    set_auth_cookies,
)

# Re-export sub-routers so route_discovery picks up all endpoints
from src.api.routes._auth_abac import router as _abac_router  # noqa: F401
from src.api.routes._auth_users import router as _users_router  # noqa: F401

# Re-export route functions for backward compatibility
from src.api.routes._auth_abac import (  # noqa: F401
    list_abac_policies,
    create_abac_policy,
    update_abac_policy,
    delete_abac_policy,
    get_system_auth_stats,
)
from src.api.routes._auth_users import (  # noqa: F401
    list_users,
    create_user,
    update_user,
    delete_user,
    get_user,
    list_roles,
    assign_role,
    revoke_role,
    list_kb_permissions,
    set_kb_permission,
    remove_kb_permission,
    get_my_activities,
    get_my_activity_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & Permissions"])

# Include sub-router routes into this router
for _sub in (_abac_router, _users_router):
    for route in _sub.routes:
        router.routes.append(route)


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
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    """Authenticate with email/password, return JWT tokens in HttpOnly cookies."""
    auth_service = _get_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    user = await auth_service.authenticate(body.email, body.password)
    if not user:
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "AUTH_FAIL: email=%s ip=%s user_agent=%s",
            body.email,
            client_ip,
            request.headers.get("user-agent", "unknown"),
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    rbac = state.get("rbac_engine")

    if not jwt_service:
        raise HTTPException(status_code=503, detail="JWT service not initialized (AUTH_PROVIDER=internal required)")

    result = await build_login_tokens(
        auth_service=auth_service,
        jwt_service=jwt_service,
        token_store=token_store,
        rbac=rbac,
        user=user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
    )

    set_auth_cookies(response, jwt_service, result["token_pair"].access_token, result["token_pair"].refresh_token)

    return {
        "success": True,
        "user": user,
        "roles": result["role_names"],
        "active_org_id": result.get("active_org_id"),
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
async def refresh_token(request: Request, response: Response) -> dict[str, Any]:
    """Refresh access token using refresh token from cookie or body."""
    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    auth_service = state.get("auth_service")
    rbac = state.get("rbac_engine")

    if not jwt_service:
        raise HTTPException(status_code=503, detail="JWT service not initialized")

    refresh_token_raw = request.cookies.get("refresh_token", "")
    if not refresh_token_raw:
        try:
            body = await request.json()
            refresh_token_raw = body.get("refresh_token", "")
        except (RuntimeError, ValueError, KeyError, UnicodeDecodeError) as e:
            logger.debug("Failed to parse refresh token from request body: %s", e)

    if not refresh_token_raw:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    from src.auth.providers import AuthenticationError
    try:
        claims = jwt_service.decode_refresh_token(refresh_token_raw)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.detail)

    if not token_store:
        raise HTTPException(status_code=503, detail="Token store not initialized")
    token_meta = await token_store.validate_and_rotate(
        jti=claims["jti"], token_raw=refresh_token_raw
    )
    if not token_meta:
        await token_store.revoke_family(claims["family_id"])
        raise HTTPException(status_code=401, detail="Refresh token revoked")

    result = await rotate_refresh_token(
        jwt_service=jwt_service,
        token_store=token_store,
        auth_service=auth_service,
        rbac=rbac,
        claims=claims,
    )

    new_pair = result["new_pair"]
    set_auth_cookies(response, jwt_service, new_pair.access_token, new_pair.refresh_token)

    return {
        "success": True,
        "token_type": "Bearer",
        "expires_in": jwt_service.access_expire_seconds,
    }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    """Revoke tokens and clear cookies."""
    state = _get_state()
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    revoked_store = state.get("revoked_token_store")

    # Revoke refresh token family (DB-backed)
    refresh_token_raw = request.cookies.get("refresh_token", "")
    if refresh_token_raw and jwt_service and token_store:
        try:
            claims = jwt_service.decode_refresh_token(refresh_token_raw)
            await token_store.revoke_family(claims["family_id"])
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    # Revoke access token jti (Redis/in-memory) so it can't be reused until natural expiry
    access_token_raw = request.cookies.get("access_token", "") or _bearer_token(request)
    if access_token_raw and jwt_service and revoked_store:
        try:
            claims = jwt_service.verify_access_token(access_token_raw)
            jti = claims.get("jti")
            exp = claims.get("exp")
            if jti and exp:
                import time
                ttl = max(int(exp - time.time()), 0)
                await revoked_store.revoke(jti, ttl)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    is_secure = _is_cookie_secure()
    response.delete_cookie("access_token", path="/", secure=is_secure, samesite="lax")
    response.delete_cookie("refresh_token", path=_REFRESH_PATH, secure=is_secure, samesite="lax")

    return {"success": True}


def _bearer_token(request: Request) -> str:
    """Extract Bearer token from Authorization header (returns empty string if absent)."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


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
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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

    token_store = _get_state().get("token_store")
    if token_store:
        await token_store.revoke_all_user_tokens(user.sub)

    return {"success": True, "message": "Password changed. Please login again."}


# =============================================================================
# Current User
# =============================================================================


@router.get("/me")
async def get_me(user: Annotated[AuthUser, Depends(get_current_user)]) -> dict[str, Any]:
    """Return current user, role/permission set, and org context.

    Frontend uses this to:
    - Render the user chip + role-aware nav
    - Populate the OrgSwitcher (memberships list)
    - Decide whether to call ``/switch-org`` (multiple memberships)
    """
    auth_service = _get_auth_service()
    roles: list[Any] = []
    memberships: list[dict[str, Any]] = []
    if auth_service:
        roles = await auth_service.get_user_roles(user.sub)
        memberships = await auth_service.list_user_memberships(user.sub)

    from src.api.app import _get_state
    rbac = _get_state().get("rbac_engine")
    permissions: list[str] = []
    if rbac:
        permissions = sorted(rbac.get_effective_permissions(roles))

    return {
        "sub": user.sub,
        "email": user.email,
        "display_name": user.display_name,
        "provider": user.provider,
        "department": user.department,
        "organization_id": user.organization_id,
        "active_org_id": user.active_org_id,
        "memberships": memberships,
        "roles": roles,
        "permissions": permissions,
    }


# =============================================================================
# Switch organization (multi-membership users)
# =============================================================================


from pydantic import BaseModel, Field  # noqa: E402 — kept after re-exports


class SwitchOrgRequest(BaseModel):
    organization_id: str = Field(..., min_length=1, max_length=100)


@router.post(
    "/switch-org",
    responses={
        403: {"description": "User is not a member of the requested organization"},
        503: {"description": "Service not initialized"},
    },
)
async def switch_org(
    body: SwitchOrgRequest,
    request: Request,
    response: Response,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict[str, Any]:
    """Issue a new token pair scoped to the requested organization.

    Validates membership, then mints a fresh access/refresh pair with the
    new ``active_org_id`` claim and rewrites the HttpOnly cookies. The old
    access JTI is added to the revocation list so it can't be reused.
    """
    state = _get_state()
    auth_service = state.get("auth_service")
    jwt_service = state.get("jwt_service")
    token_store = state.get("token_store")
    rbac = state.get("rbac_engine")
    revoked_store = state.get("revoked_token_store")

    if not (auth_service and jwt_service):
        raise HTTPException(status_code=503, detail=_AUTH_NOT_INIT)

    if not await auth_service.is_org_member(user.sub, body.organization_id):
        raise HTTPException(
            status_code=403,
            detail=f"User is not a member of organization '{body.organization_id}'",
        )

    user_dict = await auth_service.get_user(user.sub) or {
        "id": user.sub, "email": user.email, "display_name": user.display_name,
    }

    result = await build_login_tokens(
        auth_service=auth_service,
        jwt_service=jwt_service,
        token_store=token_store,
        rbac=rbac,
        user=user_dict,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        requested_org_id=body.organization_id,
    )

    # Best-effort revocation of the previous access token's JTI so the old
    # session cannot keep talking to the previous org. Failures here are
    # logged but do not block the switch.
    old_jti = user.raw_claims.get("jti") if user.raw_claims else None
    if revoked_store and old_jti:
        try:
            ttl = jwt_service.access_expire_seconds
            await revoked_store.revoke(old_jti, ttl_seconds=ttl)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Old JTI revocation skipped: %s", e)

    pair = result["token_pair"]
    set_auth_cookies(response, jwt_service, pair.access_token, pair.refresh_token)

    return {
        "success": True,
        "active_org_id": result.get("active_org_id"),
        "token_type": "Bearer",
        "expires_in": jwt_service.access_expire_seconds,
    }
