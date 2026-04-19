"""Unit tests for src/api/routes/kb.py — KB management endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import kb


def _make_test_app():
    app = FastAPI()
    app.include_router(kb.router)
    app.include_router(kb.admin_router)
    return app


# ---------------------------------------------------------------------------
# GET /api/v1/kb/list
# ---------------------------------------------------------------------------

class TestListKBs:
    def test_list_kbs_with_registry(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_all = AsyncMock(return_value=[
            {"kb_id": "kb1", "name": "KB One", "status": "active", "document_count": 10},
            {"kb_id": "kb2", "name": "KB Two", "status": "active", "document_count": 5},
        ])
        state["kb_registry"] = registry

        store = AsyncMock()
        store.count = AsyncMock(return_value=100)
        state["qdrant_store"] = store

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/kb/list")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["kbs"]) == 2
                    assert data["kbs"][0]["kb_id"] == "kb1"

            asyncio.run(_run())

    def test_list_kbs_no_registry_no_qdrant(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/kb/list")
                    assert resp.status_code == 200
                    assert resp.json()["kbs"] == []

            asyncio.run(_run())

    def test_list_kbs_registry_error_falls_back_to_qdrant(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_all = AsyncMock(side_effect=RuntimeError("DB down"))
        state["kb_registry"] = registry

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["kb_test"])
        collections._provider = MagicMock()
        collections._provider.config.collection_prefix = "kb"
        state["qdrant_collections"] = collections

        store = AsyncMock()
        store.count = AsyncMock(return_value=42)
        state["qdrant_store"] = store

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/kb/list")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert len(data["kbs"]) == 1
                    assert data["kbs"][0]["kb_id"] == "test"
                    assert data["kbs"][0]["chunk_count"] == 42

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/kb/create
# ---------------------------------------------------------------------------

class TestCreateKB:
    def test_create_kb_success(self):
        """B-1 Day 1: /api/v1/kb/create only accepts tier=personal now."""
        from src.api.state import AppState

        state = AppState()
        collections = AsyncMock()
        collections.ensure_collection = AsyncMock()
        registry = AsyncMock()
        registry.list_by_tier = AsyncMock(return_value=[])
        registry.create_kb = AsyncMock()
        state["qdrant_collections"] = collections
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/kb/create",
                        json={"kb_id": "pkb-new", "name": "New KB", "tier": "personal"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["kb_id"] == "pkb-new"
                    assert data["tier"] == "personal"

            asyncio.run(_run())

    def test_create_kb_no_qdrant(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/kb/create",
                        json={"kb_id": "x", "name": "X"},
                    )
                    assert resp.status_code == 503

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# DELETE /api/v1/kb/{kb_id}
# ---------------------------------------------------------------------------

class TestDeleteKB:
    def test_delete_kb_success(self):
        from src.api.state import AppState

        state = AppState()
        mock_client = AsyncMock()
        mock_client.delete_collection = AsyncMock()
        provider = AsyncMock()
        provider.ensure_client = AsyncMock(return_value=mock_client)
        state["qdrant_provider"] = provider

        collections = MagicMock()
        collections.get_collection_name = MagicMock(return_value="kb_test")
        state["qdrant_collections"] = collections

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/kb/test")
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            asyncio.run(_run())

    def test_delete_kb_no_provider(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/kb/test")
                    assert resp.status_code == 503

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb — admin list
# ---------------------------------------------------------------------------

class TestAdminListKBs:
    def test_admin_list_kbs(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_all = AsyncMock(return_value=[{"kb_id": "x", "status": "active", "document_count": 0}])
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb")
                    assert resp.status_code == 200
                    assert len(resp.json()["kbs"]) == 1

            asyncio.run(_run())

    def test_admin_list_kbs_with_status_filter(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_by_status = AsyncMock(return_value=[])
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb", params={"status": "archived"})
                    assert resp.status_code == 200
                    # Day 3: dependency injection auto-fills organization_id
                    # from get_current_org → AUTH_ENABLED=false routes to default-org.
                    registry.list_by_status.assert_called_once_with(
                        "archived", organization_id="default-org",
                    )

            asyncio.run(_run())

    def test_admin_list_kbs_with_tier_filter(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_by_tier = AsyncMock(return_value=[])
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb", params={"tier": "team"})
                    assert resp.status_code == 200
                    registry.list_by_tier.assert_called_once_with(
                        "team", organization_id="default-org",
                    )

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/stats
# ---------------------------------------------------------------------------

class TestAdminKBStats:
    def test_kb_stats_no_services(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_kbs"] == 0
                    assert data["total_chunks"] == 0
                    assert data["total_documents"] == 0

            asyncio.run(_run())

    def test_kb_stats_with_registry(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.list_all = AsyncMock(return_value=[
            {"kb_id": "kb1", "document_count": 10},
            {"kb_id": "kb2", "document_count": 5},
        ])
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["total_kbs"] == 2
                    assert data["total_documents"] == 15

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------

class TestAdminGetKB:
    def test_get_kb_from_registry(self):
        from src.api.state import AppState

        state = AppState()
        registry = AsyncMock()
        registry.get_kb = AsyncMock(return_value={"kb_id": "test", "name": "Test KB"})
        state["kb_registry"] = registry

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test")
                    assert resp.status_code == 200
                    assert resp.json()["kb_id"] == "test"

            asyncio.run(_run())

    def test_get_kb_fallback_no_registry(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["kb_id"] == "test"
                    assert data["name"] == "test"
                    assert data["status"] == "active"

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/stats
# ---------------------------------------------------------------------------

class TestAdminKBDetailStats:
    def test_kb_detail_stats_no_store(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/stats")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["kb_id"] == "test"
                    assert data["total_chunks"] == 0

            asyncio.run(_run())

    def test_kb_detail_stats_with_store(self):
        from src.api.state import AppState

        state = AppState()
        store = AsyncMock()
        store.count = AsyncMock(return_value=250)
        state["qdrant_store"] = store

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/stats")
                    data = resp.json()
                    assert data["total_chunks"] == 250

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/kb/{kb_id}
# ---------------------------------------------------------------------------

class TestAdminUpdateKB:
    def test_update_kb(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.put(
                        "/api/v1/admin/kb/test",
                        json={"name": "Updated"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# POST /api/v1/admin/kb/search-cache/clear
# ---------------------------------------------------------------------------

class TestClearSearchCache:
    def test_clear_cache_no_cache(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/kb/search-cache/clear")
                    assert resp.status_code == 200
                    assert resp.json()["deleted"] == 0

            asyncio.run(_run())

    def test_clear_cache_with_cache(self):
        from src.api.state import AppState

        state = AppState()
        cache = AsyncMock()
        cache.clear = AsyncMock(return_value=15)
        state["search_cache"] = cache

        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/kb/search-cache/clear")
                    assert resp.status_code == 200
                    assert resp.json()["deleted"] == 15

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# Stub endpoints (lifecycle, coverage-gaps, impact, etc.)
# ---------------------------------------------------------------------------

class TestStubEndpoints:
    def test_lifecycle(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/lifecycle")
                    assert resp.status_code == 200
                    assert resp.json()["kb_id"] == "test"

            asyncio.run(_run())

    def test_coverage_gaps(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/coverage-gaps")
                    assert resp.status_code == 200
                    assert resp.json()["coverage_score"] == 1.0

            asyncio.run(_run())

    def test_impact(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/impact")
                    assert resp.status_code == 200
                    assert resp.json()["total_queries_served"] == 0

            asyncio.run(_run())

    def test_freshness(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/freshness")
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_value_tiers(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/value-tiers")
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_members(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/members")
                    assert resp.status_code == 200
                    assert resp.json()["members"] == []

            asyncio.run(_run())

    def test_add_member(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/admin/kb/test/members",
                        json={"user_id": "u1", "role": "reader"},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True

            asyncio.run(_run())

    def test_remove_member(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/kb/test/members/u1")
                    assert resp.status_code == 200

            asyncio.run(_run())

    def test_impact_rankings(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/impact/rankings")
                    assert resp.status_code == 200

            asyncio.run(_run())


# ---------------------------------------------------------------------------
# GET /api/v1/admin/kb/{kb_id}/trust-scores
# ---------------------------------------------------------------------------

class TestTrustScores:
    def test_trust_scores_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/trust-scores")
                    assert resp.status_code == 200
                    assert resp.json()["items"] == []

            asyncio.run(_run())

    def test_trust_score_distribution_no_repo(self):
        from src.api.state import AppState

        state = AppState()
        with patch.object(kb, "_get_state", return_value=state):
            app = _make_test_app()
            transport = ASGITransport(app=app)

            async def _run():
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/kb/test/trust-scores/distribution")
                    assert resp.status_code == 200
                    assert resp.json()["avg_score"] == 0

            asyncio.run(_run())
