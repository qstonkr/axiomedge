"""Tests for data source sync: trigger, crawl, ingest pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
        from src.api.routes.data_source_sync import _start_ocr_instance

        with patch("src.api.routes.data_source_sync._PADDLEOCR_INSTANCE_ID", ""):
            result = await _start_ocr_instance()
            assert result is None or result == ""

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
        from src.database.repositories.data_source import DataSourceRepository

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
