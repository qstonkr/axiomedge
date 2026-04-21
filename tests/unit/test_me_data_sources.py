"""User self-service data sources — me_data_sources 라우트 검증.

핵심 보장:
1. cross-user 접근 → 404 (존재 누설 X)
2. owner_user_id 가 caller 의 user.sub 로 강제 (body 가 다른 값 보내도 무시)
3. SecretBox path 가 user-scoped (``user/{uid}/data-source/...``)
4. shared-token connector (Slack) 는 secret_token 무시 (admin token 사용)
5. 미지원 connector (예: ``custom_xxx``) 는 400
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# secret_paths — 격리 helper 단위
# ---------------------------------------------------------------------------


class TestSecretPaths:
    def test_admin_path_is_org_scoped(self):
        from src.auth.secret_paths import data_source_path
        path = data_source_path(
            organization_id="org-1", source_id="ds-1", owner_user_id=None,
        )
        assert path == "org/org-1/data-source/ds-1"

    def test_user_path_is_user_scoped(self):
        from src.auth.secret_paths import data_source_path
        path = data_source_path(
            organization_id="org-1", source_id="ds-1", owner_user_id="u-42",
        )
        assert path == "user/u-42/data-source/ds-1"

    def test_shared_token_path(self):
        from src.auth.secret_paths import shared_token_path
        assert (
            shared_token_path("org-1", "slack")
            == "org/org-1/connector-shared/slack"
        )

    def test_parse_path_scope(self):
        from src.auth.secret_paths import parse_path_scope
        assert parse_path_scope("org/o1/data-source/ds1") == "org"
        assert parse_path_scope("user/u1/data-source/ds1") == "user"
        assert parse_path_scope("org/o1/connector-shared/slack") == "shared"
        assert parse_path_scope("invalid") == "unknown"


class TestCatalogMeta:
    def test_per_user_connectors(self):
        from src.connectors.catalog_meta import (
            is_per_user_token_connector,
            is_shared_token_connector,
        )
        assert is_per_user_token_connector("notion")
        assert is_per_user_token_connector("git")
        assert is_per_user_token_connector("confluence")
        assert not is_per_user_token_connector("slack")

    def test_shared_connectors(self):
        from src.connectors.catalog_meta import is_shared_token_connector
        assert is_shared_token_connector("slack")
        assert not is_shared_token_connector("notion")

    def test_user_self_service_includes_all(self):
        from src.connectors.catalog_meta import is_user_self_service
        for ct in ("notion", "git", "confluence", "slack",
                   "file_upload", "crawl_result"):
            assert is_user_self_service(ct), f"{ct} should be self-service"
        assert not is_user_self_service("custom_xxx")


# ---------------------------------------------------------------------------
# Repository — owner_user_id filter (cross-user 격리)
# ---------------------------------------------------------------------------


def _make_repo():
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


class TestRepoOwnerUserFilter:
    def test_get_for_user_cross_user_returns_none(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.get_for_user(
            "ds-1", organization_id="org-1", owner_user_id="u-OTHER",
        ))
        assert out is None

    def test_delete_for_user_cross_user_returns_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.rowcount = 0
        session.execute = AsyncMock(return_value=result_mock)

        ok = _run(repo.delete_for_user(
            "ds-1", organization_id="org-1", owner_user_id="u-OTHER",
        ))
        assert ok is False

    def test_register_with_owner_user_id_persists(self):
        from unittest.mock import patch

        repo, session = _make_repo()
        body = {
            "id": "ds-1",
            "name": "my-notion",
            "source_type": "notion",
            "kb_id": "kb-personal",
        }
        with patch(
            "src.stores.postgres.repositories.data_source.DataSourceModel",
        ) as mock_model:
            out = _run(repo.register(
                body, organization_id="org-1", owner_user_id="u-42",
            ))
        # Model 생성 시 owner_user_id 가 들어가야 함
        call_kwargs = mock_model.call_args.kwargs
        assert call_kwargs["owner_user_id"] == "u-42"
        assert out["owner_user_id"] == "u-42"


# ---------------------------------------------------------------------------
# 라우트 — POST 가 owner_user_id 강제 + KB owner 체크
# ---------------------------------------------------------------------------


class TestUserDataSourceRoutes:
    pytestmark = pytest.mark.usefixtures("bypass_route_auth")

    def _make_app(self):
        from fastapi import FastAPI
        from src.api.routes.me_data_sources import router

        app = FastAPI()
        app.include_router(router)
        return app

    def test_create_returns_404_when_kb_not_owned(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        # kb_registry.get_kb 가 None 반환 (cross-user)
        kb_registry = AsyncMock()
        kb_registry.get_kb = AsyncMock(return_value=None)
        state = {
            "kb_registry": kb_registry,
            "data_source_repo": AsyncMock(),
        }

        with patch("src.api.routes.me_data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/me/knowledge/kb-other/data-sources",
                        json={
                            "name": "x", "source_type": "notion",
                            "crawl_config": {"root_page_id": "abc"},
                        },
                    )
                    assert resp.status_code == 404
                    assert "Personal KB not found" in resp.json()["detail"]

            _run(_t())

    def test_create_rejects_unsupported_connector(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        kb_registry = AsyncMock()
        kb_registry.get_kb = AsyncMock(return_value={"kb_id": "kb-1"})
        state = {
            "kb_registry": kb_registry,
            "data_source_repo": AsyncMock(),
        }

        with patch("src.api.routes.me_data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/me/knowledge/kb-1/data-sources",
                        json={
                            "name": "x", "source_type": "custom_unknown",
                        },
                    )
                    assert resp.status_code == 400
                    assert "self-service" in resp.json()["detail"]

            _run(_t())

    def test_delete_cross_user_returns_404(self):
        from unittest.mock import patch
        from httpx import ASGITransport, AsyncClient

        kb_registry = AsyncMock()
        kb_registry.get_kb = AsyncMock(return_value={"kb_id": "kb-1"})
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=None)  # cross-user → None
        state = {"kb_registry": kb_registry, "data_source_repo": repo}

        with patch("src.api.routes.me_data_sources._get_state", return_value=state):
            app = self._make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.delete(
                        "/api/v1/me/knowledge/kb-1/data-sources/ds-OTHER",
                    )
                    assert resp.status_code == 404

            _run(_t())
