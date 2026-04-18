"""Auth ABAC policy + system stats route handlers — extracted from auth.py.

Contains: ABAC policy CRUD and system auth statistics endpoints.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from src.auth.dependencies import require_permission
from src.auth.providers import AuthUser
from src.api.routes.auth_helpers import _AUTH_NOT_INIT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & Permissions"])


# =============================================================================
# ABAC Policy Management (admin)
# =============================================================================


@router.get("/abac/policies")
async def list_abac_policies(
    _user: Annotated[AuthUser, Depends(require_permission("admin", "system"))],
) -> dict[str, list[dict[str, Any]]]:
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
) -> dict[str, Any]:
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
) -> dict[str, bool]:
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

        for field in ("name", "description", "resource_type", "action", "conditions", "effect", "priority", "is_active"):  # noqa: E501
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
) -> dict[str, bool]:
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
) -> dict[str, Any]:
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
