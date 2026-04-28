"""User self-service routes — /api/v1/users/me/*

Currently exposes:
- POST /users/me/consent — record server-side acceptance of chat retention
  policy. Mirrors the client-side PrivacyConsent modal so we have a legal
  trail that survives a browser localStorage clear.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.state import AppState
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users/me", tags=["Users / Self"])

# Bumped only on policy text change. Keeps old accepts auditable.
CURRENT_POLICY_VERSION = "v1"


def _get_state() -> AppState:
    from src.api.app import _get_state as _gs
    return _gs()


def _get_repo():
    state = _get_state()
    repo = state.consent_repo
    if repo is None:
        raise HTTPException(503, detail="Consent service not initialized")
    return repo


def _set_audit(request: Request, event_type: str, user: AuthUser, **details) -> None:
    request.state.audit = {
        "event_type": event_type,
        "actor": user.sub,
        "details": details,
    }


class ConsentAcceptRequest(BaseModel):
    policy_version: str = Field(default=CURRENT_POLICY_VERSION, max_length=32)


class ConsentResponse(BaseModel):
    policy_version: str
    accepted_at: str


@router.post("/consent", response_model=ConsentResponse, status_code=status.HTTP_201_CREATED)
async def accept_consent(
    body: ConsentAcceptRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
):
    repo = _get_repo()
    record = await repo.accept(
        user_id=uuid.UUID(user.sub),
        org_id=org.id,
        policy_version=body.policy_version,
        # client IP + UA strengthen the legal trail. Headers are best-effort —
        # honor `X-Forwarded-For` first hop when behind a proxy.
        ip_address=(
            (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        ),
        user_agent=request.headers.get("user-agent"),
    )
    _set_audit(
        request, "chat.privacy_consent.accepted", user,
        policy_version=record.policy_version,
        consent_id=str(record.id),
    )
    return ConsentResponse(
        policy_version=record.policy_version,
        accepted_at=record.accepted_at.isoformat(),
    )


@router.get("/consent", response_model=ConsentResponse | None)
async def get_consent(
    user: AuthUser = Depends(get_current_user),
    policy_version: str = CURRENT_POLICY_VERSION,
):
    repo = _get_repo()
    record = await repo.get_for_user(uuid.UUID(user.sub), policy_version)
    if record is None:
        return None
    return ConsentResponse(
        policy_version=record.policy_version,
        accepted_at=record.accepted_at.isoformat(),
    )
