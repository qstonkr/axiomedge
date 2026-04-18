"""FastAPI Dependencies for Auth.

Usage in route handlers:

    @router.get("/protected")
    async def protected(user: AuthUser = Depends(get_current_user)):
        ...

    @router.delete("/admin-only")
    async def admin_only(user: AuthUser = Depends(require_role("admin"))):
        ...

    @router.post("/kb/{kb_id}/ingest")
    async def ingest(
        kb_id: str,
        user: AuthUser = Depends(require_kb_access("contributor")),
    ):
        ...
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from src.auth.providers import AuthUser, AuthenticationError

if TYPE_CHECKING:
    from src.api.state import AppState

logger = logging.getLogger(__name__)

# Auth can be disabled for development
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

# Anonymous org used when AUTH_ENABLED=false (dev). Matches the organizations.id
# row seeded by migration 0003_rbac_b0 so org-scoped queries don't 404.
ANONYMOUS_ORG_ID = "default-org"

# Anonymous user for when auth is disabled
_ANONYMOUS_USER = AuthUser(
    sub="anonymous",
    email="anonymous@local",
    display_name="Anonymous",
    provider="local",
    roles=["OWNER", "admin"],  # Full access when auth is off (canonical + legacy)
    active_org_id=ANONYMOUS_ORG_ID,
)


@dataclass(frozen=True)
class OrgContext:
    """Resolved organization context for the current request.

    Returned by ``get_current_org``. Carries just enough info that handlers
    don't need to hit the DB again for tenant scoping decisions.
    """

    id: str
    user_role_in_org: str  # "OWNER" | "ADMIN" | "MEMBER" | "VIEWER" or legacy alias


def _get_app_state(request: Request) -> Any:
    """Get AppState from request without circular import."""
    return getattr(request.app.state, "_app_state", None)


async def get_current_user(request: Request) -> AuthUser:
    """Extract and verify the current user from request.

    When AUTH_ENABLED=false, returns an anonymous admin user.
    When enabled, extracts Bearer token from Authorization header
    and verifies via the configured auth provider.
    """
    if not AUTH_ENABLED:
        return _ANONYMOUS_USER

    # Extract token from header or cookie
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif auth_header.startswith("ApiKey "):
        token = auth_header[7:]
    else:
        token = request.headers.get("X-API-Key", "")

    # Fall back to HttpOnly cookie
    if not token:
        token = request.cookies.get("access_token", "")

    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    # Get provider from app state (no circular import)
    state = _get_app_state(request)
    if not state:
        raise HTTPException(status_code=503, detail="Application state not initialized")

    auth_provider = state.get("auth_provider")

    if not auth_provider:
        raise HTTPException(status_code=503, detail="Auth provider not initialized")

    try:
        user = await auth_provider.verify_token(token)

        # Sync user to local DB (fire-and-forget) — skip for internal provider
        # (internal users already exist in DB; sync would create orphan records)
        if user.provider != "internal":
            auth_service = state.get("auth_service")
            if auth_service:
                try:
                    await auth_service.sync_user_from_idp(user)
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.debug("User sync failed (non-blocking): %s", e)

        return user

    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


async def get_optional_user(request: Request) -> AuthUser | None:
    """Get current user if authenticated, None otherwise.

    For endpoints that work with or without auth (e.g., search).
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


async def get_current_org(
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> OrgContext:
    """Resolve the active organization for this request.

    Resolution priority:
      1. ``X-Organization-Id`` header (org switcher) — must be a member.
      2. ``user.active_org_id`` from the JWT — must be a current member.
      3. Single-membership auto-resolution.
      4. 403 — caller must select an org via /auth/switch-org.

    When ``AUTH_ENABLED=false`` the dev anonymous org is returned without DB
    lookup so local Streamlit + tests keep working.
    """
    if not AUTH_ENABLED:
        return OrgContext(id=ANONYMOUS_ORG_ID, user_role_in_org="OWNER")

    state = _get_app_state(request)
    if not state:
        raise HTTPException(status_code=503, detail="Application state not initialized")

    auth_service = state.get("auth_service")
    if not auth_service:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    requested = request.headers.get("X-Organization-Id") or user.active_org_id
    org_id = await auth_service.resolve_active_org_id(user.sub, requested)

    if not org_id:
        memberships = await auth_service.list_user_memberships(user.sub)
        if not memberships:
            raise HTTPException(
                status_code=403,
                detail="User has no active organization membership",
            )
        raise HTTPException(
            status_code=409,
            detail=(
                "Multiple organizations available — supply X-Organization-Id header "
                "or call /auth/switch-org to select one."
            ),
        )

    role_in_org = "MEMBER"
    for m in await auth_service.list_user_memberships(user.sub):
        if m["organization_id"] == org_id:
            role_in_org = m.get("role") or "MEMBER"
            break

    return OrgContext(id=org_id, user_role_in_org=role_in_org)


async def _check_rbac_roles(
    state: AppState, user: AuthUser, roles: tuple[str, ...],
) -> bool:
    """Check if user has any of the required roles via RBAC engine."""
    rbac = state.get("rbac_engine")
    if not rbac:
        return False

    auth_service = state.get("auth_service")
    if auth_service:
        user_roles = await auth_service.get_user_roles(user.sub)
    else:
        user_roles = [{"role": r} for r in user.roles]

    if rbac.get_highest_role(user_roles) in roles:
        return True

    return any(ur.get("role") in roles for ur in user_roles)


def require_role(*roles: str) -> Callable:
    """Dependency factory: require user to have at least one of the specified roles.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_role("admin", "kb_manager"))])
    """
    async def _check(
        request: Request,
        user: AuthUser = Depends(get_current_user),
    ) -> AuthUser:
        if not AUTH_ENABLED:
            return user

        state = _get_app_state(request)
        if not state:
            return user  # No state = allow (graceful degradation)

        if await _check_rbac_roles(state, user, roles):
            return user

        # Fallback: check IdP roles from token
        if any(r in roles for r in user.roles):
            return user

        raise HTTPException(
            status_code=403,
            detail=f"Requires one of roles: {', '.join(roles)}",
        )

    return _check


def require_permission(resource: str, action: str) -> Callable:
    """Dependency factory: require specific RBAC permission.

    Usage:
        @router.post("/glossary/import", dependencies=[Depends(require_permission("glossary", "import"))])
    """
    async def _check(
        request: Request,
        user: AuthUser = Depends(get_current_user),
    ) -> AuthUser:
        if not AUTH_ENABLED:
            return user

        state = _get_app_state(request)
        if not state:
            return user  # No state = allow (graceful degradation)

        rbac = state.get("rbac_engine")

        if not rbac:
            return user  # No RBAC engine = allow

        auth_service = state.get("auth_service")
        if auth_service:
            user_roles = await auth_service.get_user_roles(user.sub)
        else:
            user_roles = [{"role": r} for r in user.roles]

        decision = rbac.check_permission(user_roles, resource, action)
        if not decision.allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {resource}:{action} - {decision.reason}",
            )

        return user

    return _check


async def _check_rbac_admin(state: AppState, user: AuthUser) -> bool:
    """Check if user has admin-level KB management permission via RBAC."""
    rbac = state.get("rbac_engine")
    auth_service = state.get("auth_service")
    if not (rbac and auth_service):
        return False
    user_roles = await auth_service.get_user_roles(user.sub)
    admin_check = rbac.check_permission(user_roles, "kb", "manage")
    return admin_check.allowed


async def _check_kb_level_permission(
    state: AppState, user: AuthUser, kb_id: str, min_level: str,
    level_order: dict[str, int],
) -> bool:
    """Check if user has sufficient KB-level permission."""
    auth_service = state.get("auth_service")
    if not auth_service:
        return False
    kb_perm = await auth_service.get_kb_permission(user.sub, kb_id)
    if not kb_perm:
        return False
    return level_order.get(kb_perm, -1) >= level_order.get(min_level, 0)


async def _check_abac_kb_access(
    state: AppState, user: AuthUser, kb_id: str, min_level: str,
) -> bool:
    """Check KB access via ABAC policies."""
    abac = state.get("abac_engine")
    if not abac:
        return False

    from src.auth.abac import ABACContext

    kb_info = await _fetch_kb_info(state, kb_id)
    ctx = ABACContext(
        subject={
            "user_id": user.sub,
            "department": user.department,
            "organization_id": user.organization_id,
            "provider": user.provider,
            "roles": user.roles,
        },
        resource={
            "type": "kb",
            "kb_id": kb_id,
            "tier": kb_info.get("tier", "team") if isinstance(kb_info, dict) else "team",
            "organization_id": kb_info.get("organization_id") if isinstance(kb_info, dict) else None,
            "data_classification": kb_info.get("data_classification", "internal") if isinstance(kb_info, dict) else "internal",  # noqa: E501
        },
        action="read" if min_level == "reader" else "write",
    )
    return abac.evaluate(ctx).allowed


async def _fetch_kb_info(state: AppState, kb_id: str) -> dict:
    """Fetch KB info from registry, returning empty dict on failure."""
    kb_registry = state.get("kb_registry")
    if not kb_registry:
        return {}
    try:
        return await kb_registry.get_kb(kb_id) or {}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug("Failed to fetch KB info for ABAC check: %s", e)
        return {}


def require_kb_access(min_level: str = "reader") -> Callable:
    """Dependency factory: require KB-level access.

    Permission levels (ascending): reader < contributor < manager < owner

    The kb_id is extracted from path parameter.

    Usage:
        @router.post("/kb/{kb_id}/ingest")
        async def ingest(kb_id: str, user: AuthUser = Depends(require_kb_access("contributor"))):
    """
    level_order = {"reader": 0, "contributor": 1, "manager": 2, "owner": 3}

    async def _check(
        request: Request,
        user: AuthUser = Depends(get_current_user),
    ) -> AuthUser:
        if not AUTH_ENABLED:
            return user

        kb_id = request.path_params.get("kb_id")
        if not kb_id:
            return user  # No KB context

        state = _get_app_state(request)
        if not state:
            return user  # No state = allow (graceful degradation)

        if await _check_rbac_admin(state, user):
            return user
        if await _check_kb_level_permission(state, user, kb_id, min_level, level_order):
            return user
        if await _check_abac_kb_access(state, user, kb_id, min_level):
            return user

        raise HTTPException(
            status_code=403,
            detail=f"KB '{kb_id}' requires '{min_level}' level access",
        )

    return _check
