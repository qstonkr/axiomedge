"""Unit tests for src/auth/dependencies.py:get_current_org.

Covers single-org auto-resolve, multi-org 409, no-membership 403,
header-based switching, and AUTH_ENABLED=false bypass.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.auth import dependencies as deps
from src.auth.dependencies import (
    ANONYMOUS_ORG_ID,
    OrgContext,
    get_current_org,
)
from src.auth.providers import AuthUser


def _make_request(headers: dict[str, str] | None = None, state: dict[str, Any] | None = None) -> Any:
    """Minimal Request stand-in — only the bits get_current_org touches."""
    request = MagicMock()
    request.headers = headers or {}
    app_state = MagicMock()
    app_state._app_state = state if state is not None else {}
    request.app.state = app_state
    return request


def _make_auth_user(sub: str = "u1", active_org_id: str | None = None) -> AuthUser:
    return AuthUser(
        sub=sub,
        email=f"{sub}@test",
        display_name=sub,
        provider="internal",
        roles=["MEMBER"],
        active_org_id=active_org_id,
    )


@pytest.mark.asyncio
async def test_auth_disabled_returns_anonymous_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AUTH_ENABLED is false the dev anonymous org bypasses DB lookups."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", False)

    request = _make_request()
    user = _make_auth_user()

    org = await get_current_org(request, user)

    assert isinstance(org, OrgContext)
    assert org.id == ANONYMOUS_ORG_ID
    assert org.user_role_in_org == "OWNER"


@pytest.mark.asyncio
async def test_single_membership_auto_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value="org-1")
    auth_service.list_user_memberships = AsyncMock(
        return_value=[{"organization_id": "org-1", "role": "MEMBER"}]
    )

    request = _make_request(state={"auth_service": auth_service})
    user = _make_auth_user()

    org = await get_current_org(request, user)

    assert org.id == "org-1"
    assert org.user_role_in_org == "MEMBER"


@pytest.mark.asyncio
async def test_jwt_active_org_used_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """User has multiple orgs; JWT claim picks one."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value="org-2")
    auth_service.list_user_memberships = AsyncMock(
        return_value=[
            {"organization_id": "org-1", "role": "MEMBER"},
            {"organization_id": "org-2", "role": "ADMIN"},
        ]
    )

    request = _make_request(state={"auth_service": auth_service})
    user = _make_auth_user(active_org_id="org-2")

    org = await get_current_org(request, user)

    assert org.id == "org-2"
    assert org.user_role_in_org == "ADMIN"
    auth_service.resolve_active_org_id.assert_awaited_once_with("u1", "org-2")


@pytest.mark.asyncio
async def test_header_overrides_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Organization-Id header is the org-switcher mechanism."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value="org-3")
    auth_service.list_user_memberships = AsyncMock(
        return_value=[
            {"organization_id": "org-1", "role": "MEMBER"},
            {"organization_id": "org-3", "role": "OWNER"},
        ]
    )

    request = _make_request(
        headers={"X-Organization-Id": "org-3"},
        state={"auth_service": auth_service},
    )
    user = _make_auth_user(active_org_id="org-1")

    org = await get_current_org(request, user)

    assert org.id == "org-3"
    assert org.user_role_in_org == "OWNER"
    auth_service.resolve_active_org_id.assert_awaited_once_with("u1", "org-3")


@pytest.mark.asyncio
async def test_no_membership_raises_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value=None)
    auth_service.list_user_memberships = AsyncMock(return_value=[])

    request = _make_request(state={"auth_service": auth_service})
    user = _make_auth_user()

    with pytest.raises(HTTPException) as exc:
        await get_current_org(request, user)

    assert exc.value.status_code == 403
    assert "no active organization membership" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_multi_membership_without_selection_raises_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value=None)  # ambiguous
    auth_service.list_user_memberships = AsyncMock(
        return_value=[
            {"organization_id": "org-1", "role": "MEMBER"},
            {"organization_id": "org-2", "role": "ADMIN"},
        ]
    )

    request = _make_request(state={"auth_service": auth_service})
    user = _make_auth_user()  # no active_org_id

    with pytest.raises(HTTPException) as exc:
        await get_current_org(request, user)

    assert exc.value.status_code == 409
    assert "X-Organization-Id" in exc.value.detail


@pytest.mark.asyncio
async def test_invalid_requested_org_falls_back_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header references an org the user is not a member of → resolver returns None → 409."""
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    auth_service = MagicMock()
    auth_service.resolve_active_org_id = AsyncMock(return_value=None)
    auth_service.list_user_memberships = AsyncMock(
        return_value=[
            {"organization_id": "org-1", "role": "MEMBER"},
            {"organization_id": "org-2", "role": "MEMBER"},
        ]
    )

    request = _make_request(
        headers={"X-Organization-Id": "org-99"},  # not a member
        state={"auth_service": auth_service},
    )
    user = _make_auth_user()

    with pytest.raises(HTTPException) as exc:
        await get_current_org(request, user)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_state_not_initialized_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    request = _make_request(state=None)
    user = _make_auth_user()

    with pytest.raises(HTTPException) as exc:
        await get_current_org(request, user)

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_auth_service_not_initialized_raises_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "AUTH_ENABLED", True)

    request = _make_request(state={})  # no auth_service key
    user = _make_auth_user()

    with pytest.raises(HTTPException) as exc:
        await get_current_org(request, user)

    assert exc.value.status_code == 503
