"""Tests for data source sync: trigger, crawl, ingest pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import pytest

from src.api.routes.data_source_sync import (
    _extract_page_id_from_url,
    run_data_source_sync,
)


# ---------------------------------------------------------------------------
# _extract_page_id_from_url
# ---------------------------------------------------------------------------

class TestExtractPageId:
    def test_pages_path(self):
        url = "https://wiki.gsretail.com/spaces/DXbot/pages/373865276/Title"
        assert _extract_page_id_from_url(url) == "373865276"

    def test_page_id_query_param(self):
        url = "https://wiki.gsretail.com/pages/viewpage.action?pageId=12345"
        assert _extract_page_id_from_url(url) == "12345"

    def test_encoded_url(self):
        url = "https://wiki.gsretail.com/spaces/DXbot/pages/388722996/AX+FAQ"
        assert _extract_page_id_from_url(url) == "388722996"

    def test_no_page_id(self):
        assert _extract_page_id_from_url("https://example.com") is None

    def test_empty_string(self):
        assert _extract_page_id_from_url("") is None


# ---------------------------------------------------------------------------
# run_data_source_sync
# ---------------------------------------------------------------------------

class TestRunDataSourceSync:
    @pytest.fixture()
    def mock_state(self):
        return {
            "data_source_repo": AsyncMock(),
            "ingestion_run_repo": AsyncMock(),
            "qdrant_store": MagicMock(),
            "embedder": MagicMock(),
            "qdrant_collections": AsyncMock(),
            "graph_repo": MagicMock(),
            "dedup_cache": None,
            "dedup_pipeline": None,
            "term_extractor": None,
            "graphrag_extractor": None,
            "kb_registry": AsyncMock(),
        }

    @pytest.fixture(autouse=True)
    def _mock_pat(self):
        with patch("src.api.routes.data_source_sync._CONFLUENCE_PAT", "test-pat"):
            yield

    @pytest.fixture()
    def source(self):
        return {
            "id": "test-source-id",
            "name": "test_wiki",
            "kb_id": "test-kb",
            "source_type": "crawl_result",
            "metadata": {
                "url": "https://wiki.gsretail.com/spaces/DXbot/pages/373865276/Home",
                "root_page_id": "373865276",
            },
            "crawl_config": {},
        }

    @pytest.mark.asyncio
    async def test_missing_page_id_sets_error(self, mock_state):
        source = {
            "id": "src-1",
            "name": "no-page",
            "kb_id": "kb-1",
            "metadata": {},
            "crawl_config": {},
        }
        await run_data_source_sync(source, mock_state)
        mock_state["data_source_repo"].complete_sync.assert_called_once()
        call_args = mock_state["data_source_repo"].complete_sync.call_args
        assert call_args[0][1] == "error"  # status = error

    @pytest.mark.asyncio
    async def test_extracts_page_id_from_metadata_url(self, mock_state, source):
        # Remove explicit root_page_id to test URL extraction
        source["metadata"].pop("root_page_id")

        with (
            patch("src.connectors.confluence.crawl_space") as mock_crawl,
            patch("src.connectors.confluence.save_results"),
            patch("src.connectors.crawl_result.CrawlResultConnector"),
        ):
            mock_crawl.side_effect = RuntimeError("test stop")
            await run_data_source_sync(source, mock_state)

        # Should have attempted crawler with extracted page_id
        mock_crawl.assert_called_once()
        assert mock_crawl.call_args[1]["page_id"] == "373865276"

    @pytest.mark.asyncio
    async def test_crawler_failure_sets_error_status(self, mock_state, source):
        with patch("src.connectors.confluence.crawl_space") as mock_crawl:
            mock_crawl.side_effect = RuntimeError("crawler crashed")
            await run_data_source_sync(source, mock_state)

        ds_repo = mock_state["data_source_repo"]
        ds_repo.complete_sync.assert_called_once()
        assert ds_repo.complete_sync.call_args[0][1] == "error"

    @pytest.mark.asyncio
    async def test_successful_crawl_and_ingest(self, mock_state, source):
        from src.connectors.confluence.models import CrawlSpaceResult
        from src.domain.models import RawDocument, ConnectorResult

        fake_crawl_result = CrawlSpaceResult(pages=[], page_dicts=[])
        fake_docs = [
            RawDocument(
                doc_id="p1", title="Page 1", content="content 1",
                source_uri="http://wiki/p1",
            ),
            RawDocument(
                doc_id="p2", title="Page 2", content="content 2",
                source_uri="http://wiki/p2",
            ),
        ]
        fake_connector_result = ConnectorResult(
            success=True, source_type="crawl_result",
            documents=fake_docs, version_fingerprint="abc123",
        )

        with (
            patch("src.connectors.confluence.crawl_space", return_value=fake_crawl_result),
            patch("src.connectors.confluence.save_results"),
            patch("src.connectors.crawl_result.CrawlResultConnector") as MockConnector,
            patch("src.pipeline.ingestion.IngestionPipeline") as MockPipeline,
        ):
            connector_instance = AsyncMock()
            connector_instance.fetch.return_value = fake_connector_result
            MockConnector.return_value = connector_instance

            pipeline_instance = AsyncMock()
            ingest_result = MagicMock()
            ingest_result.chunks_stored = 5
            pipeline_instance.ingest.return_value = ingest_result
            MockPipeline.return_value = pipeline_instance

            await run_data_source_sync(source, mock_state)

        assert pipeline_instance.ingest.call_count == 2

        ds_repo = mock_state["data_source_repo"]
        ds_repo.complete_sync.assert_called_once()
        call_args = ds_repo.complete_sync.call_args
        assert call_args[0][1] == "active"
        sync_result = call_args[1]["sync_result"]
        assert sync_result["documents_synced"] == 2
        assert sync_result["chunks_stored"] == 10

    @pytest.mark.asyncio
    async def test_no_documents_still_completes(self, mock_state, source):
        from src.connectors.confluence.models import CrawlSpaceResult
        from src.domain.models import ConnectorResult

        fake_crawl_result = CrawlSpaceResult(pages=[], page_dicts=[])
        empty_result = ConnectorResult(
            success=True, source_type="crawl_result",
            documents=[], version_fingerprint="empty",
        )

        with (
            patch("src.connectors.confluence.crawl_space", return_value=fake_crawl_result),
            patch("src.connectors.confluence.save_results"),
            patch("src.connectors.crawl_result.CrawlResultConnector") as MockConnector,
        ):
            connector_instance = AsyncMock()
            connector_instance.fetch.return_value = empty_result
            MockConnector.return_value = connector_instance

            await run_data_source_sync(source, mock_state)

        ds_repo = mock_state["data_source_repo"]
        ds_repo.complete_sync.assert_called_once()
        sync_result = ds_repo.complete_sync.call_args[1]["sync_result"]
        assert sync_result["documents_synced"] == 0


# ---------------------------------------------------------------------------
# OCR EC2 lifecycle
# ---------------------------------------------------------------------------

class TestOCRInstanceLifecycle:
    @pytest.mark.asyncio
    async def test_start_ocr_returns_none_when_no_instance_id(self):
        import src.api.routes.data_source_sync as _sync_mod
        orig_id = _sync_mod._PADDLEOCR_INSTANCE_ID
        orig_url = _sync_mod._PADDLEOCR_API_URL
        try:
            _sync_mod._PADDLEOCR_INSTANCE_ID = ""
            _sync_mod._PADDLEOCR_API_URL = ""
            result = await _sync_mod._start_ocr_instance()
            assert result is None
        finally:
            _sync_mod._PADDLEOCR_INSTANCE_ID = orig_id
            _sync_mod._PADDLEOCR_API_URL = orig_url

    @pytest.mark.asyncio
    async def test_start_ocr_already_running(self):
        from src.api.routes.data_source_sync import _start_ocr_instance

        with (
            patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", "i-123"),
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="running",
            ),
            patch(
                "src.api.routes.data_source_sync._get_instance_ip",
                return_value="1.2.3.4",
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_health",
                return_value=True,
            ),
        ):
            result = await _start_ocr_instance()
            assert result == "http://1.2.3.4:8866"

    @pytest.mark.asyncio
    async def test_stop_ocr_noop_when_no_instance_id(self):
        from src.api.routes.data_source_sync import _stop_ocr_instance

        with patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", ""):
            await _stop_ocr_instance()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_ocr_calls_aws(self):
        from src.api.routes.data_source_sync import _stop_ocr_instance

        with (
            patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", "i-123"),
            patch(
                "src.api.routes.data_source_sync.asyncio.create_subprocess_shell"
            ) as mock_proc,
        ):
            proc = AsyncMock()
            proc.communicate.return_value = (b"ok", b"")
            mock_proc.return_value = proc

            await _stop_ocr_instance()
            mock_proc.assert_called_once()
            assert "stop-instances" in mock_proc.call_args[0][0]


# ---------------------------------------------------------------------------
# get_active_job_count (jobs.py)
# ---------------------------------------------------------------------------

class TestGetActiveJobCount:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_redis(self):
        from src.api.routes.jobs import get_active_job_count

        with patch("src.api.routes.jobs._get_redis", side_effect=Exception("no redis")):
            count = await get_active_job_count()
            assert count == 0


# ---------------------------------------------------------------------------
# DataSourceRepository.complete_sync
# ---------------------------------------------------------------------------

class TestDataSourceRepoCompleteSync:
    @pytest.mark.asyncio
    async def test_complete_sync_updates_fields(self):
        """Verify complete_sync calls update with correct fields."""
        from src.stores.postgres.repositories.data_source import DataSourceRepository

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        repo = DataSourceRepository.__new__(DataSourceRepository)
        repo._get_session = AsyncMock(return_value=mock_session)

        await repo.complete_sync(
            "src-1", "active",
            sync_result={"documents_synced": 5},
        )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_page_id
# ---------------------------------------------------------------------------

class TestResolvePageId:
    def test_from_metadata_root_page_id(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {"metadata": {"root_page_id": "111"}, "crawl_config": {}}
        assert _resolve_page_id(source) == "111"

    def test_from_crawl_config(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {"metadata": {}, "crawl_config": {"root_page_id": "222"}}
        assert _resolve_page_id(source) == "222"

    def test_from_url_fallback(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {
            "metadata": {"url": "https://wiki.example.com/pages/333/Page"},
            "crawl_config": {},
        }
        assert _resolve_page_id(source) == "333"

    def test_raises_when_missing(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {"metadata": {}, "crawl_config": {}}
        with pytest.raises(ValueError, match="No page_id"):
            _resolve_page_id(source)

    def test_none_metadata(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {"metadata": None, "crawl_config": {}}
        with pytest.raises(ValueError, match="No page_id"):
            _resolve_page_id(source)

    def test_metadata_root_page_id_takes_precedence_over_url(self):
        from src.api.routes.data_source_sync import _resolve_page_id

        source = {
            "metadata": {
                "root_page_id": "444",
                "url": "https://wiki.example.com/pages/555/Page",
            },
            "crawl_config": {},
        }
        assert _resolve_page_id(source) == "444"


# ---------------------------------------------------------------------------
# _resolve_pat
# ---------------------------------------------------------------------------

class TestResolvePat:
    def test_returns_pat_when_set(self):
        from src.api.routes.data_source_sync import _resolve_pat

        with patch("src.api.routes.data_source_sync._CONFLUENCE_PAT", "my-pat"):
            assert _resolve_pat() == "my-pat"

    def test_raises_when_empty(self):
        from src.api.routes.data_source_sync import _resolve_pat

        with patch("src.api.routes.data_source_sync._CONFLUENCE_PAT", ""):
            with pytest.raises(ValueError, match="CONFLUENCE_PAT"):
                _resolve_pat()


# ---------------------------------------------------------------------------
# _wait_for_instance_stopped / _wait_for_instance_running
# ---------------------------------------------------------------------------

class TestWaitForInstanceState:
    @pytest.mark.asyncio
    async def test_wait_for_stopped_succeeds(self):
        from src.api.routes.data_source_sync import _wait_for_instance_stopped

        with (
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                side_effect=["stopping", "stopped"],
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await _wait_for_instance_stopped("i-123", retries=3)

    @pytest.mark.asyncio
    async def test_wait_for_stopped_exhausts_retries(self):
        from src.api.routes.data_source_sync import _wait_for_instance_stopped

        with (
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="stopping",
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            # Should not raise, just return after exhausting retries
            await _wait_for_instance_stopped("i-123", retries=2)

    @pytest.mark.asyncio
    async def test_wait_for_running_succeeds(self):
        from src.api.routes.data_source_sync import _wait_for_instance_running

        with (
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                side_effect=["pending", "running"],
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _wait_for_instance_running("i-123", retries=3)
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_running_timeout(self):
        from src.api.routes.data_source_sync import _wait_for_instance_running

        with (
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="pending",
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _wait_for_instance_running("i-123", retries=2)
            assert result is False


# ---------------------------------------------------------------------------
# _boot_and_resolve_url
# ---------------------------------------------------------------------------

class TestBootAndResolveUrl:
    @pytest.mark.asyncio
    async def test_success(self):
        from src.api.routes.data_source_sync import _boot_and_resolve_url

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")

        with (
            patch(
                "src.api.routes.data_source_sync.asyncio.create_subprocess_shell",
                return_value=mock_proc,
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_instance_running",
                return_value=True,
            ),
            patch(
                "src.api.routes.data_source_sync._get_instance_ip",
                return_value="10.0.0.1",
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_health",
                return_value=True,
            ),
        ):
            url = await _boot_and_resolve_url("i-123")
            assert url == "http://10.0.0.1:8866"

    @pytest.mark.asyncio
    async def test_instance_not_running(self):
        from src.api.routes.data_source_sync import _boot_and_resolve_url

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")

        with (
            patch(
                "src.api.routes.data_source_sync.asyncio.create_subprocess_shell",
                return_value=mock_proc,
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_instance_running",
                return_value=False,
            ),
        ):
            url = await _boot_and_resolve_url("i-123")
            assert url is None

    @pytest.mark.asyncio
    async def test_no_public_ip(self):
        from src.api.routes.data_source_sync import _boot_and_resolve_url

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")

        with (
            patch(
                "src.api.routes.data_source_sync.asyncio.create_subprocess_shell",
                return_value=mock_proc,
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_instance_running",
                return_value=True,
            ),
            patch(
                "src.api.routes.data_source_sync._get_instance_ip",
                return_value=None,
            ),
        ):
            url = await _boot_and_resolve_url("i-123")
            assert url is None

    @pytest.mark.asyncio
    async def test_health_check_timeout(self):
        from src.api.routes.data_source_sync import _boot_and_resolve_url

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok", b"")

        with (
            patch(
                "src.api.routes.data_source_sync.asyncio.create_subprocess_shell",
                return_value=mock_proc,
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_instance_running",
                return_value=True,
            ),
            patch(
                "src.api.routes.data_source_sync._get_instance_ip",
                return_value="10.0.0.1",
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_health",
                return_value=False,
            ),
        ):
            url = await _boot_and_resolve_url("i-123")
            assert url is None


# ---------------------------------------------------------------------------
# _ensure_kb_and_update_counts
# ---------------------------------------------------------------------------

class TestEnsureKbAndUpdateCounts:
    @pytest.mark.asyncio
    async def test_creates_new_kb(self):
        from src.api.routes.data_source_sync import _ensure_kb_and_update_counts

        kb_registry = AsyncMock()
        kb_registry.get_kb.return_value = None
        state = {"kb_registry": kb_registry}

        await _ensure_kb_and_update_counts(
            state, "kb-1", "TestKB", {"description": "test"}, 5, 50,
        )
        kb_registry.create_kb.assert_awaited_once()
        kb_registry.update_counts.assert_awaited_once_with("kb-1", 5, 50)

    @pytest.mark.asyncio
    async def test_updates_existing_kb(self):
        from src.api.routes.data_source_sync import _ensure_kb_and_update_counts

        kb_registry = AsyncMock()
        kb_registry.get_kb.return_value = {"id": "kb-1"}
        state = {"kb_registry": kb_registry}

        await _ensure_kb_and_update_counts(
            state, "kb-1", "TestKB", {}, 3, 30,
        )
        kb_registry.create_kb.assert_not_awaited()
        kb_registry.update_counts.assert_awaited_once_with("kb-1", 3, 30)

    @pytest.mark.asyncio
    async def test_skips_when_no_registry(self):
        from src.api.routes.data_source_sync import _ensure_kb_and_update_counts

        state = {"kb_registry": None}
        # Should not raise
        await _ensure_kb_and_update_counts(state, "kb-1", "Test", {}, 1, 10)

    @pytest.mark.asyncio
    async def test_skips_when_zero_docs(self):
        from src.api.routes.data_source_sync import _ensure_kb_and_update_counts

        kb_registry = AsyncMock()
        state = {"kb_registry": kb_registry}
        await _ensure_kb_and_update_counts(state, "kb-1", "Test", {}, 0, 0)
        kb_registry.get_kb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_registry_exception(self):
        from src.api.routes.data_source_sync import _ensure_kb_and_update_counts

        kb_registry = AsyncMock()
        kb_registry.get_kb.side_effect = Exception("DB error")
        state = {"kb_registry": kb_registry}

        # Should not raise (logs warning)
        await _ensure_kb_and_update_counts(state, "kb-1", "Test", {}, 1, 10)


# ---------------------------------------------------------------------------
# _run_ingestion
# ---------------------------------------------------------------------------

class TestRunIngestion:
    @pytest.mark.asyncio
    async def test_missing_services_raises(self):
        from src.api.routes.data_source_sync import _run_ingestion

        state = {"qdrant_store": None, "embedder": MagicMock()}
        with pytest.raises(RuntimeError, match="not initialized"):
            await _run_ingestion(state, [], "kb-1")

    @pytest.mark.asyncio
    async def test_ingestion_with_mock_pipeline(self):
        from src.api.routes.data_source_sync import _run_ingestion

        mock_doc = MagicMock()
        mock_doc.title = "Doc1"
        mock_doc.metadata = {"file_size_bytes": 1000}

        ingest_result = MagicMock()
        ingest_result.chunks_stored = 3
        ingest_result.success = True

        state = {
            "qdrant_store": MagicMock(),
            "embedder": MagicMock(),
            "qdrant_collections": AsyncMock(),
            "graph_repo": None,
            "dedup_cache": None,
            "dedup_pipeline": None,
            "term_extractor": None,
            "graphrag_extractor": None,
        }

        with patch("src.pipeline.ingestion.IngestionPipeline") as MockPipeline:
            pipeline_instance = AsyncMock()
            pipeline_instance.ingest.return_value = ingest_result
            MockPipeline.return_value = pipeline_instance

            docs_ingested, total_chunks, errors = await _run_ingestion(
                state, [mock_doc], "kb-1",
            )

        assert docs_ingested == 1
        assert total_chunks == 3
        assert errors == []

    @pytest.mark.asyncio
    async def test_ingestion_with_errors(self):
        from src.api.routes.data_source_sync import _run_ingestion

        mock_doc = MagicMock()
        mock_doc.title = "FailDoc"
        mock_doc.metadata = {"file_size_bytes": 500}

        state = {
            "qdrant_store": MagicMock(),
            "embedder": MagicMock(),
            "qdrant_collections": None,
            "graph_repo": None,
            "dedup_cache": None,
            "dedup_pipeline": None,
            "term_extractor": None,
            "graphrag_extractor": None,
        }

        with patch("src.pipeline.ingestion.IngestionPipeline") as MockPipeline:
            pipeline_instance = AsyncMock()
            pipeline_instance.ingest.side_effect = RuntimeError("parse error")
            MockPipeline.return_value = pipeline_instance

            docs_ingested, total_chunks, errors = await _run_ingestion(
                state, [mock_doc], "kb-1",
            )

        assert docs_ingested == 0
        assert total_chunks == 0
        assert len(errors) == 1
        assert "FailDoc" in errors[0]


# ---------------------------------------------------------------------------
# _report_sync_failure
# ---------------------------------------------------------------------------

class TestReportSyncFailure:
    @pytest.mark.asyncio
    async def test_updates_both_repos(self):
        from src.api.routes.data_source_sync import _report_sync_failure

        ds_repo = AsyncMock()
        run_repo = AsyncMock()

        await _report_sync_failure(
            ds_repo, run_repo, "src-1", "run-1", RuntimeError("boom"),
        )
        ds_repo.complete_sync.assert_awaited_once()
        assert ds_repo.complete_sync.call_args[0][1] == "error"
        run_repo.complete.assert_awaited_once()
        run_data = run_repo.complete.call_args[0][1]
        assert run_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_handles_repo_exceptions(self):
        from src.api.routes.data_source_sync import _report_sync_failure

        ds_repo = AsyncMock()
        ds_repo.complete_sync.side_effect = Exception("db error")
        run_repo = AsyncMock()
        run_repo.complete.side_effect = Exception("db error")

        # Should not raise
        await _report_sync_failure(
            ds_repo, run_repo, "src-1", "run-1", RuntimeError("boom"),
        )

    @pytest.mark.asyncio
    async def test_none_repos(self):
        from src.api.routes.data_source_sync import _report_sync_failure

        # Should not raise with None repos
        await _report_sync_failure(None, None, "src-1", "run-1", RuntimeError("boom"))


# ---------------------------------------------------------------------------
# _OnnxSparseEmbedder
# ---------------------------------------------------------------------------

class TestOnnxSparseEmbedder:
    @pytest.mark.asyncio
    async def test_embed_sparse(self):
        from src.api.routes.data_source_sync import _OnnxSparseEmbedder

        mock_provider = MagicMock()
        mock_provider.encode.return_value = {
            "lexical_weights": [{"token1": 0.5}, {"token2": 0.3}],
        }

        embedder = _OnnxSparseEmbedder(mock_provider)
        result = await embedder.embed_sparse(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == {"token1": 0.5}

    @pytest.mark.asyncio
    async def test_embed_sparse_no_lexical_weights(self):
        from src.api.routes.data_source_sync import _OnnxSparseEmbedder

        mock_provider = MagicMock()
        mock_provider.encode.return_value = {}

        embedder = _OnnxSparseEmbedder(mock_provider)
        result = await embedder.embed_sparse(["text1"])
        assert result == [{}]


# ---------------------------------------------------------------------------
# _start_ocr_instance — additional states
# ---------------------------------------------------------------------------

class TestStartOcrInstanceStates:
    @pytest.mark.asyncio
    async def test_unexpected_state_returns_fallback(self):
        from src.api.routes.data_source_sync import _start_ocr_instance

        with (
            patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", "i-123"),
            patch("src.api.routes.data_source_sync._PADDLEOCR_API_URL", "http://fallback:8866"),
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="terminated",
            ),
        ):
            result = await _start_ocr_instance()
            assert result == "http://fallback:8866"

    @pytest.mark.asyncio
    async def test_stopping_state_waits_then_boots(self):
        from src.api.routes.data_source_sync import _start_ocr_instance

        with (
            patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", "i-123"),
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="stopping",
            ),
            patch(
                "src.api.routes.data_source_sync._wait_for_instance_stopped",
            ) as mock_wait,
            patch(
                "src.api.routes.data_source_sync._boot_and_resolve_url",
                return_value="http://10.0.0.1:8866",
            ),
        ):
            result = await _start_ocr_instance()
            mock_wait.assert_awaited_once()
            assert result == "http://10.0.0.1:8866"

    @pytest.mark.asyncio
    async def test_stopped_state_boots(self):
        from src.api.routes.data_source_sync import _start_ocr_instance

        with (
            patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", "i-123"),
            patch(
                "src.api.routes.data_source_sync._get_instance_state",
                return_value="stopped",
            ),
            patch(
                "src.api.routes.data_source_sync._boot_and_resolve_url",
                return_value="http://10.0.0.2:8866",
            ),
        ):
            result = await _start_ocr_instance()
            assert result == "http://10.0.0.2:8866"
