"""Unit tests for KB registry organization_id filter (B-0 Day 3).

Hits the SQL WHERE-clause logic via a stubbed AsyncSession so the actual
PostgreSQL is not required. The point is to prove the repo refuses to leak
foreign-org rows; integration test_cross_tenant.py verifies end-to-end.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.postgres.repositories.kb_registry import KBRegistryRepository


def _stub_repo(captured_stmt: dict[str, Any]) -> KBRegistryRepository:
    """Build a repo whose session.execute records the executed Select stmt."""
    repo = KBRegistryRepository("postgresql://test")

    async def _execute(stmt: Any) -> Any:
        # SQLAlchemy compiles statements lazily; capture the where_criteria text
        # by stringifying the compiled form. We don't bind the dialect so it
        # falls back to a generic compiler — sufficient for substring assertions.
        captured_stmt["last"] = str(
            stmt.compile(compile_kwargs={"literal_binds": True})
        )
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    repo._session_maker = factory
    return repo


# organization_id appears in every SELECT column list (it's a model column),
# so we look for the WHERE-clause pattern instead: "organization_id = 'org-X'".
def _where_has_org(stmt: str, org_id: str) -> bool:
    return f"organization_id = '{org_id}'" in stmt


def _where_has_any_org_filter(stmt: str) -> bool:
    return "organization_id = '" in stmt


@pytest.mark.asyncio
async def test_get_kb_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.get_kb("kb-1", organization_id="org-A")

    assert _where_has_org(captured["last"], "org-A")


@pytest.mark.asyncio
async def test_get_kb_without_org_id_skips_filter() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.get_kb("kb-1")

    assert not _where_has_any_org_filter(captured["last"])


@pytest.mark.asyncio
async def test_list_all_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.list_all(organization_id="org-X")

    assert _where_has_org(captured["last"], "org-X")


@pytest.mark.asyncio
async def test_list_by_tier_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.list_by_tier("global", organization_id="org-Y")

    assert "tier = 'global'" in captured["last"]
    assert _where_has_org(captured["last"], "org-Y")


@pytest.mark.asyncio
async def test_list_by_status_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.list_by_status("active", organization_id="org-Z")

    assert "status = 'active'" in captured["last"]
    assert _where_has_org(captured["last"], "org-Z")


@pytest.mark.asyncio
async def test_delete_kb_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    deleted = await repo.delete_kb("kb-foreign", organization_id="org-A")

    # No matching row → returns False (does not delete cross-tenant)
    assert deleted is False
    assert _where_has_org(captured["last"], "org-A")


@pytest.mark.asyncio
async def test_update_kb_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    result = await repo.update_kb(
        "kb-foreign", {"name": "renamed"}, organization_id="org-A",
    )

    assert result is None
    assert _where_has_org(captured["last"], "org-A")


@pytest.mark.asyncio
async def test_get_kb_by_name_with_org_id_filters_query() -> None:
    captured: dict[str, Any] = {}
    repo = _stub_repo(captured)

    await repo.get_kb_by_name("MyKB", organization_id="org-W")

    assert _where_has_org(captured["last"], "org-W")
    assert "name = 'MyKB'" in captured["last"]
