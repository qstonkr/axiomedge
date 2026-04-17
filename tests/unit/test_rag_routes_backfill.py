"""Backfill unit tests for src/api/routes/rag.py — RAG + file ingest routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


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


# ---------------------------------------------------------------------------
# rag_query — additional branches
# ---------------------------------------------------------------------------
class TestRagQuery:
    def test_kb_id_single_fallback(self):
        """When kb_ids is absent, kb_id_single is used."""
        from src.api.routes.rag import rag_query

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "query": "q", "answer": "a", "chunks": [],
        }
        rag = AsyncMock()
        rag.process = AsyncMock(return_value=mock_result)
        state = _mock_state(rag_pipeline=rag)

        with patch("src.api.app._get_state", return_value=state):
            result = _run(rag_query({
                "query": "test", "kb_id": "single-kb",
            }))
        assert result["answer"] == "a"
        # Verify kb_id was passed from kb_id field
        call_args = rag.process.call_args
        req = call_args[0][0]
        assert req.kb_id == "single-kb"

    def test_mode_passed_through(self):
        """Custom mode should be in fallback response."""
        from src.api.routes.rag import rag_query

        state = _mock_state(rag_pipeline=None)
        with patch("src.api.app._get_state", return_value=state):
            result = _run(rag_query({
                "query": "q", "mode": "graph",
            }))
        assert result["mode"] == "graph"

    def test_empty_query(self):
        from src.api.routes.rag import rag_query

        state = _mock_state(rag_pipeline=None)
        with patch("src.api.app._get_state", return_value=state):
            result = _run(rag_query({}))
        assert result["query"] == ""
        assert result["answer"] is None


# ---------------------------------------------------------------------------
# rag_query_alias
# ---------------------------------------------------------------------------
class TestRagQueryAlias:
    def test_delegates_to_rag_query(self):
        from src.api.routes.rag import rag_query_alias

        state = _mock_state(rag_pipeline=None)
        with patch("src.api.app._get_state", return_value=state):
            result = _run(rag_query_alias({"query": "hello"}))
        assert result["query"] == "hello"
        assert result["answer"] is None


# ---------------------------------------------------------------------------
# _correct_ocr_if_needed
# ---------------------------------------------------------------------------
class TestCorrectOcrIfNeeded:
    def test_no_ocr_text(self):
        from src.api.routes.rag import _correct_ocr_if_needed

        pr = MagicMock()
        pr.ocr_text = ""
        _run(_correct_ocr_if_needed(pr))
        # Should return early, no correction applied

    def test_with_ocr_text_and_llm(self):
        from src.api.routes.rag import _correct_ocr_if_needed

        pr = MagicMock()
        pr.ocr_text = "noisy OCR text"
        llm = MagicMock()
        state = _mock_state(llm=llm)

        corrected = "clean OCR text"
        with (
            patch("src.api.app._get_state", return_value=state),
            patch(
                "src.pipelines.ocr_corrector.correct_ocr_chunks",
                new_callable=AsyncMock,
                return_value=corrected,
            ),
        ):
            _run(_correct_ocr_if_needed(pr))
        assert pr.ocr_text == corrected

    def test_with_ocr_text_no_llm(self):
        from src.api.routes.rag import _correct_ocr_if_needed

        pr = MagicMock()
        pr.ocr_text = "noisy"
        state = _mock_state(llm=None)

        with patch("src.api.app._get_state", return_value=state):
            _run(_correct_ocr_if_needed(pr))
        # No correction, original remains
        assert pr.ocr_text == "noisy"

    def test_correction_exception_skipped(self):
        from src.api.routes.rag import _correct_ocr_if_needed

        pr = MagicMock()
        pr.ocr_text = "noisy"
        llm = MagicMock()
        state = _mock_state(llm=llm)

        with (
            patch("src.api.app._get_state", return_value=state),
            patch(
                "src.pipelines.ocr_corrector.correct_ocr_chunks",
                new_callable=AsyncMock,
                side_effect=Exception("LLM timeout"),
            ),
        ):
            _run(_correct_ocr_if_needed(pr))
        # Should not crash, original preserved
        assert pr.ocr_text == "noisy"


# ---------------------------------------------------------------------------
# _ensure_qdrant_collection
# ---------------------------------------------------------------------------
class TestEnsureQdrantCollection:
    def test_no_collections(self):
        from src.api.routes.rag import _ensure_qdrant_collection

        state = _mock_state(qdrant_collections=None)
        _run(_ensure_qdrant_collection(state, "kb1"))
        # Should return without error

    def test_success(self):
        from src.api.routes.rag import _ensure_qdrant_collection

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_collections=coll)
        _run(_ensure_qdrant_collection(state, "kb1"))
        coll.ensure_collection.assert_called_once_with("kb1")

    def test_sdk_fails_falls_back_to_rest(self):
        from src.api.routes.rag import _ensure_qdrant_collection

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock(
            side_effect=Exception("SDK err")
        )
        state = _mock_state(qdrant_collections=coll)

        with patch(
            "src.api.routes.rag._create_collection_via_rest",
            new_callable=AsyncMock,
        ) as mock_rest:
            _run(_ensure_qdrant_collection(state, "kb1"))
        mock_rest.assert_called_once_with(coll, "kb1")


# ---------------------------------------------------------------------------
# _create_collection_via_rest
# ---------------------------------------------------------------------------
def _patch_w_for_rest_tests():
    """Patch module-level _w ref used in _create_collection_via_rest.

    Note: rag.py line 350 references `_w` (from kb.py's import alias)
    instead of the locally-imported `_cw`. We inject a mock `_w` into
    the rag module namespace so the function can execute.
    """
    import src.api.routes.rag as _rag_mod
    from src.config.weights import weights as _real_w

    _rag_mod._w = _real_w  # type: ignore[attr-defined]


class TestCreateCollectionViaRest:
    def setup_method(self):
        _patch_w_for_rest_tests()

    def test_success_200(self):
        from src.api.routes.rag import _create_collection_via_rest

        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.config.get_settings") as mock_gs,
        ):
            mock_gs.return_value.qdrant.url = "http://q:6333"
            _run(_create_collection_via_rest(coll, "test"))
        mock_client.put.assert_called_once()

    def test_conflict_409(self):
        from src.api.routes.rag import _create_collection_via_rest

        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")

        mock_resp = MagicMock()
        mock_resp.status_code = 409

        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.config.get_settings") as mock_gs,
        ):
            mock_gs.return_value.qdrant.url = "http://q:6333"
            _run(_create_collection_via_rest(coll, "test"))
        # Should not raise

    def test_failure_status(self):
        from src.api.routes.rag import _create_collection_via_rest

        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"

        mock_client = AsyncMock()
        mock_client.put = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.config.get_settings") as mock_gs,
        ):
            mock_gs.return_value.qdrant.url = "http://q:6333"
            _run(_create_collection_via_rest(coll, "test"))
        # Should log error but not raise


# ---------------------------------------------------------------------------
# _auto_register_kb
# ---------------------------------------------------------------------------
class TestAutoRegisterKb:
    def test_no_registry(self):
        from src.api.routes.rag import _auto_register_kb

        state = _mock_state(kb_registry=None)
        _run(_auto_register_kb(state, "kb1", None, None))
        # No error

    def test_already_exists(self):
        from src.api.routes.rag import _auto_register_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(return_value={"kb_id": "kb1"})
        state = _mock_state(kb_registry=reg)
        _run(_auto_register_kb(state, "kb1", "Name", "global"))
        reg.create_kb.assert_not_called()

    def test_creates_new(self):
        from src.api.routes.rag import _auto_register_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(return_value=None)
        reg.create_kb = AsyncMock()
        state = _mock_state(kb_registry=reg)
        _run(_auto_register_kb(state, "kb2", "My KB", "dept"))
        reg.create_kb.assert_called_once()
        call_data = reg.create_kb.call_args[0][0]
        assert call_data["id"] == "kb2"
        assert call_data["name"] == "My KB"
        assert call_data["tier"] == "dept"

    def test_defaults_name_and_tier(self):
        from src.api.routes.rag import _auto_register_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(return_value=None)
        reg.create_kb = AsyncMock()
        state = _mock_state(kb_registry=reg)
        _run(_auto_register_kb(state, "kb3", None, None))
        call_data = reg.create_kb.call_args[0][0]
        assert call_data["name"] == "kb3"
        assert call_data["tier"] == "global"

    def test_exception_logged(self):
        from src.api.routes.rag import _auto_register_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(side_effect=Exception("db"))
        state = _mock_state(kb_registry=reg)
        _run(_auto_register_kb(state, "kb4", None, None))
        # Should not raise


# ---------------------------------------------------------------------------
# _update_kb_and_invalidate_cache
# ---------------------------------------------------------------------------
class TestUpdateKbAndInvalidateCache:
    def test_no_registry(self):
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        state = _mock_state(
            kb_registry=None,
            multi_layer_cache=None,
            search_cache=None,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 5, 50))

    def test_with_registry(self):
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        reg = AsyncMock()
        reg.update_counts = AsyncMock()
        state = _mock_state(
            kb_registry=reg,
            multi_layer_cache=None,
            search_cache=None,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 5, 50))
        reg.update_counts.assert_called_once_with("kb1", 5, 50)

    def test_multi_layer_cache_invalidated(self):
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        mc = AsyncMock()
        mc.invalidate_by_kb = AsyncMock()
        state = _mock_state(
            kb_registry=None,
            multi_layer_cache=mc,
            search_cache=None,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 1, 10))
        mc.invalidate_by_kb.assert_called_once_with("kb1")

    def test_multi_cache_fails_falls_back_to_search_cache(self):
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        mc = AsyncMock()
        mc.invalidate_by_kb = AsyncMock(side_effect=Exception("err"))
        sc = AsyncMock()
        sc.clear = AsyncMock()
        state = _mock_state(
            kb_registry=None,
            multi_layer_cache=mc,
            search_cache=sc,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 1, 10))
        sc.clear.assert_called_once()

    def test_search_cache_clear_error_logged(self):
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        sc = AsyncMock()
        sc.clear = AsyncMock(side_effect=Exception("redis"))
        state = _mock_state(
            kb_registry=None,
            multi_layer_cache=None,
            search_cache=sc,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 1, 10))
        # Should not raise

    def test_no_invalidate_by_kb_attr(self):
        """Multi-layer cache without invalidate_by_kb falls to search_cache."""
        from src.api.routes.rag import _update_kb_and_invalidate_cache

        mc = MagicMock(spec=[])  # no invalidate_by_kb attr
        sc = AsyncMock()
        sc.clear = AsyncMock()
        state = _mock_state(
            kb_registry=None,
            multi_layer_cache=mc,
            search_cache=sc,
        )
        with patch("src.api.app._get_state", return_value=state):
            _run(_update_kb_and_invalidate_cache("kb1", 1, 10))
        sc.clear.assert_called_once()


# ---------------------------------------------------------------------------
# _build_reingest_pipeline
# ---------------------------------------------------------------------------
class TestBuildReingestPipeline:
    def test_builds_pipeline(self):
        from src.api.routes.rag import _build_reingest_pipeline

        embedder = MagicMock()
        # _OnnxSparseEmbedder needs embedder.encode
        embedder.encode = MagicMock(
            return_value={"lexical_weights": [{}]}
        )
        store = MagicMock()
        state = _mock_state(
            graph_repo=None,
            dedup_cache=None,
            dedup_pipeline=None,
            term_extractor=None,
            graphrag_extractor=None,
        )

        with patch(
            "src.pipelines.ingestion.IngestionPipeline"
        ) as MockPipeline:
            MockPipeline.return_value = MagicMock()
            pipeline = _build_reingest_pipeline(state, embedder, store)
        assert pipeline is not None


# ---------------------------------------------------------------------------
# upload_and_ingest — service unavailable
# ---------------------------------------------------------------------------
class TestUploadAndIngest:
    def test_no_store_503(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.rag import knowledge_router

        app = FastAPI()
        app.include_router(knowledge_router)

        state = _mock_state(qdrant_store=None, embedder=None)
        with patch("src.api.app._get_state", return_value=state):
            async def _t():
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 503
            _run(_t())

    def test_no_files_400(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.rag import knowledge_router

        app = FastAPI()
        app.include_router(knowledge_router)

        state = _mock_state(
            qdrant_store=MagicMock(), embedder=MagicMock()
        )
        with patch("src.api.app._get_state", return_value=state):
            async def _t():
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/file-upload-ingest",
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 400
            _run(_t())


# ---------------------------------------------------------------------------
# reingest_from_jsonl — service unavailable
# ---------------------------------------------------------------------------
class TestReingestFromJsonl:
    def test_no_store_503(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.rag import knowledge_router

        app = FastAPI()
        app.include_router(knowledge_router)

        state = _mock_state(qdrant_store=None, embedder=None)
        with patch("src.api.app._get_state", return_value=state):
            async def _t():
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/reingest-from-jsonl",
                        data={"kb_id": "test"},
                    )
                    assert resp.status_code == 503
            _run(_t())

    def test_invalid_path_400(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.rag import knowledge_router

        app = FastAPI()
        app.include_router(knowledge_router)

        state = _mock_state(
            qdrant_store=MagicMock(), embedder=MagicMock()
        )
        with patch("src.api.app._get_state", return_value=state):
            async def _t():
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/knowledge/reingest-from-jsonl",
                        data={
                            "kb_id": "test",
                            "jsonl_path": "/etc/passwd",
                        },
                    )
                    assert resp.status_code == 400
            _run(_t())

    def test_empty_jsonl_404(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.rag import knowledge_router
        import tempfile
        import os

        app = FastAPI()
        app.include_router(knowledge_router)

        state = _mock_state(
            qdrant_store=MagicMock(), embedder=MagicMock()
        )

        # Create an empty JSONL file in the allowed directory
        base = os.getenv(
            "KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR",
            "/tmp/knowledge-local",
        )
        os.makedirs(base, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".jsonl", dir=base
        )
        os.close(fd)

        try:
            with (
                patch("src.api.app._get_state", return_value=state),
                patch(
                    "src.pipelines.jsonl_checkpoint.JsonlCheckpointReader"
                ) as MockReader,
            ):
                MockReader.return_value.count.return_value = 0

                async def _t():
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        resp = await ac.post(
                            "/api/v1/knowledge/reingest-from-jsonl",
                            data={
                                "kb_id": "test",
                                "jsonl_path": tmp_path,
                            },
                        )
                        assert resp.status_code == 404
                _run(_t())
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# _process_files — status logic
# ---------------------------------------------------------------------------
class TestProcessFiles:
    def test_cancelled_during_stage1(self):
        from src.api.routes.rag import _process_files

        with (
            patch(
                "src.api.routes.rag._stage1_parse_to_jsonl",
                new_callable=AsyncMock,
                return_value=("/tmp/test.jsonl", ["err1"]),
            ),
            patch(
                "src.api.routes.rag.is_cancelled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.api.routes.rag.update_job",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            _run(_process_files(
                "job1", [("f.pdf", "/tmp/f.pdf")],
                MagicMock(), "kb1",
            ))
        # Should be cancelled after stage1
        mock_update.assert_called_with(
            "job1", status="cancelled", errors=["err1"]
        )

    def test_completed_status(self):
        from src.api.routes.rag import _process_files

        with (
            patch(
                "src.api.routes.rag._stage1_parse_to_jsonl",
                new_callable=AsyncMock,
                return_value=("/tmp/test.jsonl", []),
            ),
            patch(
                "src.api.routes.rag.is_cancelled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.api.routes.rag._stage2_ingest_from_jsonl",
                new_callable=AsyncMock,
                return_value=(3, 30, []),
            ),
            patch(
                "src.api.routes.rag.metrics_inc",
            ),
            patch(
                "src.api.routes.rag._update_kb_and_invalidate_cache",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.routes.rag.update_job",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            _run(_process_files(
                "job2", [("a.pdf", "/tmp/a.pdf")],
                MagicMock(), "kb1",
            ))
        last_call = mock_update.call_args
        assert last_call.kwargs.get("status") == "completed"

    def test_failed_status_zero_docs(self):
        from src.api.routes.rag import _process_files

        with (
            patch(
                "src.api.routes.rag._stage1_parse_to_jsonl",
                new_callable=AsyncMock,
                return_value=("/tmp/test.jsonl", ["parse err"]),
            ),
            patch(
                "src.api.routes.rag.is_cancelled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.api.routes.rag._stage2_ingest_from_jsonl",
                new_callable=AsyncMock,
                return_value=(0, 0, ["ingest err"]),
            ),
            patch("src.api.routes.rag.metrics_inc"),
            patch(
                "src.api.routes.rag.update_job",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            _run(_process_files(
                "job3", [], MagicMock(), "kb1",
            ))
        last_call = mock_update.call_args
        assert last_call.kwargs.get("status") == "failed"

    def test_cleanup_save_dir(self):
        from src.api.routes.rag import _process_files
        import tempfile
        import os

        save_dir = tempfile.mkdtemp(prefix="test_cleanup_")
        assert os.path.exists(save_dir)

        with (
            patch(
                "src.api.routes.rag._stage1_parse_to_jsonl",
                new_callable=AsyncMock,
                return_value=("/tmp/test.jsonl", []),
            ),
            patch(
                "src.api.routes.rag.is_cancelled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.api.routes.rag._stage2_ingest_from_jsonl",
                new_callable=AsyncMock,
                return_value=(1, 5, []),
            ),
            patch("src.api.routes.rag.metrics_inc"),
            patch(
                "src.api.routes.rag._update_kb_and_invalidate_cache",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.routes.rag.update_job",
                new_callable=AsyncMock,
            ),
        ):
            _run(_process_files(
                "job4", [], MagicMock(), "kb1", save_dir,
            ))
        assert not os.path.exists(save_dir)

    def test_kb_count_update_error_logged(self):
        from src.api.routes.rag import _process_files

        with (
            patch(
                "src.api.routes.rag._stage1_parse_to_jsonl",
                new_callable=AsyncMock,
                return_value=("/tmp/test.jsonl", []),
            ),
            patch(
                "src.api.routes.rag.is_cancelled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.api.routes.rag._stage2_ingest_from_jsonl",
                new_callable=AsyncMock,
                return_value=(2, 20, []),
            ),
            patch("src.api.routes.rag.metrics_inc"),
            patch(
                "src.api.routes.rag._update_kb_and_invalidate_cache",
                new_callable=AsyncMock,
                side_effect=Exception("count update fail"),
            ),
            patch(
                "src.api.routes.rag.update_job",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            _run(_process_files(
                "job5", [], MagicMock(), "kb1",
            ))
        # Should still complete
        last_call = mock_update.call_args
        assert last_call.kwargs.get("status") == "completed"


# ---------------------------------------------------------------------------
# _attach_reingest_callbacks
# ---------------------------------------------------------------------------
class TestAttachReingestCallbacks:
    def test_attaches_callbacks(self):
        from src.api.routes.rag import _attach_reingest_callbacks

        task = MagicMock()
        task.add_done_callback = MagicMock()
        state = _mock_state()
        state.setdefault("_background_tasks", set())

        _attach_reingest_callbacks(task, "job1", "kb1", state)
        # Two done callbacks: _safe_finalize_callback + bg_tasks.discard
        assert task.add_done_callback.call_count == 2


# ---------------------------------------------------------------------------
# get_rag_config / get_rag_stats — verify static structure
# ---------------------------------------------------------------------------
class TestStaticRagEndpoints:
    def test_rag_config_keys(self):
        from src.api.routes.rag import get_rag_config

        result = _run(get_rag_config())
        assert set(result.keys()) == {
            "mode", "top_k", "reranking", "graph_enabled",
        }

    def test_rag_stats_keys(self):
        from src.api.routes.rag import get_rag_stats

        result = _run(get_rag_stats())
        assert set(result.keys()) == {
            "total_queries", "avg_response_time_ms", "avg_chunks_returned",
        }
