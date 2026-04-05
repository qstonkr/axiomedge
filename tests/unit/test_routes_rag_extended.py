"""Extended unit tests for src/api/routes/rag.py — file-upload-ingest, stage1/stage2."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.api.app  # noqa: F401
from src.api.routes import rag as rag_mod


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
    app.include_router(rag_mod.knowledge_router)
    app.include_router(rag_mod.rag_query_router)
    return app


# ============================================================================
# GET /api/v1/knowledge/rag/config
# ============================================================================
class TestRagConfig:
    def test_get_config(self):
        async def _go():
            app = _make_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/api/v1/knowledge/rag/config")
                assert resp.status_code == 200
                data = resp.json()
                assert data["mode"] == "classic"
                assert data["top_k"] == 5

        _run(_go())


# ============================================================================
# GET /api/v1/knowledge/rag/stats
# ============================================================================
class TestRagStats:
    def test_get_stats(self):
        async def _go():
            app = _make_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/api/v1/knowledge/rag/stats")
                assert resp.status_code == 200
                data = resp.json()
                assert data["total_queries"] == 0

        _run(_go())


# ============================================================================
# POST /api/v1/knowledge/file-upload-ingest
# ============================================================================
class TestFileUploadIngest:
    def test_no_services(self):
        state = _mock_state()
        with patch("src.api.app._get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        files={"file": ("test.txt", b"hello", "text/plain")},
                        data={"kb_id": "test-kb"},
                    )
                    assert resp.status_code == 503

            _run(_go())

    def test_no_files(self):
        store = AsyncMock()
        embedder = MagicMock()
        state = _mock_state(qdrant_store=store, embedder=embedder)
        with patch("src.api.app._get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        data={"kb_id": "test-kb"},
                    )
                    assert resp.status_code == 400

            _run(_go())

    def test_upload_success(self):
        store = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(return_value={"lexical_weights": [{}]})
        collections = MagicMock()
        collections.ensure_collection = AsyncMock()
        collections.get_collection_name = MagicMock(return_value="kb_test")
        kb_registry = AsyncMock()
        kb_registry.get_kb = AsyncMock(return_value={"kb_id": "test-kb"})
        state = _mock_state(
            qdrant_store=store,
            embedder=embedder,
            qdrant_collections=collections,
            kb_registry=kb_registry,
        )

        with patch("src.api.app._get_state", return_value=state), \
             patch("src.api.routes.rag.create_job", new_callable=AsyncMock, return_value="job-123"), \
             patch("src.api.routes.rag.asyncio") as mock_asyncio, \
             patch("src.pipeline.ingestion.IngestionPipeline", autospec=True), \
             patch("src.api.routes.ingest._OnnxSparseEmbedder"):
            # Mock asyncio.create_task to not actually start bg task
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_asyncio.create_task = MagicMock(return_value=mock_task)
            # Mock asyncio.to_thread to execute the callable synchronously
            mock_asyncio.to_thread = AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))

            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        files={"file": ("test.txt", b"hello world", "text/plain")},
                        data={"kb_id": "test-kb"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["success"] is True
                    assert data["job_id"] == "job-123"
                    assert data["kb_id"] == "test-kb"

            _run(_go())


# ============================================================================
# POST /api/v1/knowledge/reingest-from-jsonl
# ============================================================================
class TestReingestFromJsonl:
    def test_no_services(self):
        state = _mock_state()
        with patch("src.api.app._get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/reingest-from-jsonl",
                        data={"kb_id": "test-kb"},
                    )
                    assert resp.status_code == 503

            _run(_go())

    def test_invalid_path(self):
        store = AsyncMock()
        embedder = MagicMock()
        state = _mock_state(qdrant_store=store, embedder=embedder)
        with patch("src.api.app._get_state", return_value=state), \
             patch.dict("os.environ", {"KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR": "/tmp/knowledge-local"}):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/reingest-from-jsonl",
                        data={"kb_id": "test-kb", "jsonl_path": "/etc/passwd"},
                    )
                    assert resp.status_code == 400

            _run(_go())


# ============================================================================
# _stage1_parse_to_jsonl (direct unit test)
# ============================================================================
class TestStage1Parse:
    def test_cancelled_early(self):
        async def _go():
            with patch("src.api.routes.rag.is_cancelled", new_callable=AsyncMock, return_value=True), \
                 patch("src.pipeline.jsonl_checkpoint.get_jsonl_path", return_value="/tmp/test.jsonl"), \
                 patch("src.pipeline.jsonl_checkpoint.get_already_parsed_ids", return_value=set()), \
                 patch("src.pipeline.jsonl_checkpoint.JsonlCheckpointWriter") as mock_writer:
                mock_ctx = MagicMock()
                mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
                mock_ctx.__exit__ = MagicMock(return_value=False)
                mock_writer.return_value = mock_ctx

                jsonl_path, errors = await rag_mod._stage1_parse_to_jsonl(
                    "job-1", [("file.txt", "/tmp/file.txt")], "test-kb",
                )
                assert jsonl_path == "/tmp/test.jsonl"
                assert errors == []

        _run(_go())


# ============================================================================
# _stage2_ingest_from_jsonl (direct unit test)
# ============================================================================
class TestStage2Ingest:
    def test_cancelled_early(self):
        async def _go():
            with patch("src.api.routes.rag.is_cancelled", new_callable=AsyncMock, return_value=True), \
                 patch("src.pipeline.jsonl_checkpoint.JsonlCheckpointReader") as mock_reader:
                mock_reader_inst = MagicMock()
                mock_reader_inst.__iter__ = MagicMock(return_value=iter([]))
                mock_reader.return_value = mock_reader_inst

                pipeline = AsyncMock()
                total_docs, total_chunks, errors = await rag_mod._stage2_ingest_from_jsonl(
                    "job-1", "/tmp/test.jsonl", pipeline, "test-kb",
                )
                assert total_docs == 0
                assert total_chunks == 0

        _run(_go())

    def test_ingest_records(self):
        async def _go():
            record = MagicMock()
            record.doc_id = "doc1"
            record.filename = "test.txt"
            parse_result = MagicMock()
            parse_result.full_text = "hello world"

            ingest_result = MagicMock()
            ingest_result.chunks_stored = 5

            pipeline = AsyncMock()
            pipeline.ingest = AsyncMock(return_value=ingest_result)

            with patch("src.api.routes.rag.is_cancelled", new_callable=AsyncMock, return_value=False), \
                 patch("src.pipeline.jsonl_checkpoint.JsonlCheckpointReader") as mock_reader, \
                 patch("src.api.routes.rag.update_job", new_callable=AsyncMock), \
                 patch("src.domain.models.RawDocument") as mock_raw:
                mock_reader_inst = MagicMock()
                mock_reader_inst.__iter__ = MagicMock(return_value=iter([(record, parse_result)]))
                mock_reader.return_value = mock_reader_inst

                total_docs, total_chunks, errors = await rag_mod._stage2_ingest_from_jsonl(
                    "job-1", "/tmp/test.jsonl", pipeline, "test-kb",
                )
                assert total_docs == 1
                assert total_chunks == 5
                assert errors == []

        _run(_go())


# ============================================================================
# _process_files (direct unit test)
# ============================================================================
class TestProcessFiles:
    def test_process_files_completed(self):
        async def _go():
            with patch.object(rag_mod, "_stage1_parse_to_jsonl", new_callable=AsyncMock, return_value=("/tmp/t.jsonl", [])), \
                 patch.object(rag_mod, "_stage2_ingest_from_jsonl", new_callable=AsyncMock, return_value=(2, 10, [])), \
                 patch("src.api.routes.rag.is_cancelled", new_callable=AsyncMock, return_value=False), \
                 patch("src.api.routes.rag.update_job", new_callable=AsyncMock) as mock_update, \
                 patch("src.api.routes.rag.metrics_inc"), \
                 patch("src.api.app._get_state") as mock_gs:
                mock_gs.return_value = _mock_state()

                await rag_mod._process_files("job-1", [("f.txt", "/tmp/f.txt")], MagicMock(), "kb1")
                # Should be called with status="completed"
                calls = mock_update.call_args_list
                final_call = calls[-1]
                assert final_call.kwargs.get("status") == "completed"

        _run(_go())

    def test_process_files_failed(self):
        async def _go():
            with patch.object(rag_mod, "_stage1_parse_to_jsonl", new_callable=AsyncMock, return_value=("/tmp/t.jsonl", ["err"])), \
                 patch.object(rag_mod, "_stage2_ingest_from_jsonl", new_callable=AsyncMock, return_value=(0, 0, [])), \
                 patch("src.api.routes.rag.is_cancelled", new_callable=AsyncMock, return_value=False), \
                 patch("src.api.routes.rag.update_job", new_callable=AsyncMock) as mock_update, \
                 patch("src.api.routes.rag.metrics_inc"), \
                 patch("src.api.app._get_state") as mock_gs:
                mock_gs.return_value = _mock_state()

                await rag_mod._process_files("job-1", [], MagicMock(), "kb1")
                calls = mock_update.call_args_list
                final_call = calls[-1]
                assert final_call.kwargs.get("status") == "failed"

        _run(_go())


# ============================================================================
# POST /api/v1/rag-query (alias)
# ============================================================================
class TestRagQueryAlias:
    def test_alias_returns_same_as_ask(self):
        state = _mock_state()
        with patch("src.api.app._get_state", return_value=state):
            app = _make_app()

            async def _go():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    resp = await ac.post(
                        "/api/v1/rag-query",
                        json={"query": "test question"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["query"] == "test question"
                    assert data["answer"] is None

            _run(_go())
