"""Unit tests for src/auth/org_service.py — resolve_active_org_id logic.

Covers the priority chain (requested → single → ambiguous → none) plus the
idempotent ``add_member`` behavior. Membership lookups go through a mocked
session factory so no DB is touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.auth.org_service import DEFAULT_ORG_ID, OrgService


def _stub_session_factory(memberships: list[dict] | None = None) -> Any:
    """Build a MagicMock session factory whose .execute returns canned rows."""
    membership_rows = memberships or []

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    factory._rows = membership_rows  # for tests to inspect
    factory._session = session
    return factory


def _scalars_returning(items: list[Any]) -> Any:
    """Make a scalars() result that yields the given items via .all()."""
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=items)
    return scalars


def _scalar_one_or_none(value: Any) -> Any:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _first_returning(value: Any) -> Any:
    result = MagicMock()
    result.first = MagicMock(return_value=value)
    return result


def _scalars_result(items: list[Any]) -> Any:
    result = MagicMock()
    result.scalars = MagicMock(return_value=_scalars_returning(items))
    return result


def _membership_obj(org_id: str, role: str = "MEMBER", status: str = "active") -> Any:
    obj = MagicMock()
    obj.organization_id = org_id
    obj.role = role
    obj.status = status
    obj.joined_at = None
    return obj


@pytest.mark.asyncio
async def test_resolve_returns_requested_when_member() -> None:
    factory = _stub_session_factory()
    factory._session.execute = AsyncMock(
        return_value=_first_returning(("membership-id",)),
    )
    svc = OrgService(factory)

    result = await svc.resolve_active_org_id("user-1", "org-target")

    assert result == "org-target"


@pytest.mark.asyncio
async def test_resolve_rejects_requested_when_not_member() -> None:
    factory = _stub_session_factory()
    factory._session.execute = AsyncMock(return_value=_first_returning(None))
    svc = OrgService(factory)

    result = await svc.resolve_active_org_id("user-1", "stranger-org")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_auto_picks_single_membership() -> None:
    factory = _stub_session_factory()
    # First (and only) call returns one membership
    factory._session.execute = AsyncMock(
        return_value=_scalars_result([_membership_obj("only-org")]),
    )
    svc = OrgService(factory)

    result = await svc.resolve_active_org_id("user-1")

    assert result == "only-org"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_multi_membership() -> None:
    factory = _stub_session_factory()
    factory._session.execute = AsyncMock(
        return_value=_scalars_result([
            _membership_obj("org-1"),
            _membership_obj("org-2"),
        ]),
    )
    svc = OrgService(factory)

    result = await svc.resolve_active_org_id("user-1")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_zero_memberships() -> None:
    factory = _stub_session_factory()
    factory._session.execute = AsyncMock(return_value=_scalars_result([]))
    svc = OrgService(factory)

    result = await svc.resolve_active_org_id("user-1")

    assert result is None


@pytest.mark.asyncio
async def test_add_member_idempotent_when_already_present() -> None:
    factory = _stub_session_factory()
    existing = MagicMock()
    existing.id = "existing-id"
    existing.user_id = "user-1"
    existing.organization_id = DEFAULT_ORG_ID
    existing.role = "MEMBER"
    existing.status = "active"

    factory._session.execute = AsyncMock(return_value=_scalar_one_or_none(existing))
    svc = OrgService(factory)

    result = await svc.add_member("user-1", DEFAULT_ORG_ID)

    assert result["id"] == "existing-id"
    factory._session.add.assert_not_called()


@pytest.mark.asyncio
async def test_add_member_creates_when_missing() -> None:
    factory = _stub_session_factory()
    factory._session.execute = AsyncMock(return_value=_scalar_one_or_none(None))
    svc = OrgService(factory)

    result = await svc.add_member("user-2", DEFAULT_ORG_ID, role="ADMIN")

    assert result["organization_id"] == DEFAULT_ORG_ID
    assert result["role"] == "ADMIN"
    factory._session.add.assert_called_once()
    factory._session.commit.assert_awaited()
