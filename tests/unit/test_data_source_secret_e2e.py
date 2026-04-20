"""Phase 2 — data_sources secret 분리 e2e 검증.

핵심 보장:
1. POST /admin/data-sources body 의 secret_token → SecretBox 에 저장,
   DB 의 has_secret=True / secret_path 채워짐.
2. crawl_config 안의 평문 token (legacy) 는 SecretBox 로 redirect 후
   crawl_config 에서 strip — DB 평문 저장 X.
3. GET 응답에는 plain token 절대 포함 X (mask 됨), has_secret bool 만.
4. PUT 의 secret_token=null → SecretBox.delete + has_secret=false.
5. DELETE → cascade SecretBox 정리.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.usefixtures("bypass_route_auth")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def configured_secret_box(monkeypatch: pytest.MonkeyPatch):
    """SECRET_BOX_KEY 설정 + box cache reset → LocalFernetBox 활성화."""
    monkeypatch.setenv("SECRET_BOX_BACKEND", "fernet")
    monkeypatch.setenv("SECRET_BOX_KEY", Fernet.generate_key().decode())
    from src.auth.secret_box import reset_secret_box
    from src.config.settings import reset_settings
    reset_settings()
    reset_secret_box()
    yield
    reset_secret_box()
    reset_settings()


def _make_app() -> FastAPI:
    from src.api.routes.data_sources import router
    app = FastAPI()
    app.include_router(router)
    return app


def _mock_repo() -> AsyncMock:
    """In-memory data_source repo with set_secret_path tracking."""
    repo = AsyncMock()
    state = {"sources": {}}

    async def register(data, organization_id):
        sid = data["id"]
        state["sources"][sid] = {**data, "organization_id": organization_id, "has_secret": False, "secret_path": None}
        return state["sources"][sid]

    async def get(source_id, organization_id):
        s = state["sources"].get(source_id)
        if s and s["organization_id"] == organization_id:
            return dict(s)
        return None

    async def set_secret_path(source_id, organization_id, secret_path):
        s = state["sources"].get(source_id)
        if s and s["organization_id"] == organization_id:
            s["secret_path"] = secret_path
            s["has_secret"] = secret_path is not None
            return True
        return False

    async def update_status(source_id, status, organization_id, error_message=None):
        s = state["sources"].get(source_id)
        if s and s["organization_id"] == organization_id:
            s["status"] = status
            return True
        return False

    async def delete(source_id, organization_id):
        s = state["sources"].get(source_id)
        if s and s["organization_id"] == organization_id:
            del state["sources"][source_id]
            return True
        return False

    async def list_(organization_id, source_type=None, status=None):
        return [dict(s) for s in state["sources"].values() if s["organization_id"] == organization_id]

    repo.register = register
    repo.get = get
    repo.set_secret_path = set_secret_path
    repo.update_status = update_status
    repo.delete = delete
    repo.list = list_
    repo._state = state
    return repo


# ---------------------------------------------------------------------------
# Round-trip: POST → SecretBox put → GET 마스킹 확인
# ---------------------------------------------------------------------------


class TestSecretRoundTrip:
    def test_post_with_secret_token_stores_in_secret_box(
        self, configured_secret_box,
    ):
        repo = _mock_repo()
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = _make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    # 1. POST with secret_token
                    resp = await ac.post(
                        "/api/v1/admin/data-sources",
                        json={
                            "id": "ds-1",
                            "name": "git-private",
                            "source_type": "git",
                            "kb_id": "kb-1",
                            "crawl_config": {"repo_url": "https://github.com/org/private"},
                            "secret_token": "ghp_top-secret-token-xxx",
                        },
                    )
                    assert resp.status_code == 200
                    assert resp.json()["source_id"] == "ds-1"

                    # 2. SecretBox 에 저장됐는지 확인 — 직접 path 로 read
                    from src.auth.secret_box import get_secret_box
                    box = get_secret_box()
                    secret = await box.get("org/default-org/data-source/ds-1")
                    assert secret == "ghp_top-secret-token-xxx"

                    # 3. DB row 의 has_secret=True, secret_path 채워짐
                    saved = repo._state["sources"]["ds-1"]
                    assert saved["has_secret"] is True
                    assert saved["secret_path"] == "org/default-org/data-source/ds-1"

                    # 4. GET 응답에 plain token 없음
                    resp = await ac.get("/api/v1/admin/data-sources/ds-1")
                    body = resp.json()
                    assert body["has_secret"] is True
                    assert "secret_path" not in body  # 노출 X
                    # crawl_config 에 token 흔적 없음
                    cfg_str = str(body.get("crawl_config", {}))
                    assert "ghp_top-secret-token-xxx" not in cfg_str

            _run(_t())

    def test_legacy_plain_auth_token_in_crawl_config_redirected(
        self, configured_secret_box,
    ):
        """옛 사용자가 crawl_config 안에 auth_token 박았을 때 → SecretBox 로 자동 이동."""
        repo = _mock_repo()
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = _make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/admin/data-sources",
                        json={
                            "id": "ds-2",
                            "name": "legacy",
                            "source_type": "git",
                            "kb_id": "kb-1",
                            "crawl_config": {
                                "repo_url": "https://github.com/x/y",
                                "auth_token": "ghp_legacy",  # 평문 — redirect 되어야 함
                            },
                        },
                    )
                    assert resp.status_code == 200

                    # crawl_config 에서 auth_token strip 됐는지
                    saved = repo._state["sources"]["ds-2"]
                    cfg = saved["crawl_config"]
                    assert "auth_token" not in cfg
                    assert cfg["repo_url"] == "https://github.com/x/y"

                    # SecretBox 로 이동
                    from src.auth.secret_box import get_secret_box
                    box = get_secret_box()
                    assert (
                        await box.get("org/default-org/data-source/ds-2")
                        == "ghp_legacy"
                    )

            _run(_t())


# ---------------------------------------------------------------------------
# UPDATE — secret_token 처리 분기
# ---------------------------------------------------------------------------


class TestUpdateSecretBranches:
    def test_explicit_null_deletes_secret(self, configured_secret_box):
        repo = _mock_repo()
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = _make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    # 1. 등록 + token
                    await ac.post(
                        "/api/v1/admin/data-sources",
                        json={
                            "id": "ds-3", "name": "x", "source_type": "git",
                            "kb_id": "kb", "secret_token": "t1",
                        },
                    )
                    assert repo._state["sources"]["ds-3"]["has_secret"]

                    # 2. PUT with secret_token=null → SecretBox 에서 삭제
                    resp = await ac.put(
                        "/api/v1/admin/data-sources/ds-3",
                        json={"secret_token": None},
                    )
                    assert resp.status_code == 200
                    assert repo._state["sources"]["ds-3"]["has_secret"] is False

                    from src.auth.secret_box import get_secret_box
                    box = get_secret_box()
                    assert (
                        await box.get("org/default-org/data-source/ds-3") is None
                    )

            _run(_t())

    def test_omitted_secret_keeps_existing(self, configured_secret_box):
        repo = _mock_repo()
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = _make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    await ac.post(
                        "/api/v1/admin/data-sources",
                        json={
                            "id": "ds-4", "name": "x", "source_type": "git",
                            "kb_id": "kb", "secret_token": "keep-me",
                        },
                    )
                    # PUT without secret_token key — 옛 token 유지
                    await ac.put(
                        "/api/v1/admin/data-sources/ds-4",
                        json={"status": "active"},
                    )
                    assert repo._state["sources"]["ds-4"]["has_secret"]

                    from src.auth.secret_box import get_secret_box
                    box = get_secret_box()
                    assert (
                        await box.get("org/default-org/data-source/ds-4")
                        == "keep-me"
                    )

            _run(_t())


# ---------------------------------------------------------------------------
# DELETE — cascade SecretBox cleanup
# ---------------------------------------------------------------------------


class TestDeleteCascadesSecret:
    def test_delete_clears_secret_box(self, configured_secret_box):
        repo = _mock_repo()
        state = {"data_source_repo": repo}

        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = _make_app()

            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    await ac.post(
                        "/api/v1/admin/data-sources",
                        json={
                            "id": "ds-5", "name": "x", "source_type": "git",
                            "kb_id": "kb", "secret_token": "to-delete",
                        },
                    )
                    resp = await ac.delete("/api/v1/admin/data-sources/ds-5")
                    assert resp.status_code == 200

                    # SecretBox 에서도 사라짐
                    from src.auth.secret_box import get_secret_box
                    box = get_secret_box()
                    assert (
                        await box.get("org/default-org/data-source/ds-5") is None
                    )
                    # DB 도 비움
                    assert "ds-5" not in repo._state["sources"]

            _run(_t())
