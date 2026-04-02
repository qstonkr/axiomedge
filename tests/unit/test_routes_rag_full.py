"""Unit tests for src/api/routes/rag.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import rag as rag_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    state.setdefault = lambda k, v: _map.setdefault(k, v)
    return state


def _make_app():
    app = FastAPI()
    app.include_router(rag_mod.knowledge_router)
    app.include_router(rag_mod.rag_query_router)
    return app


# ============================================================================
# RAG Query (POST /api/v1/knowledge/ask)
# ============================================================================

class TestRagQuery:
    def test_rag_query_with_pipeline(self):
        rag = AsyncMock()
        result_mock = MagicMock()
        result_mock.to_dict.return_value = {"query": "test", "answer": "answer1", "chunks": []}
        rag.process = AsyncMock(return_value=result_mock)

        app = _make_app()
        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state(rag_pipeline=rag)):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/knowledge/ask", json={"query": "test", "kb_ids": ["kb1"]})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["answer"] == "answer1"

    def test_rag_query_no_pipeline(self):
        app = _make_app()
        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state()):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/knowledge/ask", json={"query": "hello"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["answer"] is None

    def test_rag_query_pipeline_exception(self):
        rag = AsyncMock()
        rag.process = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        app = _make_app()
        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state(rag_pipeline=rag)):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/knowledge/ask", json={"query": "test"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["answer"] is None

    def test_rag_query_no_kb_ids(self):
        rag = AsyncMock()
        result_mock = MagicMock()
        result_mock.to_dict.return_value = {"query": "test", "answer": "a", "chunks": []}
        rag.process = AsyncMock(return_value=result_mock)

        app = _make_app()
        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state(rag_pipeline=rag)):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/knowledge/ask", json={"query": "test"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["answer"] == "a"


# ============================================================================
# RAG Config (GET /api/v1/knowledge/rag/config)
# ============================================================================

class TestRagConfig:
    def test_get_rag_config(self):
        result = _run(rag_mod.get_rag_config())
        assert result["mode"] == "classic"
        assert result["top_k"] == 5


# ============================================================================
# RAG Stats (GET /api/v1/knowledge/rag/stats)
# ============================================================================

class TestRagStats:
    def test_get_rag_stats(self):
        result = _run(rag_mod.get_rag_stats())
        assert result["total_queries"] == 0


# ============================================================================
# RAG Query Alias
# ============================================================================

class TestRagQueryAlias:
    def test_alias_delegates_to_rag_query(self):
        app = _make_app()

        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state()):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post("/api/v1/rag-query", json={"query": "hello"})
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        assert resp.json()["query"] == "hello"


# ============================================================================
# File Upload & Ingest (POST /api/v1/knowledge/file-upload-ingest)
# ============================================================================

class TestFileUploadIngest:
    def test_no_services(self):
        app = _make_app()

        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state()):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        data={"kb_id": "kb1"},
                        files={"file": ("test.txt", b"hello world", "text/plain")},
                    )
            return resp

        resp = _run(_test())
        assert resp.status_code == 503

    def test_no_files(self):
        store = MagicMock()
        embedder = MagicMock()
        app = _make_app()

        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state(
                qdrant_store=store, embedder=embedder
            )):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        data={"kb_id": "kb1"},
                    )
            return resp

        resp = _run(_test())
        assert resp.status_code == 400

    def test_upload_with_files(self):
        store = MagicMock()
        embedder = MagicMock()
        collections = AsyncMock()
        collections.ensure_collection = AsyncMock()
        collections.get_collection_name = MagicMock(return_value="kb_kb1")
        kb_registry = AsyncMock()
        kb_registry.get_kb = AsyncMock(return_value={"id": "kb1"})

        app = _make_app()

        async def _test():
            state = _mock_state(
                qdrant_store=store,
                embedder=embedder,
                qdrant_collections=collections,
                kb_registry=kb_registry,
            )
            with patch("src.api.app._get_state", return_value=state):
                with patch("src.api.routes.rag.create_job", new_callable=AsyncMock, return_value="job123"):
                    with patch("src.api.routes.rag._process_files", new_callable=AsyncMock):
                        with patch("src.pipeline.ingestion.IngestionPipeline"):
                            with patch("src.api.routes.ingest._OnnxSparseEmbedder"):
                                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                    resp = await client.post(
                                        "/api/v1/knowledge/file-upload-ingest",
                                        data={"kb_id": "kb1"},
                                        files={"file": ("test.txt", b"hello", "text/plain")},
                                    )
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["job_id"] == "job123"


# ============================================================================
# Reingest from JSONL (POST /api/v1/knowledge/reingest-from-jsonl)
# ============================================================================

class TestReingestFromJsonl:
    def test_no_services(self):
        app = _make_app()

        async def _test():
            with patch("src.api.app._get_state", return_value=_mock_state()):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/knowledge/reingest-from-jsonl",
                        data={"kb_id": "kb1"},
                    )
            return resp

        resp = _run(_test())
        assert resp.status_code == 503

    def test_reingest_success(self):
        store = MagicMock()
        embedder = MagicMock()
        app = _make_app()

        mock_reader = MagicMock()
        mock_reader.count.return_value = 5

        async def _test():
            state = _mock_state(qdrant_store=store, embedder=embedder)
            with patch("src.api.app._get_state", return_value=state):
                with patch("src.pipeline.jsonl_checkpoint.get_jsonl_path", return_value="/tmp/knowledge-local/test.jsonl"):
                    with patch("src.pipeline.jsonl_checkpoint.JsonlCheckpointReader", return_value=mock_reader):
                        with patch("src.pipeline.ingestion.IngestionPipeline"):
                            with patch("src.api.routes.ingest._OnnxSparseEmbedder"):
                                with patch("src.api.routes.rag.create_job", new_callable=AsyncMock, return_value="j1"):
                                    with patch("src.api.routes.rag._stage2_ingest_from_jsonl", new_callable=AsyncMock):
                                        with patch("os.path.realpath", side_effect=lambda p: p):
                                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                                resp = await client.post(
                                                    "/api/v1/knowledge/reingest-from-jsonl",
                                                    data={"kb_id": "kb1"},
                                                )
            return resp

        resp = _run(_test())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["records"] == 5

    def test_reingest_empty_jsonl(self):
        store = MagicMock()
        embedder = MagicMock()
        app = _make_app()

        mock_reader = MagicMock()
        mock_reader.count.return_value = 0

        async def _test():
            state = _mock_state(qdrant_store=store, embedder=embedder)
            with patch("src.api.app._get_state", return_value=state):
                with patch("src.pipeline.jsonl_checkpoint.get_jsonl_path", return_value="/tmp/knowledge-local/test.jsonl"):
                    with patch("src.pipeline.jsonl_checkpoint.JsonlCheckpointReader", return_value=mock_reader):
                        with patch("os.path.realpath", side_effect=lambda p: p):
                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                resp = await client.post(
                                    "/api/v1/knowledge/reingest-from-jsonl",
                                    data={"kb_id": "kb1"},
                                )
            return resp

        resp = _run(_test())
        assert resp.status_code == 404

    def test_reingest_invalid_path(self):
        store = MagicMock()
        embedder = MagicMock()
        app = _make_app()

        def _fake_realpath(p):
            if p == "/etc/passwd":
                return "/etc/passwd"
            return "/tmp/knowledge-local"

        async def _test():
            state = _mock_state(qdrant_store=store, embedder=embedder)
            with patch("src.api.app._get_state", return_value=state):
                with patch("os.path.realpath", side_effect=_fake_realpath):
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        resp = await client.post(
                            "/api/v1/knowledge/reingest-from-jsonl",
                            data={"kb_id": "kb1", "jsonl_path": "/etc/passwd"},
                        )
            return resp

        resp = _run(_test())
        assert resp.status_code == 400
