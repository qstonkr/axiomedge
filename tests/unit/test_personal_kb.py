"""Personal KB unit tests (B-1 Day 1).

Coverage:
* ``KBRegistryRepository`` honours the new ``owner_id`` filter (WHERE clause).
* ``_drop_foreign_personal_kbs`` strips other users' personal KBs from a
  collection list.
* ``KBListerTool`` (agentic) drops foreign personal KBs from its agent-facing
  list once ``current_user_id`` is in state.
* ``POST /api/v1/kb/create`` rejects non-personal tiers and enforces the
  per-user soft cap.
* ``permission_matrix`` lets a MEMBER hit ``POST /api/v1/kb/create`` (it
  resolves to ``document:write``, which MEMBER has).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# =============================================================================
# 1. Repository owner_id filter (WHERE clause)
# =============================================================================


def _stub_repo(captured: dict[str, Any]):
    from src.stores.postgres.repositories.kb_registry import KBRegistryRepository

    repo = KBRegistryRepository("postgresql://test")

    async def _execute(stmt: Any) -> Any:
        captured["last"] = str(
            stmt.compile(compile_kwargs={"literal_binds": True})
        )
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    repo._session_maker = factory
    return repo


@pytest.mark.asyncio
async def test_get_kb_with_owner_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.get_kb("kb-1", owner_id="user-A")

    assert "owner_id = 'user-A'" in captured["last"]


@pytest.mark.asyncio
async def test_list_by_tier_with_owner_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.list_by_tier("personal", organization_id="org-1", owner_id="user-A")

    assert "tier = 'personal'" in captured["last"]
    assert "organization_id = 'org-1'" in captured["last"]
    assert "owner_id = 'user-A'" in captured["last"]


@pytest.mark.asyncio
async def test_list_all_without_owner_skips_filter() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.list_all(organization_id="org-1")

    assert "owner_id = '" not in captured["last"]


# =============================================================================
# 2. _drop_foreign_personal_kbs — search-side isolation
# =============================================================================


@pytest.mark.asyncio
async def test_drop_foreign_personal_kbs_strips_other_users() -> None:
    from src.api.routes._search_preprocess import _drop_foreign_personal_kbs

    kb_registry = AsyncMock()

    async def _get_kb(kb_id):
        return {
            "kb-team": {"id": "kb-team", "tier": "team", "owner_id": "user-B"},
            "pkb-mine": {"id": "pkb-mine", "tier": "personal", "owner_id": "user-A"},
            "pkb-foreign": {"id": "pkb-foreign", "tier": "personal", "owner_id": "user-B"},
        }.get(kb_id)

    kb_registry.get_kb = AsyncMock(side_effect=_get_kb)

    state = {"kb_registry": kb_registry}
    collections = ["kb-team", "pkb-mine", "pkb-foreign"]

    result = await _drop_foreign_personal_kbs(collections, state, current_user_id="user-A")

    assert result == ["kb-team", "pkb-mine"]


@pytest.mark.asyncio
async def test_drop_foreign_personal_kbs_no_user_passes_through() -> None:
    """Anonymous dev mode (current_user_id is None) must not filter anything."""
    from src.api.routes._search_preprocess import _drop_foreign_personal_kbs

    state = {"kb_registry": AsyncMock()}
    collections = ["pkb-someone"]

    result = await _drop_foreign_personal_kbs(collections, state, current_user_id=None)

    assert result == collections


# =============================================================================
# 3. Agentic kb_lister filters foreign personal KBs
# =============================================================================


@pytest.mark.asyncio
async def test_kb_lister_drops_foreign_personal_kbs() -> None:
    from src.agentic.tools.kb_lister import KBListerTool

    registry = AsyncMock()
    registry.list_all = AsyncMock(return_value=[
        {"id": "kb-team", "tier": "team", "status": "active", "owner_id": "user-B"},
        {"id": "pkb-mine", "tier": "personal", "status": "active", "owner_id": "user-A"},
        {"id": "pkb-foreign", "tier": "personal", "status": "active", "owner_id": "user-B"},
    ])

    state = {
        "kb_registry": registry,
        "organization_id": "org-1",
        "current_user_id": "user-A",
    }

    tool = KBListerTool()
    result = await tool.execute({}, state)

    assert result.success
    visible_ids = {k["kb_id"] for k in result.data}
    assert visible_ids == {"kb-team", "pkb-mine"}


# =============================================================================
# 4. POST /api/v1/kb/create — tier + cap validation
# =============================================================================


def _make_create_kb_call(
    *, tier: str, existing_count: int, user_sub: str = "user-A",
):
    """Helper — invoke kb.create_kb with stubbed registry/qdrant/state."""
    from src.api.routes import kb as kb_module
    from src.api.routes.kb import KBCreateRequest, create_kb
    from src.auth.dependencies import OrgContext
    from src.auth.providers import AuthUser
    from unittest.mock import patch

    qdrant = AsyncMock()
    qdrant.ensure_collection = AsyncMock()

    registry = AsyncMock()
    registry.list_by_tier = AsyncMock(
        return_value=[{"kb_id": f"existing-{i}"} for i in range(existing_count)],
    )
    registry.create_kb = AsyncMock()

    state = {"qdrant_collections": qdrant, "kb_registry": registry}

    user = AuthUser(
        sub=user_sub, email=f"{user_sub}@test", display_name=user_sub,
        provider="internal", roles=["MEMBER"], active_org_id="org-1",
    )
    org = OrgContext(id="org-1", user_role_in_org="MEMBER")
    request = KBCreateRequest(
        kb_id=f"pkb-{user_sub}-new", name="New", description="", tier=tier,
    )

    async def _go():
        with patch.object(kb_module, "_get_state", return_value=state):
            return await create_kb(request, user=user, org=org)

    return _go, registry


@pytest.mark.asyncio
async def test_create_kb_rejects_non_personal_tier() -> None:
    from fastapi import HTTPException

    go, _ = _make_create_kb_call(tier="team", existing_count=0)

    with pytest.raises(HTTPException) as exc:
        await go()

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_kb_personal_under_cap_succeeds() -> None:
    go, registry = _make_create_kb_call(tier="personal", existing_count=3)

    result = await go()

    assert result["success"] is True
    assert result["tier"] == "personal"
    assert result["owner_id"] == "user-A"
    registry.create_kb.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_kb_personal_at_cap_returns_409() -> None:
    from fastapi import HTTPException
    from src.api.routes.kb import PERSONAL_KB_LIMIT_PER_USER

    go, _ = _make_create_kb_call(
        tier="personal", existing_count=PERSONAL_KB_LIMIT_PER_USER,
    )

    with pytest.raises(HTTPException) as exc:
        await go()

    assert exc.value.status_code == 409


# =============================================================================
# 5. permission_matrix — MEMBER can hit /api/v1/kb/create
# =============================================================================


def test_permission_matrix_allows_member_to_create_personal_kb() -> None:
    """The route maps to document:write (which MEMBER has). Tier policing
    happens inside the handler — the matrix should not block legitimate
    personal-KB creation."""
    from src.auth.permission_matrix import find_required_permission
    from src.auth.rbac import RBACEngine

    perm = find_required_permission("POST", "/api/v1/kb/create")
    assert perm == ("document", "write")

    engine = RBACEngine()
    assert engine.check_permission([{"role": "MEMBER"}], *perm).allowed
    # VIEWER must still be denied (no document:write).
    assert not engine.check_permission([{"role": "VIEWER"}], *perm).allowed


# =============================================================================
# 6. _ensure_personal_kb idempotency contract (without DB)
# =============================================================================


@pytest.mark.asyncio
async def test_ensure_personal_kb_skips_when_no_org() -> None:
    """No org → nothing to scope the KB to → silently bail."""
    from src.auth.user_crud import _ensure_personal_kb

    # If it tried to touch the DB this would explode; the early-return is
    # what we're verifying.
    await _ensure_personal_kb(user_id="user-X", organization_id=None, display_name="X")
