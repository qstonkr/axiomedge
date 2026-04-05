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
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request

from src.auth.providers import AuthUser, AuthenticationError

logger = logging.getLogger(__name__)

# Auth can be disabled for development
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

# Anonymous user for when auth is disabled
_ANONYMOUS_USER = AuthUser(
    sub="anonymous",
    email="anonymous@local",
    display_name="Anonymous",
    provider="local",
    roles=["admin"],  # Full access when auth is off
)


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
                except Exception as e:
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

        rbac = state.get("rbac_engine")

        if rbac:
            # Build user roles from DB
            auth_service = state.get("auth_service")
            if auth_service:
                user_roles = await auth_service.get_user_roles(user.sub)
            else:
                user_roles = [{"role": r} for r in user.roles]

            highest = rbac.get_highest_role(user_roles)
            if highest in roles:
                return user

            # Check if any user role is in the required set
            for ur in user_roles:
                if ur.get("role") in roles:
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

        # 1. Check RBAC (admin bypasses everything)
        rbac = state.get("rbac_engine")
        auth_service = state.get("auth_service")

        if rbac and auth_service:
            user_roles = await auth_service.get_user_roles(user.sub)
            admin_check = rbac.check_permission(user_roles, "kb", "manage")
            if admin_check.allowed:
                return user

        # 2. Check KB-level permission
        if auth_service:
            kb_perm = await auth_service.get_kb_permission(user.sub, kb_id)
            if kb_perm:
                user_level = level_order.get(kb_perm, -1)
                required_level = level_order.get(min_level, 0)
                if user_level >= required_level:
                    return user

        # 3. Check ABAC policies
        abac = state.get("abac_engine")
        if abac:
            from src.auth.abac import ABACContext
            kb_info = {}
            kb_registry = state.get("kb_registry")
            if kb_registry:
                try:
                    kb_info = await kb_registry.get_kb(kb_id) or {}
                except Exception as e:
                    logger.debug("Failed to fetch KB info for ABAC check: %s", e)

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
                    "data_classification": kb_info.get("data_classification", "internal") if isinstance(kb_info, dict) else "internal",
                },
                action="read" if min_level == "reader" else "write",
            )
            abac_decision = abac.evaluate(ctx)
            if abac_decision.allowed:
                return user

        raise HTTPException(
            status_code=403,
            detail=f"KB '{kb_id}' requires '{min_level}' level access",
        )

    return _check
