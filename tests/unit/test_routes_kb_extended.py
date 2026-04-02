"""Extended unit tests for src/api/routes/kb.py — admin endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.app  # noqa: F401
from src.api.routes import kb


def _make_app():
    app = FastAPI()
    app.include_router(kb.router)
    app.include_router(kb.admin_router)
    return app


def _mock_state(**overrides):
    from src.api.state import AppState
    state = AppState()
    for k, v in overrides.items():
        state[k] = v
    return state


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/stats
# ---------------------------------------------------------------------------
class TestAdminKbStats:
    def test_with_store(self):
        store = AsyncMock()
        store.count = AsyncMock(return_value=42)
        state = _mock_state(qdrant_store=store)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["kb_id"] == "test-kb"
                    assert data["total_chunks"] == 42

            _run(_go())

    def test_no_store(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_chunks"] == 0

            _run(_go())

    def test_store_exception(self):
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("err"))
        state = _mock_state(qdrant_store=store)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/stats")
                    assert resp.status_code == 200
                    assert resp.json()["total_chunks"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/lifecycle
# ---------------------------------------------------------------------------
class TestAdminKbLifecycle:
    def test_lifecycle(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/lifecycle")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["kb_id"] == "test-kb"
                    assert data["stage"] == "active"

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/coverage-gaps
# ---------------------------------------------------------------------------
class TestAdminKbCoverageGaps:
    def test_coverage_gaps(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/coverage-gaps")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["gaps"] == []
                    assert data["coverage_score"] == 1.0

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/impact
# ---------------------------------------------------------------------------
class TestAdminKbImpact:
    def test_impact(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/impact")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_queries_served"] == 0

            _run(_go())

    def test_impact_rankings(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/impact/rankings")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["rankings"] == []

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/freshness
# ---------------------------------------------------------------------------
class TestAdminKbFreshness:
    def test_freshness(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/freshness")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["freshness_score"] == 0.0

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/value-tiers
# ---------------------------------------------------------------------------
class TestAdminKbValueTiers:
    def test_value_tiers(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/value-tiers")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["tiers"] == []

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/members
# ---------------------------------------------------------------------------
class TestAdminKbMembers:
    def test_list_members(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/members")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["members"] == []
                    assert data["total"] == 0

            _run(_go())

    def test_add_member(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/kb/test-kb/members",
                        json={"user_id": "u1", "role": "contributor"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            _run(_go())

    def test_remove_member(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/kb/test-kb/members/u1")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["member_id"] == "u1"

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores
# ---------------------------------------------------------------------------
class TestAdminKbTrustScores:
    def test_with_repo(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[{"kts_score": 0.8}])
        state = _mock_state(trust_score_repo=repo)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total"] == 1

            _run(_go())

    def test_no_repo(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores")
                    assert resp.status_code == 200
                    assert resp.json()["total"] == 0

            _run(_go())

    def test_repo_error(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db error"))
        state = _mock_state(trust_score_repo=repo)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores")
                    assert resp.status_code == 200
                    assert resp.json()["total"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores/distribution
# ---------------------------------------------------------------------------
class TestTrustScoreDistribution:
    def test_with_items(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"kts_score": 0.9, "confidence_tier": "HIGH"},
            {"kts_score": 0.5, "confidence_tier": "MEDIUM"},
            {"kts_score": 0.2, "confidence_tier": "LOW"},
            {"kts_score": 0.1, "confidence_tier": "unknown_tier"},
        ])
        state = _mock_state(trust_score_repo=repo)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores/distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["distribution"]["HIGH"] == 1
                    assert data["distribution"]["MEDIUM"] == 1
                    assert data["distribution"]["LOW"] == 1
                    assert data["distribution"]["UNCERTAIN"] == 1
                    assert data["total"] == 4

            _run(_go())

    def test_no_repo(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores/distribution")
                    assert resp.status_code == 200
                    assert resp.json()["avg_score"] == 0

            _run(_go())

    def test_empty_items(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[])
        state = _mock_state(trust_score_repo=repo)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test-kb/trust-scores/distribution")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["avg_score"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/search-cache/clear
# ---------------------------------------------------------------------------
class TestClearSearchCache:
    def test_clear_success(self):
        cache = AsyncMock()
        cache.clear = AsyncMock(return_value=10)
        state = _mock_state(search_cache=cache)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/kb/search-cache/clear")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["deleted"] == 10

            _run(_go())

    def test_clear_no_cache(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/kb/search-cache/clear")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["deleted"] == 0

            _run(_go())

    def test_clear_error(self):
        cache = AsyncMock()
        cache.clear = AsyncMock(side_effect=RuntimeError("redis err"))
        state = _mock_state(search_cache=cache)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/kb/search-cache/clear")
                    assert resp.status_code == 500

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/stats (aggregation)
# ---------------------------------------------------------------------------
class TestAdminKbAggregation:
    def test_aggregation_with_registry(self):
        registry = AsyncMock()
        registry.list_all = AsyncMock(return_value=[
            {"kb_id": "kb1", "document_count": 5},
            {"kb_id": "kb2", "document_count": 10},
        ])
        collections = MagicMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["kb_kb1", "kb_kb2"])
        config_mock = MagicMock()
        config_mock.collection_prefix = "kb"
        collections._provider = MagicMock()
        collections._provider.config = config_mock
        store = AsyncMock()
        store.count = AsyncMock(return_value=50)
        state = _mock_state(kb_registry=registry, qdrant_collections=collections, qdrant_store=store)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_kbs"] == 2
                    assert data["total_documents"] == 15
                    assert data["total_chunks"] == 100  # 50 * 2 collections

            _run(_go())

    def test_aggregation_no_services(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_kbs"] == 0

            _run(_go())


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
class TestAdminUpdateKb:
    def test_update_kb(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.put(
                        "/api/v1/admin/kb/test-kb",
                        json={"name": "Updated KB"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            _run(_go())


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------
class TestAdminDeleteKb:
    def test_delete_success(self):
        provider = AsyncMock()
        client_mock = AsyncMock()
        provider.ensure_client = AsyncMock(return_value=client_mock)
        client_mock.delete_collection = AsyncMock()
        collections = MagicMock()
        collections.get_collection_name = MagicMock(return_value="kb_test")
        state = _mock_state(qdrant_provider=provider, qdrant_collections=collections)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/kb/test-kb")
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            _run(_go())

    def test_delete_no_provider(self):
        state = _mock_state()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/kb/test-kb")
                    assert resp.status_code == 503

            _run(_go())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id} (single)
# ---------------------------------------------------------------------------
class TestAdminGetKb:
    def test_with_registry(self):
        registry = AsyncMock()
        registry.get_kb = AsyncMock(return_value={"kb_id": "test", "name": "Test KB"})
        state = _mock_state(kb_registry=registry)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test")
                    assert resp.status_code == 200
                    assert resp.json()["name"] == "Test KB"

            _run(_go())

    def test_fallback_no_registry(self):
        store = AsyncMock()
        store.count = AsyncMock(return_value=10)
        state = _mock_state(qdrant_store=store)
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["kb_id"] == "test"
                    assert data["chunk_count"] == 10

            _run(_go())
