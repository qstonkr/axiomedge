"""Final coverage push — target remaining easy-to-test modules."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _run(coro):
    return asyncio.run(coro)


def _make_session_maker():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    maker = MagicMock()
    maker.return_value = session
    return maker, session


# ===========================================================================
# Quality Routes (79 uncovered)
# ===========================================================================
class TestQualityRoutes:
    def _mock_state(self, **overrides):
        from src.api.state import AppState
        state = AppState()
        for k, v in overrides.items():
            state[k] = v
        return state

    def _make_app(self):
        import src.api.app  # noqa: F401
        from src.api.routes import quality as quality_mod
        app = FastAPI()
        app.include_router(quality_mod.router)
        return app, quality_mod

    def test_dedup_stats(self):
        app, mod = self._make_app()
        state = self._mock_state()
        with patch.object(mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/dedup/stats")
                    assert resp.status_code == 200

            _run(_go())

    def test_dedup_conflicts(self):
        app, mod = self._make_app()
        state = self._mock_state()
        with patch.object(mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/dedup/conflicts")
                    assert resp.status_code == 200

            _run(_go())

    def test_eval_status(self):
        app, mod = self._make_app()
        state = self._mock_state()
        with patch.object(mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/eval/status")
                    assert resp.status_code == 200

            _run(_go())

    def test_eval_history(self):
        app, mod = self._make_app()
        state = self._mock_state()
        with patch.object(mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/eval/history")
                    assert resp.status_code == 200

            _run(_go())


# ===========================================================================
# GlossaryRepository (105 uncovered)
# ===========================================================================
class TestGlossaryRepository:
    def test_list_by_kb_basic(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()

        row = MagicMock()
        row.id = "t1"
        row.kb_id = "kb1"
        row.term = "서버"
        row.term_ko = "서버"
        row.definition = "server"
        row.synonyms = '["server"]'
        row.abbreviations = '["SVR"]'
        row.status = "approved"
        row.term_type = "word"
        row.scope = "global"
        row.source = "csv_import"
        row.physical_meaning = ""
        row.composition_info = ""
        row.domain_name = ""
        row.related_terms = "[]"
        row.created_at = None
        row.updated_at = None
        row.approved_by = None
        row.approved_at = None

        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        repo = GlossaryRepository(maker)

        async def _go():
            terms = await repo.list_by_kb("kb1", limit=10, offset=0)
            assert len(terms) == 1

        _run(_go())

    def test_count_by_kb(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 42
        session.execute = AsyncMock(return_value=result_mock)
        repo = GlossaryRepository(maker)

        async def _go():
            count = await repo.count_by_kb("kb1")
            assert count == 42

        _run(_go())

    def test_get_by_id(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = GlossaryRepository(maker)

        async def _go():
            term = await repo.get_by_id("missing")
            assert term is None

        _run(_go())

    def test_delete(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        existing = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result_mock)
        session.delete = AsyncMock()
        session.commit = AsyncMock()
        repo = GlossaryRepository(maker)

        async def _go():
            result = await repo.delete("t1")
            assert result is True

        _run(_go())

    def test_delete_not_found(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        repo = GlossaryRepository(maker)

        async def _go():
            result = await repo.delete("missing")
            assert result is False

        _run(_go())

    def test_search(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)
        repo = GlossaryRepository(maker)

        async def _go():
            results = await repo.search("kb1", "test")
            assert results == []

        _run(_go())

    def test_bulk_delete(self):
        from src.stores.postgres.repositories.glossary import GlossaryRepository

        maker, session = _make_session_maker()
        exec_result = MagicMock()
        exec_result.rowcount = 2
        session.execute = AsyncMock(return_value=exec_result)
        session.commit = AsyncMock()
        repo = GlossaryRepository(maker)

        async def _go():
            result = await repo.bulk_delete(["id1", "id2"])
            assert result == 2

        _run(_go())


# ===========================================================================
# Auth route: more endpoints
# ===========================================================================
class TestAuthRoutesMore:
    def _mock_state(self, **overrides):
        from src.api.state import AppState
        state = AppState()
        for k, v in overrides.items():
            state[k] = v
        return state

    def _make_app(self):
        import src.api.app  # noqa: F401
        from src.api.routes import auth as auth_mod
        app = FastAPI()
        app.include_router(auth_mod.router)
        return app, auth_mod

    def test_assign_role_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.assign_role = AsyncMock(return_value={"role": "editor"})
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/users/u1/roles", json={"role": "editor"})
                    assert resp.status_code == 200
            _run(_go())

    def test_revoke_role_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.revoke_role = AsyncMock(return_value=True)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/auth/users/u1/roles/editor")
                    assert resp.status_code == 200
            _run(_go())

    def test_get_user_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.get_user = AsyncMock(return_value={"id": "u1", "email": "a@b.com"})
        auth_svc.get_user_roles = AsyncMock(return_value=[{"role": "viewer"}])
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/auth/users/u1")
                    assert resp.status_code == 200
            _run(_go())

    def test_get_user_not_found(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.get_user = AsyncMock(return_value=None)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/auth/users/missing")
                    assert resp.status_code == 404
            _run(_go())

    def test_set_kb_permission_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.set_kb_permission = AsyncMock(return_value={"level": "contributor"})
        state = self._mock_state(auth_service=auth_svc)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc), \
             patch.object(auth_mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/kb/kb1/permissions", json={
                        "user_id": "u1", "permission_level": "contributor"
                    })
                    assert resp.status_code == 200
            _run(_go())

    def test_remove_kb_permission_success(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.remove_kb_permission = AsyncMock(return_value=True)
        state = self._mock_state(auth_service=auth_svc)
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc), \
             patch.object(auth_mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/auth/kb/kb1/permissions/u1")
                    assert resp.status_code == 200
            _run(_go())

    def test_my_activities_with_service(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.get_user_activities = AsyncMock(return_value=[{"type": "search"}])
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/auth/my-activities")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["activities"]) == 1
            _run(_go())

    def test_my_activities_summary_with_service(self):
        app, auth_mod = self._make_app()
        auth_svc = AsyncMock()
        auth_svc.get_activity_summary = AsyncMock(return_value={"total": 5})
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_auth_service", return_value=auth_svc):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/auth/my-activities/summary")
                    assert resp.status_code == 200
            _run(_go())

    def test_abac_create_policy(self):
        app, auth_mod = self._make_app()
        state = self._mock_state()
        with patch("src.auth.dependencies.AUTH_ENABLED", False), \
             patch.object(auth_mod, "_get_state", return_value=state):
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/auth/abac/policies", json={"name": "test"})
                    assert resp.status_code in (200, 201, 503)
            _run(_go())

    def test_is_cookie_secure(self):
        from src.api.routes.auth import _is_cookie_secure
        with patch.dict("os.environ", {"AUTH_COOKIE_SECURE": "true"}):
            assert _is_cookie_secure() is True
        with patch.dict("os.environ", {"AUTH_COOKIE_SECURE": "false"}):
            assert _is_cookie_secure() is False


# ===========================================================================
# Search route: hub search with engine
# ===========================================================================
class TestSearchRouteHub:
    def _mock_state(self, **overrides):
        from src.api.state import AppState
        state = AppState()
        for k, v in overrides.items():
            state[k] = v
        return state

    def test_hub_search_503(self):
        import src.api.app  # noqa: F401
        from src.api.routes import search as search_mod
        state = self._mock_state()
        with patch.object(search_mod, "_get_state", return_value=state):
            app = FastAPI()
            app.include_router(search_mod.router)
            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/search/hub", json={"query": "test"})
                    assert resp.status_code == 503
            _run(_go())

    def test_hub_search_model_classes(self):
        from src.api.routes.search import HubSearchRequest, HubSearchResponse, QueryPreprocessInfo, KBFilter
        req = HubSearchRequest(query="test", top_k=5)
        assert req.query == "test"
        resp = HubSearchResponse(query="test")
        assert resp.chunks == []
        info = QueryPreprocessInfo(corrected_query="test", original_query="tset")
        assert info.corrections == []
        filt = KBFilter(kb_ids=["kb1"])
        assert filt.kb_ids == ["kb1"]


# ===========================================================================
# Dedup result_tracker: track_conflict, list methods
# ===========================================================================
class TestDedupResultTrackerAdditional:
    def test_track_conflict(self):
        from src.pipelines.dedup.result_tracker import DedupResultTracker
        redis = AsyncMock()
        redis.xadd = AsyncMock()
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()
        tracker = DedupResultTracker(redis_client=redis)

        result = MagicMock()
        result.doc_id = "d1"
        result.status = "conflict"
        result.duplicate_of = "d2"
        result.similarity_score = 0.85
        result.stage_reached = 4
        result.processing_time_ms = 10.0
        result.resolution = "none"
        result.conflict_types = ["semantic"]
        conflict_detail = MagicMock()

        async def _go():
            await tracker.track_conflict(
                result=result,
                conflict_detail=conflict_detail,
                kb_id="kb1",
                doc_title="test.pdf",
            )
            redis.xadd.assert_awaited()

        _run(_go())

    def test_track_conflict_disabled(self):
        from src.pipelines.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)

        async def _go():
            cid = await tracker.track_conflict(MagicMock(), MagicMock(), "kb1")
            assert cid == ""

        _run(_go())

    def test_get_stats_disabled(self):
        from src.pipelines.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)

        async def _go():
            stats = await tracker.get_stats()
            assert stats["total_duplicates_found"] == 0
            assert stats["pending"] == 0

        _run(_go())

    def test_get_stats_enabled(self):
        from src.pipelines.dedup.result_tracker import DedupResultTracker
        redis = AsyncMock()
        redis.xlen = AsyncMock(return_value=100)

        # Mock scan_iter as async generator
        async def mock_scan(*args, **kwargs):
            for k in []:
                yield k
        redis.scan_iter = mock_scan
        tracker = DedupResultTracker(redis_client=redis)

        async def _go():
            stats = await tracker.get_stats()
            assert stats["total_duplicates_found"] == 100

        _run(_go())
