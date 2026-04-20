"""Phase 0 — data_sources 멀티테넌트 격리 검증.

0005 migration 이후 모든 ``DataSourceRepository`` 메서드가 ``organization_id``
를 강제. cross-org 접근은 None / False / rowcount=0 으로 응답해야 하며,
라우트 핸들러가 이를 404 로 매핑해 **존재 누설 (existence leak) 방지**.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    """매 호출마다 새 event loop — 다른 test 가 loop close 해도 영향 X."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_repo():
    """In-memory mock repo session — execute/commit/rollback."""
    from src.stores.postgres.repositories.data_source import DataSourceRepository

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    maker = AsyncMock(return_value=session)
    repo = DataSourceRepository.__new__(DataSourceRepository)
    repo._get_session = maker
    return repo, session


# ---------------------------------------------------------------------------
# Repository — cross-org returns None / False
# ---------------------------------------------------------------------------


class TestDataSourceRepoCrossOrg:
    def test_get_cross_org_returns_none(self):
        repo, session = _make_repo()
        # WHERE org_id = 'org-B' 이고 row 가 없음 (cross-org)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.get("ds-1", organization_id="org-B"))
        assert out is None

    def test_get_by_name_cross_org_returns_none(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.get_by_name("name", organization_id="org-B"))
        assert out is None

    def test_list_cross_org_returns_empty(self):
        repo, session = _make_repo()
        scalars = MagicMock()
        scalars.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.list(organization_id="org-B"))
        assert out == []

    def test_update_status_cross_org_returns_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.rowcount = 0  # WHERE org_id = 'org-B' AND id = ds-1 → 0 row
        session.execute = AsyncMock(return_value=result_mock)

        ok = _run(repo.update_status("ds-1", "active", organization_id="org-B"))
        assert ok is False

    def test_complete_sync_cross_org_returns_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute = AsyncMock(return_value=result_mock)

        ok = _run(repo.complete_sync("ds-1", "active", organization_id="org-B"))
        assert ok is False

    def test_delete_cross_org_returns_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute = AsyncMock(return_value=result_mock)

        ok = _run(repo.delete("ds-1", organization_id="org-B"))
        assert ok is False


# ---------------------------------------------------------------------------
# Repository — body 의 organization_id 는 caller 의 인자가 덮어씀
# ---------------------------------------------------------------------------


class TestRegisterOrgOverride:
    def test_register_overwrites_body_org(self):
        from unittest.mock import patch

        repo, session = _make_repo()
        # body 가 악의적으로 다른 org 보내도 caller 인자가 우선
        body = {
            "id": "ds-1",
            "name": "evil",
            "source_type": "git",
            "kb_id": "kb-1",
            "organization_id": "EVIL-ORG",  # 무시되어야 함
        }
        with patch(
            "src.stores.postgres.repositories.data_source.DataSourceModel",
        ):
            out = _run(repo.register(body, organization_id="legit-org"))
        assert out["organization_id"] == "legit-org"


# ---------------------------------------------------------------------------
# Route handler — cross-org access maps to 404
# ---------------------------------------------------------------------------


class TestDataSourceRouteCrossOrg:
    """라우트는 OrgContext 의 org.id 만 사용 — 어떤 source_id 든 cross-org
    이면 repo 가 None 반환 → 라우트가 404 매핑."""

    pytestmark = pytest.mark.usefixtures("bypass_route_auth")

    def _make_app(self):
        from fastapi import FastAPI
        from src.api.routes.data_sources import router

        app = FastAPI()
        app.include_router(router)
        return app

    def test_get_cross_org_returns_404(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)  # cross-org → None
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.get("/api/v1/admin/data-sources/ds-1")
                    assert resp.status_code == 404
                    # 존재 누설 X — message 도 generic
                    assert resp.json()["detail"] == "Data source not found"

            _run(_t())

    def test_delete_cross_org_returns_404(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        repo = AsyncMock()
        repo.delete = AsyncMock(return_value=False)  # cross-org → False
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.delete("/api/v1/admin/data-sources/ds-1")
                    assert resp.status_code == 404

            _run(_t())

    def test_list_cross_org_returns_empty(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        repo = AsyncMock()
        repo.list = AsyncMock(return_value=[])  # caller's org has none
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.get("/api/v1/admin/data-sources")
                    assert resp.status_code == 200
                    assert resp.json()["sources"] == []
                    assert resp.json()["total"] == 0

            _run(_t())
