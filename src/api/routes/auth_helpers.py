"""Auth route helpers — constants, models, and shared logic.

Extracted from auth.py to keep route handlers thin.
All public names are re-exported from auth.py for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_AUTH_NOT_INIT = "Auth service not initialized"
_REFRESH_PATH = "/api/v1/auth/refresh"
_USER_NOT_FOUND = "User not found"

# ── Service accessors ────────────────────────────────────────────────────────


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


# ── Request models ───────────────────────────────────────────────────────────


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


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    department: str | None = None
    organization_id: str | None = None
    role: str = "viewer"


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    department: str | None = None
    organization_id: str | None = None
    is_active: bool | None = None


# ── Shared helpers ───────────────────────────────────────────────────────────


def _filter_activities_by_date(
    activities: list[dict], date_from: str | None, date_to: str | None,
) -> list[dict]:
    """Filter activity records by date range."""
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
    return filtered


async def build_login_tokens(
    auth_service: Any,
    jwt_service: Any,
    token_store: Any,
    rbac: Any,
    user: dict[str, Any],
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Build JWT token pair and store refresh token for login.

    Returns dict with token_pair, role_names, and permissions.
    """
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

    if token_store:
        refresh_claims = jwt_service.decode_refresh_token(token_pair.refresh_token)
        await token_store.store_refresh_token(
            jti=refresh_claims["jti"],
            user_id=user["id"],
            family_id=refresh_claims["family_id"],
            rotation_count=0,
            token_raw=token_pair.refresh_token,
            expires_at=token_pair.refresh_expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    return {
        "token_pair": token_pair,
        "role_names": role_names,
        "permissions": permissions,
    }


async def rotate_refresh_token(
    jwt_service: Any,
    token_store: Any,
    auth_service: Any,
    rbac: Any,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Rotate a refresh token — fetch fresh roles/permissions and create new pair.

    Returns dict with new_pair, role_names, and user info.
    """
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

    return {"new_pair": new_pair}


def set_auth_cookies(
    response: Any,
    jwt_service: Any,
    access_token: str,
    refresh_token: str,
) -> None:
    """Set HttpOnly auth cookies on a response."""
    is_secure = _is_cookie_secure()
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.access_expire_seconds,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=jwt_service.refresh_expire_seconds,
        path=_REFRESH_PATH,
    )
