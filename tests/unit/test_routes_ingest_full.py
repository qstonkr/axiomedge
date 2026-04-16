"""Unit tests for src/api/routes/ingest.py — single file upload, batch upload, directory ingest."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.app  # noqa: F401
from src.api.routes import ingest as ingest_mod


def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> Any:
    from src.api.state import AppState
    state = AppState()
    for k, v in overrides.items():
        state[k] = v
    return state


def _make_app():
    app = FastAPI()
    app.include_router(ingest_mod.router)
    return app


# ============================================================================
# _OnnxSparseEmbedder
# ============================================================================
class TestOnnxSparseEmbedder:
    def test_embed_sparse(self):
        provider = MagicMock()
        provider.encode = MagicMock(return_value={"lexical_weights": [{"word": 0.5}]})
        embedder = ingest_mod._OnnxSparseEmbedder(provider)

        async def _go():
            result = await embedder.embed_sparse(["hello"])
            assert result == [{"word": 0.5}]

        _run(_go())

    def test_embed_sparse_no_weights(self):
        provider = MagicMock()
        provider.encode = MagicMock(return_value={})
        embedder = ingest_mod._OnnxSparseEmbedder(provider)

        async def _go():
            result = await embedder.embed_sparse(["hello", "world"])
            assert len(result) == 2

        _run(_go())


# ============================================================================
# POST /api/v1/knowledge/ingest
# ============================================================================
class TestIngestDirectory:
    def test_no_services(self):
        state = _mock_state()
        with patch.object(ingest_mod, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/ingest",
                        json={"source_dir": "/tmp/docs", "kb_id": "test"},
                    )
                    assert resp.status_code == 503

            _run(_go())

    def test_directory_not_found(self):
        store = AsyncMock()
        embedder = MagicMock()
        state = _mock_state(qdrant_store=store, embedder=embedder)
        with patch.object(ingest_mod, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/ingest",
                        json={"source_dir": "/nonexistent/path", "kb_id": "test"},
                    )
                    assert resp.status_code == 400

            _run(_go())

    def test_ingest_success(self, tmp_path):
        # Create temp files
        (tmp_path / "doc.txt").write_text("hello world")

        store = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"lexical_weights": [{}]})
        collections = MagicMock()
        collections.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_store=store, embedder=embedder, qdrant_collections=collections)

        mock_parse_result = MagicMock()
        mock_parse_result.full_text = "hello world"

        mock_ingest_result = MagicMock()
        mock_ingest_result.chunks_stored = 3

        with patch.object(ingest_mod, "_get_state", return_value=state), \
             patch("src.pipelines.document_parser.parse_file_enhanced", return_value=mock_parse_result), \
             patch("src.pipelines.ingestion.IngestionPipeline") as mock_pipeline_cls:
            mock_pipeline = AsyncMock()
            mock_pipeline.ingest = AsyncMock(return_value=mock_ingest_result)
            mock_pipeline_cls.return_value = mock_pipeline

            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/ingest",
                        json={"source_dir": str(tmp_path), "kb_id": "test"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["documents_processed"] == 1
                    assert data["chunks_created"] == 3

            _run(_go())

    def test_ingest_with_parse_error(self, tmp_path):
        (tmp_path / "bad.txt").write_text("content")

        store = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"lexical_weights": [{}]})
        collections = MagicMock()
        collections.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_store=store, embedder=embedder, qdrant_collections=collections)

        with patch.object(ingest_mod, "_get_state", return_value=state), \
             patch("src.pipelines.document_parser.parse_file_enhanced", side_effect=RuntimeError("parse fail")), \
             patch("src.pipelines.ingestion.IngestionPipeline") as mock_pipeline_cls:
            mock_pipeline_cls.return_value = AsyncMock()

            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/ingest",
                        json={"source_dir": str(tmp_path), "kb_id": "test"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert len(data["errors"]) == 1

            _run(_go())


# ============================================================================
# POST /api/v1/knowledge/upload
# ============================================================================
class TestUploadFile:
    def test_no_services(self):
        state = _mock_state()
        with patch.object(ingest_mod, "_get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/upload",
                        files={"file": ("test.txt", b"hello", "text/plain")},
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 503

            _run(_go())

    def test_upload_success(self):
        store = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"lexical_weights": [{}]})
        collections = MagicMock()
        collections.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_store=store, embedder=embedder, qdrant_collections=collections)

        mock_parse_result = MagicMock()
        mock_parse_result.full_text = "hello"

        mock_ingest_result = MagicMock()
        mock_ingest_result.chunks_stored = 2

        with patch.object(ingest_mod, "_get_state", return_value=state), \
             patch("src.pipelines.document_parser.parse_file_enhanced", return_value=mock_parse_result), \
             patch("src.pipelines.ingestion.IngestionPipeline") as mock_pipeline_cls:
            mock_pipeline = AsyncMock()
            mock_pipeline.ingest = AsyncMock(return_value=mock_ingest_result)
            mock_pipeline_cls.return_value = mock_pipeline

            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/upload",
                        files={"file": ("test.txt", b"hello world content", "text/plain")},
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["chunks_created"] == 2

            _run(_go())

    def test_upload_empty_content(self):
        store = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"lexical_weights": [{}]})
        collections = MagicMock()
        collections.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_store=store, embedder=embedder, qdrant_collections=collections)

        mock_parse_result = MagicMock()
        mock_parse_result.full_text = ""

        with patch.object(ingest_mod, "_get_state", return_value=state), \
             patch("src.pipelines.document_parser.parse_file_enhanced", return_value=mock_parse_result), \
             patch("src.pipelines.ingestion.IngestionPipeline") as mock_pipeline_cls:
            mock_pipeline_cls.return_value = AsyncMock()

            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/upload",
                        files={"file": ("test.txt", b"", "text/plain")},
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 500

            _run(_go())
