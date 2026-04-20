"""Unit tests for remaining routes: rag, ingest, data_sources."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.app  # noqa: F401


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _mock_state(**kwargs):
    from src.api.state import AppState
    state = AppState()
    for k, v in kwargs.items():
        state[k] = v
    return state


# ===========================================================================
# RAG routes (src/api/routes/rag.py)
# ===========================================================================

class TestRagRoutes:
    def _make_app(self):
        from src.api.routes.rag import knowledge_router
        app = FastAPI()
        app.include_router(knowledge_router)
        return app

    def test_rag_query_no_pipeline(self):
        state = _mock_state(rag_pipeline=None)
        # The route imports _get_state from src.api.app, so patch there
        with patch("src.api.app._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/knowledge/ask", json={"query": "test"})
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["query"] == "test"
                    assert data["answer"] is None
            _run(_t())

    def test_rag_query_with_pipeline(self):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"query": "test", "answer": "response", "chunks": []}
        rag = AsyncMock()
        rag.process = AsyncMock(return_value=mock_result)
        state = _mock_state(rag_pipeline=rag)
        with patch("src.api.app._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/knowledge/ask",
                                        json={"query": "test", "kb_ids": ["kb1"]})
                    assert resp.status_code == 200
                    assert resp.json()["answer"] == "response"
            _run(_t())

    def test_rag_query_pipeline_error(self):
        rag = AsyncMock()
        rag.process = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        state = _mock_state(rag_pipeline=rag)
        with patch("src.api.app._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/knowledge/ask",
                                        json={"query": "test", "kb_ids": ["kb1"]})
                    assert resp.status_code == 200
                    assert resp.json()["answer"] is None
            _run(_t())

    def test_get_rag_config(self):
        app = self._make_app()
        async def _t():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/api/v1/knowledge/rag/config")
                assert resp.status_code == 200
                assert "mode" in resp.json()
        _run(_t())

    def test_get_rag_stats(self):
        app = self._make_app()
        async def _t():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/api/v1/knowledge/rag/stats")
                assert resp.status_code == 200
                assert "total_queries" in resp.json()
        _run(_t())


# ===========================================================================
# Ingest routes (src/api/routes/ingest.py)
# ===========================================================================

class TestIngestRoutes:
    def _make_app(self):
        from src.api.routes.ingest import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_ingest_no_services(self):
        state = _mock_state(qdrant_store=None, embedder=None)
        with patch("src.api.routes.ingest._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/knowledge/ingest",
                                        json={"source_dir": "/tmp/docs", "kb_id": "test"})
                    assert resp.status_code == 503
            _run(_t())

    def test_ingest_bad_directory(self):
        state = _mock_state(qdrant_store=MagicMock(), embedder=MagicMock())
        with patch("src.api.routes.ingest._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/knowledge/ingest",
                                        json={"source_dir": "/nonexistent/path", "kb_id": "test"})
                    assert resp.status_code == 400
            _run(_t())


class TestOnnxSparseEmbedder:
    def test_adapter(self):
        from src.api.routes.ingest import _OnnxSparseEmbedder
        onnx = MagicMock()
        onnx.encode = MagicMock(return_value={"lexical_weights": [{"1": 0.5}]})
        adapter = _OnnxSparseEmbedder(onnx)
        result = _run(adapter.embed_sparse(["hello"]))
        assert result == [{"1": 0.5}]


# ===========================================================================
# Data Sources routes (src/api/routes/data_sources.py)
# ===========================================================================
# 0005 이후 모든 data-sources 라우트가 OrgContext 강제. bypass_route_auth
# fixture 가 get_current_org → fake org_id="default-org" 로 자동 주입.
pytestmark = pytest.mark.usefixtures("bypass_route_auth")


class TestDataSourceRoutes:
    def _make_app(self):
        from src.api.routes.data_sources import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_list_no_repo(self):
        state = _mock_state(data_source_repo=None)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources")
                    assert resp.status_code == 200
                    assert resp.json()["sources"] == []
            _run(_t())

    def test_list_with_repo(self):
        repo = AsyncMock()
        repo.list = AsyncMock(return_value=[{"id": "ds1", "name": "confluence"}])
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources")
                    assert len(resp.json()["sources"]) == 1
            _run(_t())

    def test_create(self):
        repo = AsyncMock()
        repo.register = AsyncMock()
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/data-sources",
                                        json={"name": "test", "type": "confluence"})
                    assert resp.status_code == 200
                    assert resp.json()["success"] is True
            _run(_t())

    def test_get_found(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value={"id": "ds1", "name": "confluence"})
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources/ds1")
                    assert resp.status_code == 200
                    assert resp.json()["name"] == "confluence"
            _run(_t())

    def test_get_not_found(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources/missing")
                    assert resp.status_code == 404
            _run(_t())

    def test_update_found(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value={"id": "ds1", "status": "active"})
        repo.update_status = AsyncMock()
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.put("/api/v1/admin/data-sources/ds1",
                                       json={"status": "inactive"})
                    assert resp.status_code == 200
            _run(_t())

    def test_update_not_found(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.put("/api/v1/admin/data-sources/missing", json={})
                    assert resp.status_code == 404
            _run(_t())

    def test_delete_found(self):
        repo = AsyncMock()
        repo.delete = AsyncMock(return_value=True)
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/data-sources/ds1")
                    assert resp.status_code == 200
            _run(_t())

    def test_delete_not_found(self):
        repo = AsyncMock()
        repo.delete = AsyncMock(return_value=False)
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.delete("/api/v1/admin/data-sources/missing")
                    assert resp.status_code == 404
            _run(_t())

    def test_trigger_sync(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value={
            "id": "ds1", "status": "active",
            "config": {"page_id": "12345", "confluence_url": "https://wiki.example.com"},
        })
        repo.update_status = AsyncMock()
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/data-sources/ds1/trigger")
                    assert resp.status_code == 200
                    assert "Sync trigger" in resp.json()["message"]
            _run(_t())

    def test_trigger_sync_not_found(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/data-sources/missing/trigger")
                    assert resp.status_code == 404
            _run(_t())

    def test_get_status(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value={"id": "ds1", "status": "syncing", "last_sync_at": None, "last_sync_result": {}})
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources/ds1/status")
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "syncing"
            _run(_t())

    def test_get_status_no_repo(self):
        state = _mock_state(data_source_repo=None)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.get("/api/v1/admin/data-sources/ds1/status")
                    assert resp.json()["status"] == "idle"
            _run(_t())

    def test_file_ingest(self):
        repo = AsyncMock()
        repo.get_by_name = AsyncMock(return_value={"id": "ds1"})
        repo.update_status = AsyncMock()
        state = _mock_state(data_source_repo=repo)
        with patch("src.api.routes.data_sources._get_state", return_value=state):
            app = self._make_app()
            async def _t():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post("/api/v1/admin/data-sources/file-ingest",
                                        json={"source_name": "file-upload"})
                    assert resp.status_code == 200
            _run(_t())
