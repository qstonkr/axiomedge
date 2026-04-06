"""Tests for src/connectors/confluence/client.py and attachment_parser.py.

Covers the main code paths with mocked external dependencies (httpx, fitz, openpyxl, docx).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_output(tmp_path):
    """Provide a temp output directory with attachments subdir."""
    att = tmp_path / "attachments"
    att.mkdir()
    return tmp_path


@pytest.fixture()
def _clean_env(monkeypatch):
    """Remove env vars that could interfere with tests."""
    for key in [
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_PAT",
        "CONFLUENCE_CRAWL_TIMEOUT",
        "CONFLUENCE_VERIFY_SSL",
        "KNOWLEDGE_CRAWL_ATTACHMENT_OCR_MODE",
        "KNOWLEDGE_CRAWL_OCR_MIN_TEXT_CHARS",
        "KNOWLEDGE_CRAWL_OCR_MAX_PDF_PAGES",
        "KNOWLEDGE_CRAWL_OCR_MAX_PPT_SLIDES",
        "KNOWLEDGE_CRAWL_OCR_MAX_IMAGES_PER_ATTACHMENT",
        "KNOWLEDGE_SLIDE_RENDER_ENABLED",
        "KNOWLEDGE_LAYOUT_ANALYSIS_ENABLED",
        "KNOWLEDGE_CRAWL_SLIDE_RENDER_ENABLED",
        "KNOWLEDGE_CRAWL_LAYOUT_ANALYSIS_ENABLED",
    ]:
        monkeypatch.delenv(key, raising=False)


def _make_client(tmp_output, **kwargs):
    """Create a ConfluenceFullClient with safe defaults for testing."""
    from src.connectors.confluence.client import ConfluenceFullClient

    defaults = {
        "base_url": "https://test.example.com",
        "pat": "test-token",
        "output_dir": tmp_output,
    }
    defaults.update(kwargs)
    return ConfluenceFullClient(**defaults)


def _make_fake_page(page_id="123", title="Test Page"):
    """Create a minimal FullPageContent for testing."""
    from src.connectors.confluence.models import FullPageContent

    return FullPageContent(
        page_id=page_id,
        title=title,
        content_text="Hello world",
        content_html="<p>Hello world</p>",
        content_preview="Hello world",
        tables=[],
        mentions=[],
        sections=[],
        creator="tester",
        last_modifier="tester",
        version=1,
        url=f"https://test.example.com/pages/viewpage.action?pageId={page_id}",
        created_at="2024-01-01T00:00:00.000Z",
        updated_at="2024-01-01T00:00:00.000Z",
    )


# ===================================================================
# client.py — Constructor
# ===================================================================


class TestConfluenceClientConstructor:
    def test_defaults(self, tmp_output):
        client = _make_client(tmp_output)
        assert client.base_url == "https://test.example.com"
        assert client.headers["Authorization"] == "Bearer test-token"
        assert client.output_dir == tmp_output
        assert client.visited_pages == set()
        assert client._total_pages_crawled == 0

    def test_env_fallback(self, tmp_output, monkeypatch):
        monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("CONFLUENCE_PAT", "env-token")
        client = _make_client(tmp_output, base_url="", pat="")
        assert client.base_url == "https://env.example.com"
        assert client.headers["Authorization"] == "Bearer env-token"

    def test_max_concurrent_floor(self, tmp_output):
        client = _make_client(tmp_output, max_concurrent=0)
        assert client._max_concurrent == 1
        assert client._page_sem is None  # no semaphore for concurrency=1

    def test_max_concurrent_semaphore(self, tmp_output):
        client = _make_client(tmp_output, max_concurrent=3)
        assert client._max_concurrent == 3
        assert client._page_sem is not None

    def test_checkpoint_dir_created(self, tmp_output):
        client = _make_client(tmp_output)
        assert client.checkpoint_file == tmp_output / "checkpoint.json"
        assert client.attachments_dir.exists()


# ===================================================================
# client.py — _http_get_with_retry
# ===================================================================


class TestHttpGetWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client._http_get_with_retry("https://test.example.com/api")
        assert result is mock_resp
        client.client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        client.client.get = AsyncMock(
            side_effect=[httpx.TimeoutException("timeout"), mock_resp]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=2
            )
        assert result is mock_resp
        assert client.client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_5xx(self, tmp_output):
        client = _make_client(tmp_output)
        # Build a real-looking HTTPStatusError
        request = httpx.Request("GET", "https://test.example.com/api")
        error_resp = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError(
            "503", request=request, response=error_resp
        )

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        client.client.get = AsyncMock(side_effect=[error, ok_resp])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=2
            )
        assert result is ok_resp

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx(self, tmp_output):
        client = _make_client(tmp_output)
        request = httpx.Request("GET", "https://test.example.com/api")
        error_resp = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError(
            "404", request=request, response=error_resp
        )
        client.client.get = AsyncMock(side_effect=error)

        with pytest.raises(httpx.HTTPStatusError):
            await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=3
            )
        assert client.client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_shutdown_aborts_retry(self, tmp_output):
        client = _make_client(tmp_output)
        client._shutdown_requested = True
        client.client.get = AsyncMock()

        with pytest.raises(RuntimeError, match="Shutdown"):
            await client._http_get_with_retry("https://test.example.com/api")
        client.client.get.assert_not_awaited()


# ===================================================================
# client.py — get_page_full
# ===================================================================


class TestGetPageFull:
    @pytest.mark.asyncio
    async def test_returns_full_page_content(self, tmp_output):
        from src.connectors.confluence.models import FullPageContent

        client = _make_client(tmp_output)
        api_data = {
            "title": "My Page",
            "body": {"storage": {"value": "<p>Content here</p>"}},
            "history": {
                "createdBy": {"displayName": "Author", "accountId": "acc1"},
                "createdDate": "2024-01-01T00:00:00Z",
                "lastUpdated": {
                    "by": {"displayName": "Editor"},
                    "when": "2024-02-01T00:00:00Z",
                },
            },
            "version": {"number": 3},
            "space": {"key": "TST"},
            "ancestors": [{"id": "10", "title": "Parent"}],
            "restrictions": {},
            "metadata": {"labels": {"results": []}},
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = api_data
        mock_resp.raise_for_status = MagicMock()

        client._http_get_with_retry = AsyncMock(return_value=mock_resp)
        client.get_labels = AsyncMock(return_value=[])
        client.get_comments = AsyncMock(return_value=[])
        client.get_user_details = AsyncMock(return_value=None)

        page = await client.get_page_full("42")
        assert isinstance(page, FullPageContent)
        assert page.page_id == "42"
        assert page.title == "My Page"
        assert page.version == 3
        assert page.space_key == "TST"
        assert page.creator == "Author"

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, tmp_output):
        client = _make_client(tmp_output)
        client._http_get_with_retry = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        result = await client.get_page_full("42")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, tmp_output):
        client = _make_client(tmp_output)
        request = httpx.Request("GET", "https://test.example.com/api")
        error_resp = httpx.Response(403, request=request, text="Forbidden")
        client._http_get_with_retry = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "403", request=request, response=error_resp
            )
        )
        result = await client.get_page_full("42")
        assert result is None


# ===================================================================
# client.py — get_child_pages
# ===================================================================


class TestGetChildPages:
    @pytest.mark.asyncio
    async def test_single_page_response(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {
            "results": [{"id": "c1"}, {"id": "c2"}],
            "_links": {},
        }
        mock_resp.raise_for_status = MagicMock()
        client._http_get_with_retry = AsyncMock(return_value=mock_resp)

        children = await client.get_child_pages("parent")
        assert children == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_pagination(self, tmp_output):
        client = _make_client(tmp_output)
        resp1 = MagicMock(spec=httpx.Response)
        resp1.json.return_value = {
            "results": [{"id": "c1"}],
            "_links": {"next": "/rest/api/content/parent/child/page?start=1"},
        }
        resp1.raise_for_status = MagicMock()

        resp2 = MagicMock(spec=httpx.Response)
        resp2.json.return_value = {
            "results": [{"id": "c2"}],
            "_links": {},
        }
        resp2.raise_for_status = MagicMock()

        client._http_get_with_retry = AsyncMock(side_effect=[resp1, resp2])

        children = await client.get_child_pages("parent")
        assert children == ["c1", "c2"]
        assert client._http_get_with_retry.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_children(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {"results": [], "_links": {}}
        mock_resp.raise_for_status = MagicMock()
        client._http_get_with_retry = AsyncMock(return_value=mock_resp)

        children = await client.get_child_pages("parent")
        assert children == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, tmp_output):
        client = _make_client(tmp_output)
        client._http_get_with_retry = AsyncMock(
            side_effect=Exception("connection error")
        )
        children = await client.get_child_pages("parent")
        assert children == []


# ===================================================================
# client.py — crawl_bfs
# ===================================================================


class TestCrawlBfs:
    @pytest.mark.asyncio
    async def test_visits_root_and_children(self, tmp_output):
        client = _make_client(tmp_output)
        page_root = _make_fake_page("root", "Root")
        page_c1 = _make_fake_page("c1", "Child 1")

        call_count = 0

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            nonlocal call_count
            call_count += 1
            if pid == "root":
                return page_root, ["c1"]
            elif pid == "c1":
                return page_c1, []
            return None, []

        client._process_single_page = mock_process

        await client.crawl_bfs(
            root_page_id="root",
            max_depth=5,
            source_key="test",
        )
        assert "root" in client.visited_pages
        assert "c1" in client.visited_pages
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_pages_limit(self, tmp_output):
        client = _make_client(tmp_output)

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            page = _make_fake_page(pid, f"Page {pid}")
            client.all_pages.append(page)
            client._total_pages_crawled += 1
            return page, [f"child_{pid}"]

        client._process_single_page = mock_process

        await client.crawl_bfs(
            root_page_id="root",
            max_depth=10,
            max_pages=1,
            source_key="test",
        )
        # Should stop after processing 1 page
        assert client._total_pages_crawled <= 2  # root + possibly one child

    @pytest.mark.asyncio
    async def test_skips_already_visited(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages.add("already-seen")

        calls = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            calls.append(pid)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process

        await client.crawl_bfs(
            root_page_id="already-seen",
            max_depth=5,
            source_key="test",
        )
        assert calls == []


# ===================================================================
# client.py — save_checkpoint / load_checkpoint
# ===================================================================


class TestCheckpoint:
    def test_save_and_load_checkpoint(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages = {"p1", "p2", "p3"}
        client._total_pages_crawled = 3
        client.all_pages = [_make_fake_page("p3", "Last Page")]

        client.save_checkpoint("test-source")
        assert client.checkpoint_file.exists()

        # Verify JSON content
        data = json.loads(client.checkpoint_file.read_text())
        assert data["source_key"] == "test-source"
        assert set(data["visited_pages"]) == {"p1", "p2", "p3"}
        assert data["last_page_id"] == "p3"

        # Load into fresh client
        client2 = _make_client(tmp_output)
        assert client2.load_checkpoint("test-source") is True
        assert client2.visited_pages == {"p1", "p2", "p3"}
        assert client2._total_pages_crawled == 3

    def test_load_checkpoint_wrong_source(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages = {"p1"}
        client.all_pages = [_make_fake_page("p1")]
        client.save_checkpoint("source-a")

        client2 = _make_client(tmp_output)
        assert client2.load_checkpoint("source-b") is False

    def test_load_checkpoint_no_file(self, tmp_output):
        client = _make_client(tmp_output)
        assert client.load_checkpoint("test") is False

    def test_load_checkpoint_kb_mismatch(self, tmp_output):
        client = _make_client(tmp_output, kb_id="kb-1")
        client.visited_pages = {"p1"}
        client.all_pages = [_make_fake_page("p1")]
        client.save_checkpoint("src")

        client2 = _make_client(tmp_output, kb_id="kb-2")
        assert client2.load_checkpoint("src") is False
        assert len(client2.visited_pages) == 0

    def test_clear_checkpoint(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages = {"p1"}
        client.all_pages = [_make_fake_page("p1")]
        client.save_checkpoint("src")
        assert client.checkpoint_file.exists()

        client.clear_checkpoint()
        assert not client.checkpoint_file.exists()


# ===================================================================
# client.py — save_incremental / load_incremental
# ===================================================================


class TestIncremental:
    def test_save_and_load_incremental(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [
            _make_fake_page("p1", "Page 1"),
            _make_fake_page("p2", "Page 2"),
        ]
        client._incremental_saved_count = 0

        client.save_incremental("test-src")

        jsonl_path = client._get_incremental_path("test-src")
        assert jsonl_path.exists()

        # Pages should be cleared from memory after save
        assert len(client.all_pages) == 0

        # Load into fresh client
        client2 = _make_client(tmp_output)
        loaded = client2.load_incremental("test-src")
        assert loaded == 2
        assert "p1" in client2.visited_pages
        assert "p2" in client2.visited_pages

    def test_load_incremental_skips_empty_content(self, tmp_output):
        client = _make_client(tmp_output)
        # Manually write JSONL with one empty-content page
        jsonl_path = client._get_incremental_path("src")
        jsonl_path.write_text(
            json.dumps({"page_id": "p1", "content_text": "real content"}) + "\n"
            + json.dumps({"page_id": "p2", "content_text": ""}) + "\n"
        )

        loaded = client.load_incremental("src")
        assert loaded == 1
        assert "p1" in client.visited_pages
        assert "p2" not in client.visited_pages

    def test_load_incremental_no_file(self, tmp_output):
        client = _make_client(tmp_output)
        assert client.load_incremental("nonexistent") == 0

    def test_save_incremental_no_new_pages(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = []
        client._incremental_saved_count = 0

        client.save_incremental("src")
        # Should not create the file
        jsonl_path = client._get_incremental_path("src")
        assert not jsonl_path.exists()

    def test_clear_incremental(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [_make_fake_page("p1")]
        client.save_incremental("src")
        assert client._get_incremental_path("src").exists()

        client.clear_incremental("src")
        assert not client._get_incremental_path("src").exists()

    def test_finalize_from_incremental(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [_make_fake_page("p1", "Page 1")]
        client.save_incremental("src")

        # Add a new in-memory page
        client.all_pages = [_make_fake_page("p2", "Page 2")]

        result = client.finalize_from_incremental("src")
        assert len(result) == 2
        ids = {d["page_id"] for d in result}
        assert ids == {"p1", "p2"}


# ===================================================================
# client.py — page_to_dict usage
# ===================================================================


class TestPageToDict:
    def test_page_to_dict_roundtrip(self):
        from src.connectors.confluence.models import page_to_dict

        page = _make_fake_page("42", "Test")
        d = page_to_dict(page)
        assert d["page_id"] == "42"
        assert d["title"] == "Test"
        assert d["content_text"] == "Hello world"
        assert d["tables"] == []
        assert d["attachments"] == []


# ===================================================================
# client.py — close()
# ===================================================================


class TestClientClose:
    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, tmp_output):
        client = _make_client(tmp_output)
        client.client.aclose = AsyncMock()
        await client.close()
        client.client.aclose.assert_awaited_once()


# ===================================================================
# client.py — runtime stats
# ===================================================================


class TestRuntimeStats:
    def test_write_runtime_stats(self, tmp_output):
        client = _make_client(tmp_output)
        client._total_pages_crawled = 5
        client.write_runtime_stats()

        stats_path = client.runtime_stats_path()
        assert stats_path.exists()
        data = json.loads(stats_path.read_text())
        assert data["pages_total"] == 5
        assert "elapsed_seconds" in data

    def test_record_attachment_stats(self, tmp_output):
        from src.connectors.confluence.models import AttachmentContent

        client = _make_client(tmp_output)
        att = AttachmentContent(
            id="a1",
            filename="test.pdf",
            media_type="application/pdf",
            file_size=1000,
            ocr_applied=True,
            native_text_chars=500,
            ocr_text_chars=200,
            ocr_units_attempted=3,
        )
        client._record_attachment_stats(att)
        assert client._runtime_stats["attachments_total"] == 1
        assert client._runtime_stats["attachments_ocr_applied"] == 1
        assert client._runtime_stats["native_text_chars_total"] == 500
        assert client._runtime_stats["pdf_pages_ocr_attempted"] == 3


# ===================================================================
# attachment_parser.py — _env_bool / _env_int
# ===================================================================


class TestEnvHelpers:
    def test_env_bool_true_values(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_bool

        for val in ["1", "true", "True", "yes", "on", "  TRUE  "]:
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", False) is True

    def test_env_bool_false_values(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_bool

        for val in ["0", "false", "no", "off", "random"]:
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", True) is False

    def test_env_bool_default(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_bool

        monkeypatch.delenv("TEST_BOOL_MISSING", raising=False)
        assert _env_bool("TEST_BOOL_MISSING", True) is True
        assert _env_bool("TEST_BOOL_MISSING", False) is False

    def test_env_int_valid(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_int

        monkeypatch.setenv("TEST_INT", "42")
        assert _env_int("TEST_INT") == 42

    def test_env_int_missing(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_int

        monkeypatch.delenv("TEST_INT_MISSING", raising=False)
        assert _env_int("TEST_INT_MISSING") is None

    def test_env_int_empty(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_int

        monkeypatch.setenv("TEST_INT", "  ")
        assert _env_int("TEST_INT") is None

    def test_env_int_invalid(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _env_int

        monkeypatch.setenv("TEST_INT", "abc")
        assert _env_int("TEST_INT") is None


# ===================================================================
# attachment_parser.py — configure_run
# ===================================================================


class TestConfigureRun:
    def test_default_policy(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = AttachmentParser.configure_run("generic_source")
        assert policy.attachment_ocr_mode == "force"
        assert policy.ocr_min_text_chars == 100
        assert policy.ocr_max_pdf_pages == 1_000_000

    def test_itops_source_defaults(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = AttachmentParser.configure_run("itops")
        assert policy.attachment_ocr_mode == "auto"
        assert policy.ocr_max_pdf_pages == 10
        assert policy.ocr_max_ppt_slides == 10
        assert policy.slide_render_enabled is False

    def test_overrides_take_precedence(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = AttachmentParser.configure_run(
            "itops",
            overrides={"attachment_ocr_mode": "off", "ocr_max_pdf_pages": 999},
        )
        assert policy.attachment_ocr_mode == "off"
        assert policy.ocr_max_pdf_pages == 999

    def test_env_var_overrides_source_defaults(self, monkeypatch, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        monkeypatch.setenv("KNOWLEDGE_CRAWL_ATTACHMENT_OCR_MODE", "off")
        policy = AttachmentParser.configure_run("itops")
        assert policy.attachment_ocr_mode == "off"

    def test_invalid_mode_falls_back(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = AttachmentParser.configure_run(
            "src", overrides={"attachment_ocr_mode": "invalid_mode"}
        )
        assert policy.attachment_ocr_mode == "force"  # default

    def test_current_policy_returns_last_configured(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("itops")
        assert AttachmentParser.current_policy().attachment_ocr_mode == "auto"

        AttachmentParser.configure_run("generic")
        assert AttachmentParser.current_policy().attachment_ocr_mode == "force"


# ===================================================================
# attachment_parser.py — _get_ocr_instance
# ===================================================================


class TestGetOcrInstance:
    def test_import_error_returns_none(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Reset singleton
        original_instance = AttachmentParser._ocr_instance
        original_type = AttachmentParser._ocr_type
        try:
            AttachmentParser._ocr_instance = None
            AttachmentParser._ocr_type = None

            # Mock the import to fail inside _get_ocr_instance
            import builtins

            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if "paddle_ocr_provider" in name:
                    raise ImportError("No PaddleOCR")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = AttachmentParser._get_ocr_instance()
                assert result is None
        finally:
            AttachmentParser._ocr_instance = original_instance
            AttachmentParser._ocr_type = original_type

    def test_returns_existing_singleton(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        original_instance = AttachmentParser._ocr_instance
        original_type = AttachmentParser._ocr_type
        try:
            sentinel = MagicMock()
            AttachmentParser._ocr_instance = sentinel
            AttachmentParser._ocr_type = "paddle"

            result = AttachmentParser._get_ocr_instance()
            assert result is sentinel
        finally:
            AttachmentParser._ocr_instance = original_instance
            AttachmentParser._ocr_type = original_type


# ===================================================================
# attachment_parser.py — parse_pdf
# ===================================================================


class TestParsePdf:
    def test_parse_pdf_text_extraction(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        # Mock fitz module
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page 1 text content"
        mock_page.find_tables.return_value = []

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.__len__ = MagicMock(return_value=1)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "test.pdf")

        assert "Page 1 text content" in result.extracted_text
        assert result.confidence == 0.9
        assert result.ocr_applied is False
        assert result.native_text_chars > 0

    def test_parse_pdf_exception(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = Exception("corrupt file")

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "bad.pdf")

        assert "오류" in result.extracted_text or "corrupt" in result.extracted_text
        assert result.confidence == 0.0

    def test_parse_pdf_multiple_pages(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        pages = []
        for i in range(3):
            p = MagicMock()
            p.get_text.return_value = f"Content of page {i + 1}"
            p.find_tables.return_value = []
            pages.append(p)

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter(pages))
        mock_doc.__len__ = MagicMock(return_value=3)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "multi.pdf")

        assert "Content of page 1" in result.extracted_text
        assert "Content of page 3" in result.extracted_text
        assert result.confidence == 0.9


# ===================================================================
# attachment_parser.py — parse_excel
# ===================================================================


class TestParseExcel:
    def test_parse_excel_basic(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Mock openpyxl
        mock_sheet = MagicMock()
        mock_sheet.iter_rows.return_value = [
            ("Header1", "Header2"),
            ("val1", "val2"),
            ("val3", "val4"),
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_sheet)

        with patch.dict("sys.modules", {"openpyxl": MagicMock()}):
            with patch(
                "src.connectors.confluence.attachment_parser.AttachmentParser.parse_excel"
            ) as mock_parse:
                # Since openpyxl import is inside the method, we test via direct mock
                pass

        # Alternative: test the actual method with mocked import
        mock_openpyxl = MagicMock()
        mock_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = AttachmentParser.parse_excel(tmp_path / "test.xlsx")

        assert result.confidence == 0.95
        assert len(result.extracted_tables) == 1
        assert "Header1" in result.extracted_text

    def test_parse_excel_exception(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_openpyxl = MagicMock()
        mock_openpyxl.load_workbook.side_effect = Exception("bad file")

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = AttachmentParser.parse_excel(tmp_path / "bad.xlsx")

        assert result.confidence == 0.0
        assert "오류" in result.extracted_text or "bad file" in result.extracted_text

    def test_parse_excel_empty_sheet(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_sheet = MagicMock()
        # All rows are empty
        mock_sheet.iter_rows.return_value = [
            (None, None),
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Empty"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_sheet)

        mock_openpyxl = MagicMock()
        mock_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = AttachmentParser.parse_excel(tmp_path / "empty.xlsx")

        # Empty rows are filtered, so no tables
        assert result.confidence == 0.0 or result.extracted_tables == []


# ===================================================================
# attachment_parser.py — parse_word
# ===================================================================


class TestParseWord:
    def test_parse_docx(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Mock python-docx
        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph"
        mock_para2 = MagicMock()
        mock_para2.text = "Second paragraph"

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2]
        mock_doc.tables = []

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = AttachmentParser.parse_word(tmp_path / "test.docx")

        assert "First paragraph" in result.extracted_text
        assert "Second paragraph" in result.extracted_text
        assert result.confidence == 0.9

    def test_parse_docx_exception(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_docx_module = MagicMock()
        mock_docx_module.Document.side_effect = Exception("corrupt docx")

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = AttachmentParser.parse_word(tmp_path / "bad.docx")

        assert result.confidence == 0.0

    def test_parse_docx_with_tables(self, tmp_path):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_doc = MagicMock()
        mock_doc.paragraphs = []

        mock_cell1 = MagicMock()
        mock_cell1.text = "H1"
        mock_cell2 = MagicMock()
        mock_cell2.text = "H2"
        mock_row1 = MagicMock()
        mock_row1.cells = [mock_cell1, mock_cell2]

        mock_cell3 = MagicMock()
        mock_cell3.text = "V1"
        mock_cell4 = MagicMock()
        mock_cell4.text = "V2"
        mock_row2 = MagicMock()
        mock_row2.cells = [mock_cell3, mock_cell4]

        mock_table = MagicMock()
        mock_table.rows = [mock_row1, mock_row2]
        mock_doc.tables = [mock_table]

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = AttachmentParser.parse_word(tmp_path / "tables.docx")

        assert len(result.extracted_tables) == 1
        assert result.extracted_tables[0]["headers"] == ["H1", "H2"]


# ===================================================================
# attachment_parser.py — cleanup_ocr
# ===================================================================


class TestCleanupOcr:
    def test_cleanup_ocr_resets_state(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Save originals
        orig_instance = AttachmentParser._ocr_instance
        orig_type = AttachmentParser._ocr_type
        orig_pool = AttachmentParser._ocr_process_pool

        try:
            AttachmentParser._ocr_instance = MagicMock()
            AttachmentParser._ocr_type = "paddle"
            AttachmentParser._ocr_process_pool = MagicMock()

            AttachmentParser.cleanup_ocr()

            assert AttachmentParser._ocr_instance is None
            assert AttachmentParser._ocr_type is None
            assert AttachmentParser._ocr_process_pool is None
        finally:
            AttachmentParser._ocr_instance = orig_instance
            AttachmentParser._ocr_type = orig_type
            AttachmentParser._ocr_process_pool = orig_pool

    def test_cleanup_ocr_pool_shutdown(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        orig_instance = AttachmentParser._ocr_instance
        orig_type = AttachmentParser._ocr_type
        orig_pool = AttachmentParser._ocr_process_pool

        try:
            mock_pool = MagicMock()
            AttachmentParser._ocr_instance = None
            AttachmentParser._ocr_type = None
            AttachmentParser._ocr_process_pool = mock_pool

            AttachmentParser.cleanup_ocr()

            mock_pool.shutdown.assert_called_once_with(wait=False)
        finally:
            AttachmentParser._ocr_instance = orig_instance
            AttachmentParser._ocr_type = orig_type
            AttachmentParser._ocr_process_pool = orig_pool

    def test_cleanup_ocr_no_pool(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        orig_instance = AttachmentParser._ocr_instance
        orig_type = AttachmentParser._ocr_type
        orig_pool = AttachmentParser._ocr_process_pool

        try:
            AttachmentParser._ocr_instance = None
            AttachmentParser._ocr_type = None
            AttachmentParser._ocr_process_pool = None

            # Should not raise
            AttachmentParser.cleanup_ocr()
            assert AttachmentParser._ocr_instance is None
        finally:
            AttachmentParser._ocr_instance = orig_instance
            AttachmentParser._ocr_type = orig_type
            AttachmentParser._ocr_process_pool = orig_pool


# ===================================================================
# attachment_parser.py — _text_chars helper
# ===================================================================


class TestTextCharsHelper:
    def test_normal_string(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._text_chars("hello world") == 11

    def test_whitespace_only(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._text_chars("   ") == 0

    def test_none(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._text_chars(None) == 0

    def test_empty(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._text_chars("") == 0


# ===================================================================
# attachment_parser.py — _emit_status helper
# ===================================================================


class TestEmitStatus:
    def test_with_heartbeat(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        fn = MagicMock()
        AttachmentParser._emit_status(fn, "hello")
        fn.assert_called_once_with("hello")

    def test_without_heartbeat(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Should not raise
        AttachmentParser._emit_status(None, "hello")


# ===================================================================
# client.py — request_shutdown / shutdown_requested
# ===================================================================


class TestShutdown:
    def test_shutdown_flag(self, tmp_output):
        client = _make_client(tmp_output)
        assert client.shutdown_requested is False
        client.request_shutdown()
        assert client.shutdown_requested is True


# ===================================================================
# client.py — _http_get_with_retry (additional edge cases)
# ===================================================================


class TestHttpGetWithRetryEdgeCases:
    @pytest.mark.asyncio
    async def test_max_retries_exceeded_timeout(self, tmp_output):
        """All retries fail with TimeoutException -> raises last error."""
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(
            side_effect=httpx.TimeoutException("always times out")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.TimeoutException, match="always times out"):
                await client._http_get_with_retry(
                    "https://test.example.com/api", max_retries=3
                )
        assert client.client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_connect_error(self, tmp_output):
        """All retries fail with ConnectError -> raises last error."""
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.ConnectError):
                await client._http_get_with_retry(
                    "https://test.example.com/api", max_retries=2
                )
        assert client.client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_429(self, tmp_output):
        """429 Too Many Requests should be retried."""
        client = _make_client(tmp_output)
        request = httpx.Request("GET", "https://test.example.com/api")
        error_resp = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("429", request=request, response=error_resp)

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        client.client.get = AsyncMock(side_effect=[error, ok_resp])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=3
            )
        assert result is ok_resp

    @pytest.mark.asyncio
    async def test_retry_on_500(self, tmp_output):
        """500 Internal Server Error should be retried."""
        client = _make_client(tmp_output)
        request = httpx.Request("GET", "https://test.example.com/api")
        error_resp = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("500", request=request, response=error_resp)

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        client.client.get = AsyncMock(side_effect=[error, ok_resp])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=2
            )
        assert result is ok_resp

    @pytest.mark.asyncio
    async def test_pool_timeout_retried(self, tmp_output):
        """PoolTimeout should be retried like TimeoutException."""
        client = _make_client(tmp_output)
        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        client.client.get = AsyncMock(
            side_effect=[httpx.PoolTimeout("pool full"), ok_resp]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._http_get_with_retry(
                "https://test.example.com/api", max_retries=2
            )
        assert result is ok_resp


# ===================================================================
# client.py — _do_process_page
# ===================================================================


class TestDoProcessPage:
    @pytest.mark.asyncio
    async def test_successful_page_processing(self, tmp_output):
        client = _make_client(tmp_output)
        fake_page = _make_fake_page("pg1", "Test Page")
        client.get_page_full = AsyncMock(return_value=fake_page)
        client.get_child_pages = AsyncMock(return_value=["c1", "c2"])
        client.get_attachments = AsyncMock(return_value=[])

        page, child_ids = await client._do_process_page(
            "pg1", False, 20, None, None, "test-src"
        )
        assert page is fake_page
        assert child_ids == ["c1", "c2"]
        assert client._total_pages_crawled == 1
        assert fake_page in client.all_pages

    @pytest.mark.asyncio
    async def test_failed_page_still_returns_children(self, tmp_output):
        client = _make_client(tmp_output)
        client.get_page_full = AsyncMock(return_value=None)
        client.get_child_pages = AsyncMock(return_value=["c1"])

        page, child_ids = await client._do_process_page(
            "pg1", False, 20, None, None, "test-src"
        )
        assert page is None
        assert child_ids == ["c1"]
        assert client._total_pages_crawled == 0

    @pytest.mark.asyncio
    async def test_skip_children_flag(self, tmp_output):
        client = _make_client(tmp_output)
        fake_page = _make_fake_page("pg1", "Test Page")
        client.get_page_full = AsyncMock(return_value=fake_page)
        client.get_attachments = AsyncMock(return_value=[])

        page, child_ids = await client._do_process_page(
            "pg1", False, 20, None, None, "test-src", skip_children=True
        )
        assert page is fake_page
        assert child_ids == []

    @pytest.mark.asyncio
    async def test_checkpoint_triggered_at_interval(self, tmp_output):
        client = _make_client(tmp_output)
        client.CHECKPOINT_INTERVAL = 1
        fake_page = _make_fake_page("pg1", "Page")
        client.get_page_full = AsyncMock(return_value=fake_page)
        client.get_child_pages = AsyncMock(return_value=[])
        client.get_attachments = AsyncMock(return_value=[])
        client.save_checkpoint = MagicMock()
        client.save_incremental = MagicMock()

        await client._do_process_page("pg1", False, 20, None, None, "test-src")
        client.save_checkpoint.assert_called_once_with("test-src")
        client.save_incremental.assert_called_once_with("test-src")

    @pytest.mark.asyncio
    async def test_downloads_attachments_when_requested(self, tmp_output):
        from src.connectors.confluence.models import AttachmentContent

        client = _make_client(tmp_output)
        fake_page = _make_fake_page("pg1", "Page")
        client.get_page_full = AsyncMock(return_value=fake_page)
        client.get_child_pages = AsyncMock(return_value=[])
        att_meta = [{"id": "a1", "title": "test.pdf",
                     "extensions": {"mediaType": "application/pdf"}}]
        client.get_attachments = AsyncMock(return_value=att_meta)
        fake_att = AttachmentContent(
            id="a1", filename="test.pdf",
            media_type="application/pdf", file_size=100,
        )
        client.download_attachment = AsyncMock(return_value=fake_att)

        page, _ = await client._do_process_page(
            "pg1", True, 20, None, None, "test-src"
        )
        assert page is not None
        assert len(page.attachments) == 1


# ===================================================================
# client.py — crawl_recursive
# ===================================================================


class TestCrawlRecursive:
    @pytest.mark.asyncio
    async def test_dfs_traversal(self, tmp_output):
        client = _make_client(tmp_output)
        visited_order = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            visited_order.append(pid)
            if pid == "root":
                return _make_fake_page("root", "Root"), ["c1", "c2"]
            return _make_fake_page(pid, f"Page {pid}"), []

        client._process_single_page = mock_process
        await client.crawl_recursive("root", depth=0, max_depth=5, source_key="test")
        assert "root" in visited_order
        assert "c1" in visited_order
        assert "c2" in visited_order

    @pytest.mark.asyncio
    async def test_max_depth_respected(self, tmp_output):
        client = _make_client(tmp_output)
        visited = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            visited.append(pid)
            return _make_fake_page(pid), [f"child_of_{pid}"]

        client._process_single_page = mock_process
        await client.crawl_recursive("root", depth=0, max_depth=0, source_key="test")
        assert "root" in visited
        assert "child_of_root" not in visited

    @pytest.mark.asyncio
    async def test_shutdown_stops_crawl(self, tmp_output):
        client = _make_client(tmp_output)
        client._shutdown_requested = True
        calls = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            calls.append(pid)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        result = await client.crawl_recursive("root", depth=0, max_depth=5, source_key="test")
        assert result is None
        assert calls == []

    @pytest.mark.asyncio
    async def test_already_visited_explores_children(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages.add("root")
        child_calls = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            child_calls.append(pid)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        client.get_child_pages = AsyncMock(return_value=["c1"])
        await client.crawl_recursive("root", depth=0, max_depth=5, source_key="test")
        assert "c1" in child_calls
        assert "root" not in child_calls


# ===================================================================
# client.py — crawl_flat
# ===================================================================


class TestCrawlFlat:
    @pytest.mark.asyncio
    async def test_processes_all_page_ids(self, tmp_output):
        client = _make_client(tmp_output)
        processed = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            processed.append(pid)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        await client.crawl_flat(page_ids=["p1", "p2", "p3"], source_key="test")
        assert processed == ["p1", "p2", "p3"]

    @pytest.mark.asyncio
    async def test_skips_already_visited(self, tmp_output):
        client = _make_client(tmp_output)
        client.visited_pages.add("p2")
        processed = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            processed.append(pid)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        await client.crawl_flat(page_ids=["p1", "p2", "p3"], source_key="test")
        assert processed == ["p1", "p3"]

    @pytest.mark.asyncio
    async def test_max_pages_limit(self, tmp_output):
        client = _make_client(tmp_output)
        processed = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            processed.append(pid)
            client._total_pages_crawled += 1
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        await client.crawl_flat(
            page_ids=["p1", "p2", "p3"], max_pages=2, source_key="test"
        )
        assert len(processed) == 2

    @pytest.mark.asyncio
    async def test_shutdown_stops_flat_crawl(self, tmp_output):
        client = _make_client(tmp_output)
        processed = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, **kw):
            processed.append(pid)
            client._shutdown_requested = True
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        await client.crawl_flat(page_ids=["p1", "p2", "p3"], source_key="test")
        assert processed == ["p1"]

    @pytest.mark.asyncio
    async def test_skip_children_flag_used(self, tmp_output):
        client = _make_client(tmp_output)
        skip_values = []

        async def mock_process(pid, dl, maxatt, prog, tid, skey, skip_children=False, **kw):
            skip_values.append(skip_children)
            return _make_fake_page(pid), []

        client._process_single_page = mock_process
        await client.crawl_flat(page_ids=["p1"], source_key="test")
        assert skip_values == [True]


# ===================================================================
# client.py — download_attachment
# ===================================================================


class TestDownloadAttachment:
    @pytest.mark.asyncio
    async def test_download_and_parse(self, tmp_output):
        from src.connectors.confluence.models import AttachmentParseResult

        client = _make_client(tmp_output)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"fake pdf content"
        client.client.get = AsyncMock(return_value=mock_resp)

        parse_result = AttachmentParseResult(
            extracted_text="Parsed text from PDF",
            extracted_tables=[], confidence=0.9, native_text_chars=20,
        )
        with patch.object(client, "_parse_attachment_content",
                          new_callable=AsyncMock, return_value=parse_result):
            att_meta = {
                "id": "a1", "title": "test.pdf",
                "extensions": {"mediaType": "application/pdf", "fileSize": 1000},
                "_links": {"download": "/download/test.pdf"},
            }
            result = await client.download_attachment(att_meta, "pg1")

        assert result.filename == "test.pdf"
        assert result.extracted_text == "Parsed text from PDF"
        assert result.ocr_confidence == 0.9

    @pytest.mark.asyncio
    async def test_file_size_limit(self, tmp_output):
        client = _make_client(tmp_output)
        att_meta = {
            "id": "a2", "title": "huge.pdf",
            "extensions": {"mediaType": "application/pdf",
                           "fileSize": 60 * 1024 * 1024},
            "_links": {"download": "/download/huge.pdf"},
        }
        result = await client.download_attachment(att_meta, "pg1")
        assert result.parse_error is not None
        assert "exceeded" in result.parse_error.lower()

    @pytest.mark.asyncio
    async def test_download_error_captured(self, tmp_output):
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(side_effect=Exception("network failure"))
        att_meta = {
            "id": "a3", "title": "doc.pdf",
            "extensions": {"mediaType": "application/pdf", "fileSize": 1000},
            "_links": {"download": "/download/doc.pdf"},
        }
        result = await client.download_attachment(att_meta, "pg1")
        assert result.parse_error == "network failure"


# ===================================================================
# client.py — _extract_content_elements
# ===================================================================


class TestExtractContentElements:
    def test_extracts_text_from_html(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = ConfluenceFullClient._extract_content_elements(
            "<p>Hello <b>World</b></p>", "Test"
        )
        assert "Hello" in result["content_text"]
        assert "World" in result["content_text"]
        assert isinstance(result["tables"], list)
        assert isinstance(result["mentions"], list)

    def test_handles_empty_html(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = ConfluenceFullClient._extract_content_elements("", "Empty")
        assert isinstance(result["content_text"], str)

    def test_handles_malformed_html(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = ConfluenceFullClient._extract_content_elements(
            "<p>Unclosed<div>Mixed</p></div>", "Malformed"
        )
        assert "Unclosed" in result["content_text"] or "Mixed" in result["content_text"]


# ===================================================================
# client.py — _extract_page_metadata
# ===================================================================


class TestExtractPageMetadata:
    @pytest.mark.asyncio
    async def test_extracts_metadata(self, tmp_output):
        client = _make_client(tmp_output)
        client.get_user_details = AsyncMock(return_value={"email": "user@test.com"})
        data = {
            "history": {
                "createdBy": {"displayName": "Creator", "accountId": "acc1"},
                "createdDate": "2024-01-01T00:00:00Z",
                "lastUpdated": {
                    "by": {"displayName": "Editor"},
                    "when": "2024-02-01T00:00:00Z",
                },
            },
            "version": {"number": 5, "when": "2024-02-01",
                        "by": {"displayName": "Editor"}, "message": "update"},
            "space": {"key": "DEV"},
            "ancestors": [{"id": "100", "title": "Parent Page"}],
        }
        meta = await client._extract_page_metadata(data, "42")
        assert meta["creator"] == "Creator"
        assert meta["creator_email"] == "user@test.com"
        assert meta["last_modifier"] == "Editor"
        assert meta["version"] == 5
        assert meta["space_key"] == "DEV"
        assert len(meta["ancestors"]) == 1
        assert "42" in meta["url"]

    @pytest.mark.asyncio
    async def test_missing_creator_account_id(self, tmp_output):
        client = _make_client(tmp_output)
        client.get_user_details = AsyncMock()
        data = {
            "history": {
                "createdBy": {"displayName": "NoAccount"},
                "createdDate": "2024-01-01T00:00:00Z",
                "lastUpdated": {},
            },
            "version": {"number": 1},
            "space": {},
            "ancestors": [],
        }
        meta = await client._extract_page_metadata(data, "99")
        assert meta["creator_email"] is None
        client.get_user_details.assert_not_awaited()


# ===================================================================
# client.py — _extract_restrictions
# ===================================================================


class TestExtractRestrictions:
    def test_extracts_user_and_group_restrictions(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        data = {
            "restrictions": {
                "read": {"restrictions": {
                    "user": {"results": [{"displayName": "Alice", "accountId": "a1"}]},
                    "group": {"results": [{"name": "developers"}]},
                }},
                "update": {"restrictions": {
                    "user": {"results": []}, "group": {"results": []},
                }},
            }
        }
        restrictions = ConfluenceFullClient._extract_restrictions(data)
        assert len(restrictions) == 2
        assert restrictions[0].operation == "read"
        assert restrictions[0].restriction_type == "user"
        assert restrictions[0].name == "Alice"
        assert restrictions[1].restriction_type == "group"

    def test_empty_restrictions(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        restrictions = ConfluenceFullClient._extract_restrictions({"restrictions": {}})
        assert restrictions == []


# ===================================================================
# client.py — save_incremental / finalize (edge cases)
# ===================================================================


class TestIncrementalEdgeCases:
    def test_finalize_deduplicates_pages(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [_make_fake_page("p1", "Page 1")]
        client.save_incremental("src")
        client.all_pages = [_make_fake_page("p1", "Page 1 v2")]
        result = client.finalize_from_incremental("src")
        page_ids = [d["page_id"] for d in result]
        assert page_ids.count("p1") == 1

    def test_finalize_merges_jsonl_and_memory(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [_make_fake_page("p1", "Disk Page")]
        client.save_incremental("src")
        client.all_pages = [_make_fake_page("p2", "Memory Page")]
        result = client.finalize_from_incremental("src")
        ids = {d["page_id"] for d in result}
        assert ids == {"p1", "p2"}

    def test_save_incremental_write_error_truncates(self, tmp_output):
        client = _make_client(tmp_output)
        client.all_pages = [_make_fake_page("p1", "Page")]
        client._incremental_saved_count = 0
        jsonl_path = client._get_incremental_path("src")

        with patch("builtins.open", side_effect=IOError("disk full")):
            with patch.object(client, "_truncate_partial_jsonl_tail") as mock_trunc:
                with pytest.raises(IOError):
                    client.save_incremental("src")
                mock_trunc.assert_called_once_with(jsonl_path)

    def test_load_incremental_skips_no_page_id(self, tmp_output):
        client = _make_client(tmp_output)
        jsonl_path = client._get_incremental_path("src")
        jsonl_path.write_text(
            json.dumps({"content_text": "no id"}) + "\n"
            + json.dumps({"page_id": "p1", "content_text": "valid"}) + "\n"
        )
        loaded = client.load_incremental("src")
        assert loaded == 1
        assert "p1" in client.visited_pages

    def test_load_incremental_json_error(self, tmp_output):
        """load_incremental warns and returns 0 on corrupted JSON."""
        client = _make_client(tmp_output)
        jsonl_path = client._get_incremental_path("src")
        jsonl_path.write_text(
            "not valid json\n"
            + json.dumps({"page_id": "p1", "content_text": "ok"}) + "\n"
        )
        loaded = client.load_incremental("src")
        # Corrupted first line causes json.loads to raise, caught by outer except
        assert loaded == 0

    def test_truncate_partial_jsonl_tail_no_file(self, tmp_output):
        client = _make_client(tmp_output)
        client._truncate_partial_jsonl_tail(tmp_output / "nonexistent.jsonl")

    def test_truncate_partial_jsonl_tail_complete_file(self, tmp_output):
        client = _make_client(tmp_output)
        jsonl_path = tmp_output / "complete.jsonl"
        jsonl_path.write_text('{"page_id": "p1"}\n')
        original = jsonl_path.read_text()
        client._truncate_partial_jsonl_tail(jsonl_path)
        assert jsonl_path.read_text() == original

    def test_truncate_partial_jsonl_tail_incomplete(self, tmp_output):
        client = _make_client(tmp_output)
        jsonl_path = tmp_output / "incomplete.jsonl"
        jsonl_path.write_text('{"page_id": "p1"}\n{"page_id": "p2", "broken')
        client._truncate_partial_jsonl_tail(jsonl_path)
        assert jsonl_path.read_text() == '{"page_id": "p1"}\n'


# ===================================================================
# client.py — _validate_page_content
# ===================================================================


class TestValidatePageContent:
    def test_no_warning_for_valid_page(self, tmp_output, caplog):
        from src.connectors.confluence.client import ConfluenceFullClient

        page = _make_fake_page("1", "Good Page")
        page.content_text = "Some content"
        page.content_html = "<p>Some content</p>"
        ConfluenceFullClient._validate_page_content(page, "1")
        assert "empty" not in caplog.text.lower()

    def test_warns_empty_body(self, tmp_output, caplog):
        import logging

        from src.connectors.confluence.client import ConfluenceFullClient

        page = _make_fake_page("2", "Empty Page")
        page.content_text = ""
        page.content_html = ""
        with caplog.at_level(logging.WARNING):
            ConfluenceFullClient._validate_page_content(page, "2")
        assert "empty" in caplog.text.lower() or "permission" in caplog.text.lower()

    def test_warns_html_but_no_text(self, tmp_output, caplog):
        import logging

        from src.connectors.confluence.client import ConfluenceFullClient

        page = _make_fake_page("3", "HTML Only")
        page.content_text = ""
        page.content_html = "<p>Something</p>"
        with caplog.at_level(logging.WARNING):
            ConfluenceFullClient._validate_page_content(page, "3")
        assert "empty" in caplog.text.lower() or "html" in caplog.text.lower()


# ===================================================================
# client.py — _decode_text_attachment
# ===================================================================


class TestDecodeTextAttachment:
    def test_utf8_content(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = ConfluenceFullClient._decode_text_attachment(b"Hello UTF-8", 1.0)
        assert result.extracted_text == "Hello UTF-8"
        assert result.confidence == 1.0

    def test_cp949_fallback(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient

        cp949_bytes = "한글 텍스트".encode("cp949")
        result = ConfluenceFullClient._decode_text_attachment(cp949_bytes, 0.95)
        assert "한글" in result.extracted_text
        assert result.confidence == 0.8


# ===================================================================
# client.py — _apply_parse_result
# ===================================================================


class TestApplyParseResult:
    def test_copies_fields(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentContent, AttachmentParseResult

        att = AttachmentContent(id="a1", filename="f.pdf",
                                media_type="application/pdf", file_size=100)
        pr = AttachmentParseResult(
            extracted_text="parsed", extracted_tables=[{"h": 1}],
            confidence=0.85, ocr_mode="force", ocr_applied=True,
            ocr_units_attempted=3, ocr_units_extracted=2,
            ocr_units_deferred=1, native_text_chars=50, ocr_text_chars=30,
        )
        ConfluenceFullClient._apply_parse_result(att, pr)
        assert att.extracted_text == "parsed"
        assert att.ocr_confidence == 0.85
        assert att.ocr_applied is True

    def test_none_parse_result(self, tmp_output):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentContent

        att = AttachmentContent(id="a1", filename="f.pdf",
                                media_type="application/pdf", file_size=100)
        ConfluenceFullClient._apply_parse_result(att, None)
        assert att.extracted_text == ""


# ===================================================================
# client.py — _parse_attachment_content dispatch
# ===================================================================


class TestParseAttachmentContent:
    @pytest.mark.asyncio
    async def test_dispatches_pdf(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentParseResult

        fake = AttachmentParseResult(extracted_text="pdf", extracted_tables=[], confidence=0.9)
        with patch("src.connectors.confluence.attachment_parser.AttachmentParser.parse_pdf",
                    return_value=fake):
            result = await ConfluenceFullClient._parse_attachment_content(
                tmp_path / "t.pdf", b"d", "application/pdf", "t.pdf")
        assert result.extracted_text == "pdf"

    @pytest.mark.asyncio
    async def test_dispatches_excel(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentParseResult

        fake = AttachmentParseResult(extracted_text="excel", extracted_tables=[], confidence=0.95)
        with patch("src.connectors.confluence.attachment_parser.AttachmentParser.parse_excel",
                    return_value=fake):
            result = await ConfluenceFullClient._parse_attachment_content(
                tmp_path / "t.xlsx", b"d", "application/vnd.spreadsheet", "t.xlsx")
        assert result.extracted_text == "excel"

    @pytest.mark.asyncio
    async def test_dispatches_ppt(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentParseResult

        fake = AttachmentParseResult(extracted_text="ppt", extracted_tables=[], confidence=0.85)
        with patch("src.connectors.confluence.attachment_parser.AttachmentParser.parse_ppt",
                    return_value=fake):
            result = await ConfluenceFullClient._parse_attachment_content(
                tmp_path / "t.pptx", b"d", "application/vnd.presentation", "t.pptx")
        assert result.extracted_text == "ppt"

    @pytest.mark.asyncio
    async def test_dispatches_word(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentParseResult

        fake = AttachmentParseResult(extracted_text="word", extracted_tables=[], confidence=0.9)
        with patch("src.connectors.confluence.attachment_parser.AttachmentParser.parse_word",
                    return_value=fake):
            result = await ConfluenceFullClient._parse_attachment_content(
                tmp_path / "t.docx", b"d", "application/msword", "t.docx")
        assert result.extracted_text == "word"

    @pytest.mark.asyncio
    async def test_dispatches_image(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient
        from src.connectors.confluence.models import AttachmentParseResult

        fake = AttachmentParseResult(extracted_text="img", extracted_tables=[], confidence=0.7)
        with patch("src.connectors.confluence.attachment_parser.AttachmentParser.parse_image_async",
                    new_callable=AsyncMock, return_value=fake):
            result = await ConfluenceFullClient._parse_attachment_content(
                tmp_path / "t.png", b"d", "image/png", "t.png")
        assert result.extracted_text == "img"

    @pytest.mark.asyncio
    async def test_dispatches_text_file(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = await ConfluenceFullClient._parse_attachment_content(
            tmp_path / "r.txt", b"plain text", "text/plain", "r.txt")
        assert result.extracted_text == "plain text"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_dispatches_csv_file(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient

        # CSV with "text/csv" media type matches "text" check first -> confidence 1.0
        result = await ConfluenceFullClient._parse_attachment_content(
            tmp_path / "d.csv", b"a,b\n1,2", "text/csv", "d.csv")
        assert "a,b" in result.extracted_text
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_dispatches_csv_by_filename(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient

        # CSV with non-text media type dispatches via filename -> confidence 0.95
        result = await ConfluenceFullClient._parse_attachment_content(
            tmp_path / "d.csv", b"a,b\n1,2", "application/octet-stream", "d.csv")
        assert "a,b" in result.extracted_text
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_unsupported_format(self, tmp_path):
        from src.connectors.confluence.client import ConfluenceFullClient

        result = await ConfluenceFullClient._parse_attachment_content(
            tmp_path / "v.mp4", b"bin", "video/mp4", "v.mp4")
        assert result.confidence == 0.0
        assert "Unsupported" in result.extracted_text


# ===================================================================
# client.py — _should_stop_crawl
# ===================================================================


class TestShouldStopCrawl:
    def test_shutdown_stops(self, tmp_output):
        client = _make_client(tmp_output)
        client._shutdown_requested = True
        assert client._should_stop_crawl(None) is True

    def test_max_pages_reached(self, tmp_output):
        client = _make_client(tmp_output)
        client._total_pages_crawled = 10
        assert client._should_stop_crawl(10) is True
        assert client._should_stop_crawl(11) is False

    def test_no_limit(self, tmp_output):
        client = _make_client(tmp_output)
        client._total_pages_crawled = 1000
        assert client._should_stop_crawl(None) is False


# ===================================================================
# client.py — get_all_descendant_page_ids_via_cql
# ===================================================================


class TestCqlDescendants:
    @pytest.mark.asyncio
    async def test_collects_all_descendants(self, tmp_output):
        client = _make_client(tmp_output)

        async def mock_fetch_cql(url, params):
            start = params.get("start", 0)
            if start == 0:
                return [{"id": "p1"}, {"id": "p2"}], 200
            if start == 100:
                return [{"id": "p3"}], 200
            return [], 200

        client._fetch_cql_page = mock_fetch_cql
        ids = await client.get_all_descendant_page_ids_via_cql("root")
        assert ids == {"p1", "p2", "p3"}

    @pytest.mark.asyncio
    async def test_stops_on_shutdown(self, tmp_output):
        client = _make_client(tmp_output)
        client._shutdown_requested = True
        ids = await client.get_all_descendant_page_ids_via_cql("root")
        assert ids == set()

    @pytest.mark.asyncio
    async def test_handles_cql_error(self, tmp_output):
        client = _make_client(tmp_output)

        async def mock_fetch_cql(url, params):
            return None

        client._fetch_cql_page = mock_fetch_cql
        ids = await client.get_all_descendant_page_ids_via_cql("root")
        assert ids == set()


# ===================================================================
# attachment_parser.py — parse_ppt (mocked pptx)
# ===================================================================


class TestParsePpt:
    def test_parse_ppt_basic_text(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Configure with OCR off so we only test native text extraction
        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})
        mock_shape = MagicMock()
        mock_shape.text = "Slide 1 text"
        mock_shape.has_table = False
        mock_shape.shape_type = 0

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP = 6
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE = 13

        with patch.dict("sys.modules", {
            "pptx": mock_pptx, "pptx.enum": mock_pptx.enum,
            "pptx.enum.shapes": mock_pptx.enum.shapes,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")
        assert "Slide 1 text" in result.extracted_text
        assert result.confidence > 0

    def test_parse_ppt_exception(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")
        mock_pptx = MagicMock()
        mock_pptx.Presentation.side_effect = Exception("corrupt pptx")

        with patch.dict("sys.modules", {
            "pptx": mock_pptx, "pptx.enum": MagicMock(),
            "pptx.enum.shapes": MagicMock(),
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "bad.pptx")
        assert result.confidence == 0.0

    def test_parse_legacy_ppt_all_fail(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        with patch("src.connectors.confluence.attachment_parser._try_libreoffice_ppt_convert",
                    return_value=None), \
             patch("src.connectors.confluence.attachment_parser._try_catppt_extract",
                    return_value=None), \
             patch.object(AttachmentParser, "_extract_ppt_olefile", return_value=None):
            result = AttachmentParser._parse_legacy_ppt(tmp_path / "old.ppt")
        assert result.confidence == 0.0


# ===================================================================
# attachment_parser.py — _extract_ppt_slide_content
# ===================================================================


class TestExtractPptSlideContent:
    def test_extracts_text_and_tables(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_pptx = MagicMock()
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP = 6
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE = 13

        text_shape = MagicMock()
        text_shape.text = "Shape text"
        text_shape.has_table = False
        text_shape.shape_type = 0

        cell1, cell2 = MagicMock(), MagicMock()
        cell1.text = "H1"
        cell2.text = "H2"
        row1 = MagicMock()
        row1.cells = [cell1, cell2]
        table_obj = MagicMock()
        table_obj.rows = [row1]
        table_shape = MagicMock()
        table_shape.text = ""
        table_shape.has_table = True
        table_shape.table = table_obj
        table_shape.shape_type = 0

        mock_slide = MagicMock()
        mock_slide.shapes = [text_shape, table_shape]
        mock_slide.has_notes_slide = False

        with patch.dict("sys.modules", {
            "pptx": mock_pptx, "pptx.enum": mock_pptx.enum,
            "pptx.enum.shapes": mock_pptx.enum.shapes,
        }):
            texts, tables, images = AttachmentParser._extract_ppt_slide_content(mock_slide, 1)
        assert "Shape text" in texts
        assert len(tables) == 1

    def test_extracts_notes(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_pptx = MagicMock()
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP = 6
        mock_pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE = 13

        mock_slide = MagicMock()
        mock_slide.shapes = []
        mock_slide.has_notes_slide = True
        mock_slide.notes_slide.notes_text_frame.text = "Speaker notes"

        with patch.dict("sys.modules", {
            "pptx": mock_pptx, "pptx.enum": mock_pptx.enum,
            "pptx.enum.shapes": mock_pptx.enum.shapes,
        }):
            texts, tables, images = AttachmentParser._extract_ppt_slide_content(mock_slide, 1)
        assert any("Notes" in t for t in texts)


# ===================================================================
# attachment_parser.py — _parse_image_sync
# ===================================================================


class TestParseImageSync:
    def test_ocr_disabled(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        orig = AttachmentParser._ocr_policy
        try:
            AttachmentParser._ocr_policy = AttachmentOCRPolicy(
                attachment_ocr_mode="off", ocr_min_text_chars=100,
                ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                ocr_max_images_per_attachment=1, slide_render_enabled=False,
                layout_analysis_enabled=False,
            )
            from PIL import Image as _PilImage
            import io

            img = _PilImage.new("RGB", (100, 100), color="red")
            buf = io.BytesIO()
            img.save(buf, format="PNG")

            result = AttachmentParser._parse_image_sync(
                tmp_path / "test.png", buf.getvalue(), use_ocr=True
            )
            assert result.ocr_skip_reason == "disabled"
        finally:
            AttachmentParser._ocr_policy = orig

    def test_use_ocr_false(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")
        from PIL import Image as _PilImage
        import io

        img = _PilImage.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result = AttachmentParser._parse_image_sync(
            tmp_path / "test.png", buf.getvalue(), use_ocr=False
        )
        assert result.ocr_skip_reason == "disabled"

    def test_image_too_large(self, tmp_path, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")
        from PIL import Image as _PilImage
        import io

        img = _PilImage.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        large_content = buf.getvalue() + b"\x00" * (10_000_000 - len(buf.getvalue()))

        result = AttachmentParser._parse_image_sync(tmp_path / "large.png", large_content)
        assert result.ocr_skip_reason == "image_too_large"


# ===================================================================
# attachment_parser.py — _resize_image_if_needed
# ===================================================================


class TestResizeImageIfNeeded:
    def test_normal_image_unchanged(self, _clean_env):
        from PIL import Image as _PilImage
        from src.connectors.confluence.attachment_parser import AttachmentParser

        img = _PilImage.new("RGB", (200, 200))
        result = AttachmentParser._resize_image_if_needed(img)
        assert result is not None
        assert result.size == (200, 200)

    def test_too_small_returns_none(self, _clean_env):
        from PIL import Image as _PilImage
        from src.connectors.confluence.attachment_parser import AttachmentParser

        img = _PilImage.new("RGB", (20, 20))
        assert AttachmentParser._resize_image_if_needed(img) is None

    def test_large_image_downscaled(self, _clean_env):
        from PIL import Image as _PilImage
        from src.connectors.confluence.attachment_parser import AttachmentParser

        img = _PilImage.new("RGB", (4096, 4096))
        result = AttachmentParser._resize_image_if_needed(img, max_size=2048)
        assert result is not None
        assert result.size[0] <= 2048

    def test_extreme_aspect_ratio_padded(self, _clean_env):
        from PIL import Image as _PilImage
        from src.connectors.confluence.attachment_parser import AttachmentParser

        img = _PilImage.new("RGB", (1000, 50))
        result = AttachmentParser._resize_image_if_needed(img)
        assert result is not None
        w, h = result.size
        assert max(w, h) / max(min(w, h), 1) <= 8.1


# ===================================================================
# attachment_parser.py — _ocr_extract_safe
# ===================================================================


class TestOcrExtractSafe:
    def test_timeout_handling(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        orig = AttachmentParser._ocr_process_pool
        try:
            mock_pool = MagicMock()
            mock_future = MagicMock()
            mock_future.result.side_effect = TimeoutError("timeout")
            mock_pool.submit.return_value = mock_future
            AttachmentParser._ocr_process_pool = mock_pool

            text, conf, tables = AttachmentParser._ocr_extract_safe(b"img", "t.png", timeout=1)
            assert text is None and conf == 0.0
        finally:
            AttachmentParser._ocr_process_pool = orig

    def test_broken_pool_handling(self, _clean_env):
        from concurrent.futures.process import BrokenProcessPool
        from src.connectors.confluence.attachment_parser import AttachmentParser

        orig = AttachmentParser._ocr_process_pool
        try:
            mock_pool = MagicMock()
            mock_future = MagicMock()
            mock_future.result.side_effect = BrokenProcessPool("sigsegv")
            mock_pool.submit.return_value = mock_future
            AttachmentParser._ocr_process_pool = mock_pool

            text, conf, tables = AttachmentParser._ocr_extract_safe(b"crash", "crash.png")
            assert text is None
            assert AttachmentParser._ocr_process_pool is None
        finally:
            AttachmentParser._ocr_process_pool = orig

    def test_generic_exception_handling(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        orig = AttachmentParser._ocr_process_pool
        try:
            mock_pool = MagicMock()
            mock_future = MagicMock()
            mock_future.result.side_effect = RuntimeError("unexpected")
            mock_pool.submit.return_value = mock_future
            AttachmentParser._ocr_process_pool = mock_pool

            text, conf, tables = AttachmentParser._ocr_extract_safe(b"bad", "bad.png")
            assert text is None and conf == 0.0
        finally:
            AttachmentParser._ocr_process_pool = orig


# ===================================================================
# attachment_parser.py — module-level helpers
# ===================================================================


class TestModuleLevelHelpers:
    def test_filter_ocr_noise_removes_repeated(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        result = _filter_ocr_noise("정상\n폐폐폐폐폐\n또 다른")
        assert "정상" in result
        assert "폐폐폐폐폐" not in result

    def test_filter_ocr_noise_keeps_short_lines(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        result = _filter_ocr_noise("OK\nHi")
        assert "OK" in result

    def test_should_ocr_ppt_force(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        p = AttachmentOCRPolicy(attachment_ocr_mode="force", ocr_min_text_chars=100,
                                ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                                ocr_max_images_per_attachment=1,
                                slide_render_enabled=False, layout_analysis_enabled=False)
        assert _should_ocr_ppt(p, 1000) is True

    def test_should_ocr_ppt_off(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        p = AttachmentOCRPolicy(attachment_ocr_mode="off", ocr_min_text_chars=100,
                                ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                                ocr_max_images_per_attachment=1,
                                slide_render_enabled=False, layout_analysis_enabled=False)
        assert _should_ocr_ppt(p, 0) is False

    def test_should_ocr_ppt_auto(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        p = AttachmentOCRPolicy(attachment_ocr_mode="auto", ocr_min_text_chars=100,
                                ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                                ocr_max_images_per_attachment=1,
                                slide_render_enabled=False, layout_analysis_enabled=False)
        assert _should_ocr_ppt(p, 50) is True
        assert _should_ocr_ppt(p, 200) is False

    def test_compute_pdf_confidence(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(True, 0) == 0.9
        assert AttachmentParser._compute_pdf_confidence(True, 5) == 0.7
        assert AttachmentParser._compute_pdf_confidence(False, 0) == 0.0

    def test_determine_ppt_skip_reason(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        off = AttachmentOCRPolicy(attachment_ocr_mode="off", ocr_min_text_chars=100,
                                  ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                                  ocr_max_images_per_attachment=1,
                                  slide_render_enabled=False, layout_analysis_enabled=False)
        assert AttachmentParser._determine_ppt_skip_reason(off, True, 0) == "disabled"

        auto = AttachmentOCRPolicy(attachment_ocr_mode="auto", ocr_min_text_chars=100,
                                   ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
                                   ocr_max_images_per_attachment=1,
                                   slide_render_enabled=False, layout_analysis_enabled=False)
        assert AttachmentParser._determine_ppt_skip_reason(auto, False, 0) == "native_text_sufficient"
        assert AttachmentParser._determine_ppt_skip_reason(auto, True, 5) == "budget_exceeded"
        assert AttachmentParser._determine_ppt_skip_reason(auto, True, 0) is None


# ===================================================================
# output.py — save_results
# ===================================================================


class TestSaveResults:
    def test_save_results_writes_json(self, tmp_path):
        from src.connectors.confluence.output import save_results

        page = _make_fake_page("p1", "Page 1")
        output_path = tmp_path / "output.json"
        save_results([page], output_path, source_info={"key": "TEST"})

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert data["source_info"]["key"] == "TEST"
        assert data["statistics"]["total_pages"] == 1
        assert data["pages"][0]["page_id"] == "p1"

    def test_save_results_with_page_dicts(self, tmp_path):
        from src.connectors.confluence.output import save_results

        page_dicts = [{"page_id": "p1", "title": "P1", "content_text": "hi", "attachments": []}]
        output_path = tmp_path / "output2.json"
        save_results([], output_path, page_dicts=page_dicts)

        data = json.loads(output_path.read_text())
        assert data["statistics"]["total_pages"] == 1


# ===================================================================
# output.py — save_results_from_jsonl
# ===================================================================


class TestSaveResultsFromJsonl:
    def test_streams_jsonl_to_json(self, tmp_path):
        from src.connectors.confluence.output import save_results_from_jsonl

        jsonl_path = tmp_path / "pages.jsonl"
        jsonl_path.write_text(
            json.dumps({"page_id": "p1", "title": "P1", "content_text": "hi", "attachments": []}) + "\n"
            + json.dumps({"page_id": "p2", "title": "P2", "content_text": "there", "attachments": []}) + "\n"
            + json.dumps({"page_id": "p1", "title": "P1 dup", "content_text": "dup", "attachments": []}) + "\n"
        )
        output_path = tmp_path / "output.json"
        count = save_results_from_jsonl(jsonl_path, output_path, source_info={"s": "v"})
        assert count == 2

    def test_empty_jsonl(self, tmp_path):
        from src.connectors.confluence.output import save_results_from_jsonl

        jsonl_path = tmp_path / "empty.jsonl"
        jsonl_path.write_text("")
        count = save_results_from_jsonl(jsonl_path, tmp_path / "output.json")
        assert count == 0


# ===================================================================
# output.py — helper functions
# ===================================================================


class TestOutputHelpers:
    def test_classify_attachment_types(self):
        from src.connectors.confluence.output import _classify_attachment, _new_parsed_stats

        stats = _new_parsed_stats()
        _classify_attachment({"media_type": "application/pdf"}, stats)
        _classify_attachment({"media_type": "application/excel"}, stats)
        _classify_attachment({"media_type": "application/word"}, stats)
        _classify_attachment({"media_type": "image/png"}, stats)
        _classify_attachment({"media_type": "application/zip"}, stats)
        _classify_attachment({"media_type": "text", "parse_error": "err"}, stats)
        assert stats["pdf"] == 1
        assert stats["excel"] == 1
        assert stats["word"] == 1
        assert stats["image"] == 1
        assert stats["other"] == 1
        assert stats["failed"] == 1

    def test_accumulate_page_text_length(self):
        from src.connectors.confluence.output import _accumulate_page_text_length

        page = {"content_text": "hello",
                "attachments": [{"extracted_text": "world"}, {"extracted_text": None}]}
        assert _accumulate_page_text_length(page) == 10

    def test_count_extra_fields(self):
        from src.connectors.confluence.output import _count_extra_fields

        pages = [
            {"labels": ["a", "b"], "comments": ["c"], "emails": []},
            {"labels": ["d"], "macros": ["m1", "m2"]},
        ]
        counts = _count_extra_fields(pages)
        assert counts["total_labels"] == 3
        assert counts["total_macros"] == 2


# ===================================================================
# attachment_parser.py — _resolve_* policy helpers
# ===================================================================


class TestPolicyResolvers:
    def test_resolve_ocr_mode_override(self):
        from src.connectors.confluence.attachment_parser import _resolve_ocr_mode

        assert _resolve_ocr_mode({"attachment_ocr_mode": "auto"}, {}) == "auto"
        assert _resolve_ocr_mode({"attachment_ocr_mode": "off"}, {}) == "off"
        assert _resolve_ocr_mode({"attachment_ocr_mode": "force"}, {}) == "force"

    def test_resolve_ocr_mode_invalid(self):
        from src.connectors.confluence.attachment_parser import _resolve_ocr_mode

        assert _resolve_ocr_mode({"attachment_ocr_mode": "invalid"}, {}) == "force"

    def test_resolve_int_field_priority(self):
        from src.connectors.confluence.attachment_parser import _resolve_int_field

        assert _resolve_int_field({"f": 42}, {}, "f", "ENV_KEY", 10) == 42
        assert _resolve_int_field({}, {"f": 7}, "f", "MISSING_ENV", 10) == 7
        assert _resolve_int_field({}, {}, "f", "MISSING_ENV", 99) == 99

    def test_resolve_bool_field_priority(self):
        from src.connectors.confluence.attachment_parser import _resolve_bool_field

        assert _resolve_bool_field({"f": True}, {}, "f", "ENV", False) is True
        assert _resolve_bool_field({}, {"f": False}, "f", "MISSING_ENV", True) is False
        assert _resolve_bool_field({}, {}, "f", "MISSING_ENV", True) is True


# ===================================================================
# client.py — API wrappers
# ===================================================================


class TestApiWrappers:
    @pytest.mark.asyncio
    async def test_get_user_details_empty_id(self, tmp_output):
        client = _make_client(tmp_output)
        assert await client.get_user_details("") is None

    @pytest.mark.asyncio
    async def test_get_user_details_success(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "displayName": "Test User", "email": "test@test.com",
            "profilePicture": {"path": "/avatar.png"},
        }
        mock_resp.raise_for_status = MagicMock()
        client.client.get = AsyncMock(return_value=mock_resp)

        result = await client.get_user_details("acc1")
        assert result["display_name"] == "Test User"
        assert result["email"] == "test@test.com"

    @pytest.mark.asyncio
    async def test_get_user_details_error(self, tmp_output):
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(side_effect=Exception("api error"))
        assert await client.get_user_details("acc1") is None

    @pytest.mark.asyncio
    async def test_get_comments_success(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{
                "id": "c1",
                "history": {"createdBy": {"displayName": "Author"}, "createdDate": "2024-01-01"},
                "body": {"storage": {"value": "<p>Comment text</p>"}},
            }]
        }
        mock_resp.raise_for_status = MagicMock()
        client.client.get = AsyncMock(return_value=mock_resp)

        comments = await client.get_comments("pg1")
        assert len(comments) == 1
        assert comments[0].author == "Author"

    @pytest.mark.asyncio
    async def test_get_comments_error(self, tmp_output):
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(side_effect=Exception("err"))
        assert await client.get_comments("pg1") == []

    @pytest.mark.asyncio
    async def test_get_labels_success(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"name": "tag1", "prefix": "global"}, {"name": "tag2", "prefix": None}]
        }
        mock_resp.raise_for_status = MagicMock()
        client.client.get = AsyncMock(return_value=mock_resp)

        labels = await client.get_labels("pg1")
        assert len(labels) == 2
        assert labels[0].name == "tag1"

    @pytest.mark.asyncio
    async def test_get_labels_error(self, tmp_output):
        client = _make_client(tmp_output)
        client.client.get = AsyncMock(side_effect=Exception("err"))
        assert await client.get_labels("pg1") == []

    @pytest.mark.asyncio
    async def test_get_attachments_success(self, tmp_output):
        client = _make_client(tmp_output)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [{"id": "att1", "title": "file.pdf"}]}
        client._http_get_with_retry = AsyncMock(return_value=mock_resp)

        attachments = await client.get_attachments("pg1")
        assert len(attachments) == 1

    @pytest.mark.asyncio
    async def test_get_attachments_error(self, tmp_output):
        client = _make_client(tmp_output)
        client._http_get_with_retry = AsyncMock(side_effect=Exception("err"))
        assert await client.get_attachments("pg1") == []


# ===================================================================
# attachment_parser.py — parse_pdf with OCR page
# ===================================================================


class TestParsePdfOcr:
    def test_parse_pdf_textless_page_with_ocr(self, tmp_path, _clean_env):
        """Test textless PDF page triggers OCR fallback."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        # Page 1: has text. Page 2: textless (triggers OCR).
        page1 = MagicMock()
        page1.get_text.return_value = "Real text"
        page1.find_tables.return_value = []

        page2 = MagicMock()
        page2.get_text.return_value = ""  # textless
        page2.find_tables.return_value = []

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([page1, page2]))
        mock_doc.__len__ = MagicMock(return_value=2)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with (
            patch.dict("sys.modules", {"fitz": mock_fitz}),
            patch.object(
                AttachmentParser, "_ocr_pdf_page", return_value="OCR extracted text",
            ),
        ):
            result = AttachmentParser.parse_pdf(tmp_path / "mixed.pdf")

        assert "Real text" in result.extracted_text
        assert "OCR extracted text" in result.extracted_text
        assert result.ocr_applied is True
        assert result.ocr_units_extracted == 1

    def test_parse_pdf_table_extraction(self, tmp_path, _clean_env):
        """Test PDF table extraction from page."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["Col1", "Col2"],
            ["v1", "v2"],
        ]

        mock_tables = MagicMock()
        mock_tables.__iter__ = MagicMock(return_value=iter([mock_table]))

        page = MagicMock()
        page.get_text.return_value = "Some text"
        page.find_tables.return_value = mock_tables

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([page]))
        mock_doc.__len__ = MagicMock(return_value=1)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "tables.pdf")

        assert len(result.extracted_tables) == 1
        assert result.extracted_tables[0]["headers"] == ["Col1", "Col2"]

    def test_parse_pdf_ocr_mode_off(self, tmp_path, _clean_env):
        """When OCR mode is off, textless pages should set skip_reason."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        page = MagicMock()
        page.get_text.return_value = ""  # textless
        page.find_tables.return_value = []

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([page]))
        mock_doc.__len__ = MagicMock(return_value=1)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "ocr_off.pdf")

        assert result.ocr_skip_reason == "disabled"
        assert result.ocr_applied is False

    def test_parse_pdf_ocr_budget_exceeded(self, tmp_path, _clean_env):
        """When OCR budget is exceeded, deferred pages set budget_exceeded skip reason."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run(
            "test", overrides={"ocr_max_pdf_pages": 0},
        )

        page = MagicMock()
        page.get_text.return_value = ""  # textless
        page.find_tables.return_value = []

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([page]))
        mock_doc.__len__ = MagicMock(return_value=1)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = AttachmentParser.parse_pdf(tmp_path / "budget.pdf")

        assert result.ocr_skip_reason == "budget_exceeded"
        assert result.ocr_units_deferred == 1


# ===================================================================
# attachment_parser.py — parse_word .doc fallback
# ===================================================================


class TestParseWordDoc:
    def test_parse_legacy_doc_antiword(self, tmp_path):
        """Test .doc parsing with antiword available."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentParseResult

        fake_result = AttachmentParseResult(
            extracted_text="antiword output",
            extracted_tables=[],
            confidence=0.7,
        )

        with patch(
            "src.connectors.confluence.attachment_parser._try_cli_doc_extract",
            return_value=fake_result,
        ):
            result = AttachmentParser.parse_word(tmp_path / "test.doc")

        assert result.extracted_text == "antiword output"
        assert result.confidence == 0.7

    def test_parse_docx_with_tables_and_text(self, tmp_path):
        """Test .docx with both paragraphs and tables."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_para = MagicMock()
        mock_para.text = "Paragraph text"

        mock_cell_h = MagicMock()
        mock_cell_h.text = "Header"
        mock_cell_v = MagicMock()
        mock_cell_v.text = "Value"
        mock_row_h = MagicMock()
        mock_row_h.cells = [mock_cell_h]
        mock_row_v = MagicMock()
        mock_row_v.cells = [mock_cell_v]

        mock_table = MagicMock()
        mock_table.rows = [mock_row_h, mock_row_v]

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = [mock_table]

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = AttachmentParser.parse_word(tmp_path / "both.docx")

        assert "Paragraph text" in result.extracted_text
        assert len(result.extracted_tables) == 1
        assert result.confidence == 0.9


# ===================================================================
# attachment_parser.py — _build_ppt_result
# ===================================================================


class TestBuildPptResult:
    def test_with_text(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )

        result = AttachmentParser._build_ppt_result(
            "Slide content", [{"slide": 1}], policy,
            should_ocr=True,
            ocr_units_attempted=3,
            ocr_units_extracted=2,
            ocr_units_deferred=0,
            native_text_chars=100,
            ocr_text_chars=50,
        )

        assert result.confidence == 0.85
        assert result.ocr_applied is True
        assert result.ocr_units_attempted == 3
        assert result.native_text_chars == 100

    def test_with_empty_text(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )

        result = AttachmentParser._build_ppt_result(
            "", [], policy,
            should_ocr=False,
            ocr_units_attempted=0,
            ocr_units_extracted=0,
            ocr_units_deferred=0,
            native_text_chars=0,
            ocr_text_chars=0,
        )

        assert result.confidence == 0.0
        assert result.ocr_applied is False

    def test_with_deferred_budget(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )

        result = AttachmentParser._build_ppt_result(
            "Some text", [], policy,
            should_ocr=True,
            ocr_units_attempted=5,
            ocr_units_extracted=3,
            ocr_units_deferred=2,
            native_text_chars=50,
            ocr_text_chars=100,
        )

        assert result.ocr_skip_reason == "budget_exceeded"


# ===================================================================
# attachment_parser.py — _apply_pdf_fallback_if_needed
# ===================================================================


class TestApplyPdfFallback:
    def test_no_fallback_when_text_sufficient(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        text, tables, chars = AttachmentParser._apply_pdf_fallback_if_needed(
            should_ocr=True,
            full_text="A" * 100,
            tables=[],
            ocr_text_chars=0,
            file_path=Path("/tmp/test.pptx"),
            heartbeat_fn=None,
        )

        assert text == "A" * 100

    def test_no_fallback_when_should_ocr_false(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        text, tables, chars = AttachmentParser._apply_pdf_fallback_if_needed(
            should_ocr=False,
            full_text="short",
            tables=[],
            ocr_text_chars=0,
            file_path=Path("/tmp/test.pptx"),
            heartbeat_fn=None,
        )

        assert text == "short"

    def test_fallback_applied_when_sparse(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        with patch.object(
            AttachmentParser, "_ppt_pdf_fallback",
            return_value=("Fallback text with more content", [{"t": 1}], 30),
        ):
            text, tables, chars = AttachmentParser._apply_pdf_fallback_if_needed(
                should_ocr=True,
                full_text="x",
                tables=[],
                ocr_text_chars=0,
                file_path=Path("/tmp/test.pptx"),
                heartbeat_fn=None,
            )

        assert text == "Fallback text with more content"
        assert len(tables) == 1
        assert chars == 30

    def test_fallback_returns_none(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        with patch.object(
            AttachmentParser, "_ppt_pdf_fallback",
            return_value=None,
        ):
            text, tables, chars = AttachmentParser._apply_pdf_fallback_if_needed(
                should_ocr=True,
                full_text="x",
                tables=[{"old": 1}],
                ocr_text_chars=5,
                file_path=Path("/tmp/test.pptx"),
                heartbeat_fn=None,
            )

        assert text == "x"
        assert tables == [{"old": 1}]


# ===================================================================
# attachment_parser.py — _accumulate_ocr_result
# ===================================================================


class TestAccumulateOcrResult:
    def test_accumulate_basic(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []

        item = {
            "attempted": 1, "extracted": 1, "deferred": 0, "chars": 50,
            "text": "[Slide 1] content",
        }
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts)

        assert totals["attempted"] == 1
        assert totals["extracted"] == 1
        assert totals["chars"] == 50
        assert len(text_parts) == 1

    def test_accumulate_with_timed_out(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []
        timed_out = []

        item = {
            "attempted": 1, "extracted": 0, "deferred": 0, "chars": 0,
            "timed_out_item": (3, b"png_data"),
        }
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts, timed_out)

        assert len(timed_out) == 1
        assert timed_out[0] == (3, b"png_data")

    def test_accumulate_no_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 1, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []

        item = {"attempted": 0, "extracted": 0, "deferred": 1, "chars": 0}
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts)

        assert totals["deferred"] == 1
        assert len(text_parts) == 0


# ===================================================================
# attachment_parser.py — _resize_image_if_needed edge cases
# ===================================================================


class TestResizeImageEdgeCases:
    def test_normal_image_passes_through(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.size = (500, 400)

        with patch(
            "src.connectors.confluence.attachment_parser._pad_extreme_aspect_ratio",
            return_value=mock_img,
        ):
            result = AttachmentParser._resize_image_if_needed(mock_img)
        assert result is mock_img

    def test_too_small_image_returns_none(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.size = (20, 20)  # Below _OCR_MIN_DIMENSION (32)

        result = AttachmentParser._resize_image_if_needed(mock_img)
        assert result is None

    def test_too_small_width_returns_none(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.size = (10, 500)

        result = AttachmentParser._resize_image_if_needed(mock_img)
        assert result is None

    def test_large_image_downscaled(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.size = (4000, 3000)

        resized_img = MagicMock()
        resized_img.size = (2048, 1536)
        mock_img.resize.return_value = resized_img

        with (
            patch(
                "src.connectors.confluence.attachment_parser._downscale_image",
                return_value=resized_img,
            ),
            patch(
                "src.connectors.confluence.attachment_parser._pad_extreme_aspect_ratio",
                return_value=resized_img,
            ),
        ):
            result = AttachmentParser._resize_image_if_needed(mock_img)
        assert result is resized_img

    def test_large_image_downscale_too_small(self, _clean_env):
        """If downscaling would make image too small, returns None."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.size = (3000, 10)  # Very thin image

        with patch(
            "src.connectors.confluence.attachment_parser._downscale_image",
            return_value=None,
        ):
            result = AttachmentParser._resize_image_if_needed(mock_img)
        assert result is None


# ===================================================================
# attachment_parser.py — _compute_pdf_confidence
# ===================================================================


class TestComputePdfConfidence:
    def test_text_only(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(True, 0) == 0.9

    def test_text_with_ocr(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(True, 3) == 0.7

    def test_no_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(False, 0) == 0.0


# ===================================================================
# attachment_parser.py — _filter_ocr_noise
# ===================================================================


class TestFilterOcrNoise:
    def test_removes_repeated_chars(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Good line\n폐폐폐폐폐\nAnother good line"
        result = _filter_ocr_noise(text)
        assert "폐폐폐폐폐" not in result
        assert "Good line" in result
        assert "Another good line" in result

    def test_keeps_short_lines(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "OK\naaaa\nHello"
        result = _filter_ocr_noise(text)
        assert "OK" in result
        assert "aaaa" in result  # len < 5 so not filtered

    def test_keeps_diverse_chars(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Hello World"
        result = _filter_ocr_noise(text)
        assert "Hello World" in result

    def test_empty_lines_removed(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "\n\n\n"
        result = _filter_ocr_noise(text)
        assert result == ""


# ===================================================================
# attachment_parser.py — _determine_ppt_skip_reason
# ===================================================================


class TestDeterminePptSkipReason:
    def test_off_mode(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="off",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )
        assert AttachmentParser._determine_ppt_skip_reason(policy, True, 0) == "disabled"

    def test_auto_with_sufficient_text(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )
        assert AttachmentParser._determine_ppt_skip_reason(policy, False, 0) == "native_text_sufficient"

    def test_budget_exceeded(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )
        assert AttachmentParser._determine_ppt_skip_reason(policy, True, 5) == "budget_exceeded"

    def test_no_skip_reason(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=100,
            ocr_max_ppt_slides=100,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True,
            layout_analysis_enabled=True,
        )
        assert AttachmentParser._determine_ppt_skip_reason(policy, True, 0) is None


# ===================================================================
# attachment_parser.py — _extract_pdf_page_tables
# ===================================================================


class TestExtractPdfPageTables:
    def test_extracts_tables(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["H1", "H2"],
            ["a", "b"],
        ]
        page = MagicMock()
        page.find_tables.return_value = [mock_table]

        tables = AttachmentParser._extract_pdf_page_tables(page, 1)
        assert len(tables) == 1
        assert tables[0]["page"] == 1
        assert tables[0]["headers"] == ["H1", "H2"]

    def test_skips_single_row_tables(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_table = MagicMock()
        mock_table.extract.return_value = [["H1"]]  # only header
        page = MagicMock()
        page.find_tables.return_value = [mock_table]

        tables = AttachmentParser._extract_pdf_page_tables(page, 1)
        assert tables == []

    def test_handles_exception(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        page = MagicMock()
        page.find_tables.side_effect = Exception("corrupt")

        tables = AttachmentParser._extract_pdf_page_tables(page, 1)
        assert tables == []


# ===================================================================
# attachment_parser.py — _extract_word_tables
# ===================================================================


class TestExtractWordTables:
    def test_multiple_tables(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        cells_h = [MagicMock(text="A"), MagicMock(text="B")]
        cells_v = [MagicMock(text="1"), MagicMock(text="2")]
        row_h = MagicMock(cells=cells_h)
        row_v = MagicMock(cells=cells_v)
        table = MagicMock(rows=[row_h, row_v])
        doc = MagicMock(tables=[table])

        tables = AttachmentParser._extract_word_tables(doc)
        assert len(tables) == 1
        assert tables[0]["table_index"] == 1
        assert tables[0]["headers"] == ["A", "B"]

    def test_empty_table(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        table = MagicMock(rows=[])
        doc = MagicMock(tables=[table])

        tables = AttachmentParser._extract_word_tables(doc)
        assert tables == []


# ===================================================================
# attachment_parser.py — _filter_ocr_noise
# ===================================================================


class TestFilterOcrNoise:
    def test_removes_repeated_chars(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Good line\n폐폐폐폐폐\nAnother good"
        result = _filter_ocr_noise(text)
        assert "폐폐폐폐폐" not in result
        assert "Good line" in result
        assert "Another good" in result

    def test_keeps_short_lines(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Hi\nAAAA\nOK"
        result = _filter_ocr_noise(text)
        # "AAAA" is only 4 chars, below the 5-char threshold
        assert "AAAA" in result

    def test_removes_five_char_repeated(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "AAAAA"
        result = _filter_ocr_noise(text)
        assert result == ""

    def test_keeps_normal_text(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Normal text here\nAnother line"
        result = _filter_ocr_noise(text)
        assert "Normal text here" in result
        assert "Another line" in result

    def test_empty_input(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        assert _filter_ocr_noise("") == ""

    def test_skips_blank_lines(self):
        from src.connectors.confluence.attachment_parser import _filter_ocr_noise

        text = "Hello\n\n\nWorld"
        result = _filter_ocr_noise(text)
        assert "Hello" in result
        assert "World" in result


# ===================================================================
# attachment_parser.py — _should_ocr_ppt
# ===================================================================


class TestShouldOcrPpt:
    def test_off_mode(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="off", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        assert _should_ocr_ppt(policy, 200) is False

    def test_force_mode(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        assert _should_ocr_ppt(policy, 200) is True

    def test_auto_mode_sufficient_text(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        assert _should_ocr_ppt(policy, 200) is False

    def test_auto_mode_insufficient_text(self):
        from src.connectors.confluence.attachment_parser import _should_ocr_ppt
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        assert _should_ocr_ppt(policy, 50) is True


# ===================================================================
# attachment_parser.py — _determine_ppt_skip_reason
# ===================================================================


class TestDeterminePptSkipReason:
    def _make_policy(self, mode="force"):
        from src.connectors.confluence.models import AttachmentOCRPolicy

        return AttachmentOCRPolicy(
            attachment_ocr_mode=mode, ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )

    def test_off_mode(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy("off")
        assert AttachmentParser._determine_ppt_skip_reason(policy, True, 0) == "disabled"

    def test_auto_mode_sufficient_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy("auto")
        result = AttachmentParser._determine_ppt_skip_reason(policy, False, 0)
        assert result == "native_text_sufficient"

    def test_budget_exceeded(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy("force")
        result = AttachmentParser._determine_ppt_skip_reason(policy, True, 5)
        assert result == "budget_exceeded"

    def test_no_skip_reason(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy("force")
        result = AttachmentParser._determine_ppt_skip_reason(policy, True, 0)
        assert result is None


# ===================================================================
# attachment_parser.py — parse_ppt
# ===================================================================


class TestParsePpt:
    def _make_policy(self, mode="force", slide_render=False):
        from src.connectors.confluence.models import AttachmentOCRPolicy

        return AttachmentOCRPolicy(
            attachment_ocr_mode=mode, ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=slide_render, layout_analysis_enabled=False,
        )

    def test_parse_pptx_text_shapes(self, tmp_path, _clean_env):
        """PPTX with text shapes extracts text correctly."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        mock_shape = MagicMock()
        mock_shape.text = "Slide text content"
        mock_shape.has_table = False
        mock_shape.shape_type = 0  # not GROUP, not PICTURE

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")

        assert "Slide text content" in result.extracted_text
        assert result.confidence == 0.85

    def test_parse_pptx_with_table(self, tmp_path, _clean_env):
        """PPTX with table shapes extracts table data."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        mock_cell_h1 = MagicMock()
        mock_cell_h1.text = "Header1"
        mock_cell_h2 = MagicMock()
        mock_cell_h2.text = "Header2"
        mock_row_h = MagicMock()
        mock_row_h.cells = [mock_cell_h1, mock_cell_h2]

        mock_cell_v1 = MagicMock()
        mock_cell_v1.text = "Value1"
        mock_cell_v2 = MagicMock()
        mock_cell_v2.text = "Value2"
        mock_row_v = MagicMock()
        mock_row_v.cells = [mock_cell_v1, mock_cell_v2]

        mock_table = MagicMock()
        mock_table.rows = [mock_row_h, mock_row_v]

        mock_shape = MagicMock()
        mock_shape.text = ""
        mock_shape.has_table = True
        mock_shape.table = mock_table
        mock_shape.shape_type = 0

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")

        assert len(result.extracted_tables) == 1
        assert result.extracted_tables[0]["headers"] == ["Header1", "Header2"]

    def test_parse_pptx_with_notes(self, tmp_path, _clean_env):
        """PPTX with slide notes extracts notes text."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        mock_shape = MagicMock()
        mock_shape.text = "Main content"
        mock_shape.has_table = False
        mock_shape.shape_type = 0

        mock_notes_frame = MagicMock()
        mock_notes_frame.text = "Speaker notes here"

        mock_notes_slide = MagicMock()
        mock_notes_slide.notes_text_frame = mock_notes_frame

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = True
        mock_slide.notes_slide = mock_notes_slide

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")

        assert "[Notes] Speaker notes here" in result.extracted_text

    def test_parse_pptx_empty_slides(self, tmp_path, _clean_env):
        """PPTX with empty slides returns zero confidence."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        mock_slide = MagicMock()
        mock_slide.shapes = []
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")

        assert result.confidence == 0.0

    def test_parse_pptx_exception(self, tmp_path, _clean_env):
        """PPTX parse error returns error result."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        mock_pptx = MagicMock()
        mock_pptx.Presentation.side_effect = Exception("corrupt pptx")

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": MagicMock(),
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "bad.pptx")

        assert result.confidence == 0.0
        assert "오류" in result.extracted_text or "corrupt" in result.extracted_text

    def test_parse_legacy_ppt_path(self, tmp_path, _clean_env):
        """Legacy .ppt file delegates to _parse_legacy_ppt."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentParseResult

        AttachmentParser.configure_run("test")
        fake_result = AttachmentParseResult(
            extracted_text="legacy text", extracted_tables=[], confidence=0.5,
        )
        with patch.object(
            AttachmentParser, "_parse_legacy_ppt", return_value=fake_result
        ):
            result = AttachmentParser.parse_ppt(tmp_path / "old.ppt")

        assert result.extracted_text == "legacy text"

    def test_parse_pptx_ocr_disabled(self, tmp_path, _clean_env):
        """OCR disabled mode still extracts native text."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        mock_shape = MagicMock()
        mock_shape.text = "Native text only"
        mock_shape.has_table = False
        mock_shape.shape_type = 0

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "test.pptx")

        assert "Native text only" in result.extracted_text
        assert result.ocr_applied is False
        assert result.ocr_skip_reason == "disabled"

    def test_parse_pptx_multiple_slides(self, tmp_path, _clean_env):
        """PPTX with multiple slides."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        slides = []
        for i in range(3):
            shape = MagicMock()
            shape.text = f"Slide {i + 1} text"
            shape.has_table = False
            shape.shape_type = 0
            slide = MagicMock()
            slide.shapes = [shape]
            slide.has_notes_slide = False
            slides.append(slide)

        mock_prs = MagicMock()
        mock_prs.slides = slides

        mock_pptx = MagicMock()
        mock_pptx.Presentation.return_value = mock_prs

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        with patch.dict("sys.modules", {
            "pptx": mock_pptx,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum,
        }):
            result = AttachmentParser.parse_ppt(tmp_path / "multi.pptx")

        assert "Slide 1 text" in result.extracted_text
        assert "Slide 3 text" in result.extracted_text
        assert result.confidence == 0.85


# ===================================================================
# attachment_parser.py — _ocr_slide_image
# ===================================================================


class TestOcrSlideImage:
    def test_successful_ocr_with_layout(self, _clean_env):
        """OCR with layout analysis succeeding."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_img = MagicMock()
        mock_img.mode = "RGB"
        mock_img.copy.return_value = mock_img

        # Create a minimal valid PNG
        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value="Layout OCR text",
        ), patch(
            "src.connectors.confluence.attachment_parser._filter_ocr_noise",
            side_effect=lambda x: x,
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
            side_effect=lambda img, sn: img,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=True, layout_analysis=True, postprocess=False,
            )

        assert result == "Layout OCR text"

    def test_fallback_to_standard_ocr(self, _clean_env):
        """Falls back to standard OCR when layout analysis returns nothing."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value=None,
        ), patch.object(
            AttachmentParser, "_fallback_standard_ocr", return_value="Standard OCR text",
        ), patch(
            "src.connectors.confluence.attachment_parser._filter_ocr_noise",
            side_effect=lambda x: x,
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
            side_effect=lambda img, sn: img,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=True, layout_analysis=True, postprocess=False,
            )

        assert result == "Standard OCR text"

    def test_returns_none_when_no_ocr(self, _clean_env):
        """Returns None when both layout and standard OCR fail."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value=None,
        ), patch.object(
            AttachmentParser, "_fallback_standard_ocr", return_value=None,
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
            side_effect=lambda img, sn: img,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=True, layout_analysis=True, postprocess=False,
            )

        assert result is None

    def test_postprocess_applied(self, _clean_env):
        """Post-processing is applied when postprocess=True."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value="Raw text",
        ), patch(
            "src.connectors.confluence.attachment_parser._postprocess_slide_text",
            return_value="Processed text",
        ), patch(
            "src.connectors.confluence.attachment_parser._filter_ocr_noise",
            side_effect=lambda x: x,
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
            side_effect=lambda img, sn: img,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=True, layout_analysis=True, postprocess=True,
            )

        assert result == "Processed text"

    def test_exception_returns_none(self, _clean_env):
        """Exception during OCR returns None."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        # Invalid PNG bytes
        result = AttachmentParser._ocr_slide_image(
            b"not a png", 1, preprocess=True, layout_analysis=True, postprocess=False,
        )
        assert result is None

    def test_no_preprocess(self, _clean_env):
        """Skips preprocessing when preprocess=False."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value="OCR text",
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
        ) as mock_preprocess, patch(
            "src.connectors.confluence.attachment_parser._filter_ocr_noise",
            side_effect=lambda x: x,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=False, layout_analysis=True, postprocess=False,
            )

        mock_preprocess.assert_not_called()
        assert result == "OCR text"

    def test_noise_filtered(self, _clean_env):
        """Noise filtering is applied to OCR text."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch(
            "src.connectors.confluence.attachment_parser._try_slide_layout_ocr",
            return_value="Good text\n폐폐폐폐폐",
        ), patch(
            "src.connectors.confluence.attachment_parser._preprocess_slide_image",
            side_effect=lambda img, sn: img,
        ):
            result = AttachmentParser._ocr_slide_image(
                png_bytes, 1, preprocess=True, layout_analysis=True, postprocess=False,
            )

        assert "Good text" in result
        assert "폐폐폐폐폐" not in result


# ===================================================================
# attachment_parser.py — _shape_ocr_pass
# ===================================================================


class TestShapeOcrPass:
    def test_no_images(self, _clean_env):
        """Empty image list returns empty results."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        text_parts, attempted, extracted, deferred, chars = (
            AttachmentParser._shape_ocr_pass(
                [], policy, 5, None, True, True, set(),
            )
        )
        assert text_parts == []
        assert attempted == 0
        assert extracted == 0

    def test_ocr_not_available(self, _clean_env):
        """OCR unavailable breaks out of loop."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        with patch.object(
            AttachmentParser, "_process_one_shape_ocr", return_value=None,
        ):
            text_parts, attempted, extracted, deferred, chars = (
                AttachmentParser._shape_ocr_pass(
                    [(1, b"img")], policy, 5, None, True, True, set(),
                )
            )
        assert attempted == 0

    def test_processes_images(self, _clean_env):
        """Processes image shapes and accumulates results."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        item_result = {
            "attempted": 1, "extracted": 1, "deferred": 0, "chars": 50,
            "text": "[Slide 1 Image OCR]\nSome text", "timed_out_item": None,
        }
        with patch.object(
            AttachmentParser, "_process_one_shape_ocr", return_value=item_result,
        ), patch.object(
            AttachmentParser, "_retry_timed_out_images",
            return_value={"text_parts": [], "attempted": 0, "extracted": 0,
                          "deferred": 0, "chars": 0},
        ):
            text_parts, attempted, extracted, deferred, chars = (
                AttachmentParser._shape_ocr_pass(
                    [(1, b"img")], policy, 5, None, True, True, set(),
                )
            )
        assert attempted == 1
        assert extracted == 1
        assert len(text_parts) == 1


# ===================================================================
# attachment_parser.py — _retry_timed_out_images
# ===================================================================


class TestRetryTimedOutImages:
    def test_empty_list_returns_zeros(self, _clean_env):
        """Empty timed-out list returns empty result."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        result = AttachmentParser._retry_timed_out_images(
            [], policy, True, set(), set(), None,
        )
        assert result["attempted"] == 0
        assert result["extracted"] == 0

    def test_retries_timed_out(self, _clean_env):
        """Retries timed-out images."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        retry_item = {
            "attempted": 1, "extracted": 1, "deferred": 0, "chars": 30,
            "text": "[Slide 1 Image OCR]\nRetried text",
        }
        with patch.object(
            AttachmentParser, "_retry_one_image", return_value=retry_item,
        ):
            result = AttachmentParser._retry_timed_out_images(
                [(1, b"img")], policy, True, set(), set(), None,
            )
        assert result["extracted"] == 1
        assert len(result["text_parts"]) == 1


# ===================================================================
# attachment_parser.py — _retry_one_image
# ===================================================================


class TestRetryOneImage:
    def _make_policy(self, max_slides=10):
        from src.connectors.confluence.models import AttachmentOCRPolicy

        return AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=max_slides,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )

    def test_successful_retry(self, _clean_env):
        """Successful retry returns text and increments extracted."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy()
        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            return_value=("Retried text", 0.8, []),
        ):
            result = AttachmentParser._retry_one_image(
                1, b"png_bytes", policy, False, set(), set(),
            )
        assert result["text"] is not None
        assert "Retried text" in result["text"]
        assert result["extracted"] == 1
        assert result["attempted"] == 1

    def test_deferred_budget_exceeded(self, _clean_env):
        """Budget exceeded defers the image."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy(max_slides=1)
        attempted = {99}  # already 1 attempted, budget is 1
        result = AttachmentParser._retry_one_image(
            2, b"png_bytes", policy, False, attempted, set(),
        )
        assert result["deferred"] == 1

    def test_low_confidence_fails(self, _clean_env):
        """Low confidence OCR result is not extracted."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy()
        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            return_value=("bad text", 0.1, []),
        ):
            result = AttachmentParser._retry_one_image(
                1, b"png_bytes", policy, False, set(), set(),
            )
        assert result["text"] is None
        assert result["extracted"] == 0

    def test_exception_handled(self, _clean_env):
        """Exception during retry is caught gracefully."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        policy = self._make_policy()
        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            side_effect=Exception("OCR crash"),
        ):
            result = AttachmentParser._retry_one_image(
                1, b"png_bytes", policy, False, set(), set(),
            )
        assert result["text"] is None
        assert result["extracted"] == 0


# ===================================================================
# attachment_parser.py — _extract_ppt_slide_content
# ===================================================================


class TestExtractPptSlideContent:
    def test_extracts_text_table_and_image(self, _clean_env):
        """Extracts text, tables, and image shapes from a slide."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        # Text shape
        text_shape = MagicMock()
        text_shape.text = "Text content"
        text_shape.has_table = False
        text_shape.shape_type = 0

        # Table shape
        mock_cell_h = MagicMock()
        mock_cell_h.text = "H"
        mock_row_h = MagicMock()
        mock_row_h.cells = [mock_cell_h]
        mock_table = MagicMock()
        mock_table.rows = [mock_row_h]

        table_shape = MagicMock()
        table_shape.text = ""
        table_shape.has_table = True
        table_shape.table = mock_table
        table_shape.shape_type = 0

        mock_slide = MagicMock()
        mock_slide.shapes = [text_shape, table_shape]
        mock_slide.has_notes_slide = False

        with patch.dict("sys.modules", {"pptx.enum.shapes": mock_enum}):
            texts, tables, images = AttachmentParser._extract_ppt_slide_content(
                mock_slide, 1,
            )

        assert "Text content" in texts
        assert len(tables) == 1

    def test_empty_slide(self, _clean_env):
        """Empty slide returns empty lists."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_enum = MagicMock()
        mock_enum.MSO_SHAPE_TYPE.GROUP = 999
        mock_enum.MSO_SHAPE_TYPE.PICTURE = 998

        mock_slide = MagicMock()
        mock_slide.shapes = []
        mock_slide.has_notes_slide = False

        with patch.dict("sys.modules", {"pptx.enum.shapes": mock_enum}):
            texts, tables, images = AttachmentParser._extract_ppt_slide_content(
                mock_slide, 1,
            )

        assert texts == []
        assert tables == []
        assert images == []


# ===================================================================
# attachment_parser.py — _render_and_ocr_slides
# ===================================================================


class TestRenderAndOcrSlides:
    def test_render_success(self, tmp_path, _clean_env):
        """Successful slide rendering and OCR."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=False,
        )

        mock_render = MagicMock(return_value=[(1, b"png1"), (2, b"png2")])
        with patch.dict("sys.modules", {
            "scripts": MagicMock(),
            "scripts.slide_renderer": MagicMock(render_slides_as_images=mock_render),
        }), patch.object(
            AttachmentParser, "_ocr_slide_image", return_value="OCR result",
        ):
            result = AttachmentParser._render_and_ocr_slides(
                tmp_path / "test.pptx", policy, None, True, True,
            )

        slide_rendered, text_parts, attempted, extracted, deferred, chars, slides = result
        assert slide_rendered is True
        assert extracted == 2

    def test_render_no_slides(self, tmp_path, _clean_env):
        """Rendering returns empty list."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=False,
        )

        mock_render = MagicMock(return_value=[])
        with patch.dict("sys.modules", {
            "scripts": MagicMock(),
            "scripts.slide_renderer": MagicMock(render_slides_as_images=mock_render),
        }):
            result = AttachmentParser._render_and_ocr_slides(
                tmp_path / "test.pptx", policy, None, True, True,
            )

        slide_rendered, text_parts, attempted, extracted, deferred, chars, slides = result
        assert slide_rendered is False

    def test_render_exception_fallback(self, tmp_path, _clean_env):
        """Rendering exception falls back gracefully."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=False,
        )

        with patch.dict("sys.modules", {
            "scripts": MagicMock(),
            "scripts.slide_renderer": MagicMock(
                render_slides_as_images=MagicMock(side_effect=Exception("render fail")),
            ),
        }):
            result = AttachmentParser._render_and_ocr_slides(
                tmp_path / "test.pptx", policy, None, True, True,
            )

        slide_rendered = result[0]
        assert slide_rendered is False


# ===================================================================
# attachment_parser.py — parse_excel more paths
# ===================================================================


class TestParseExcelPaths:
    def test_multiple_sheets(self, tmp_path):
        """Excel with multiple sheets extracts all sheets."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        sheets = {}
        for name in ["Sheet1", "Sheet2"]:
            sheet = MagicMock()
            sheet.iter_rows.return_value = [
                (f"{name}_H1", f"{name}_H2"),
                (f"{name}_V1", f"{name}_V2"),
            ]
            sheets[name] = sheet

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1", "Sheet2"]
        mock_wb.__getitem__ = MagicMock(side_effect=lambda k: sheets[k])

        mock_openpyxl = MagicMock()
        mock_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = AttachmentParser.parse_excel(tmp_path / "multi.xlsx")

        assert len(result.extracted_tables) == 2
        assert result.confidence == 0.95

    def test_more_than_10_rows_truncated(self, tmp_path):
        """Excel with >10 data rows shows truncation message."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        rows = [("H1", "H2")] + [(f"r{i}", f"v{i}") for i in range(15)]
        mock_sheet = MagicMock()
        mock_sheet.iter_rows.return_value = rows

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_sheet)

        mock_openpyxl = MagicMock()
        mock_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": mock_openpyxl}):
            result = AttachmentParser.parse_excel(tmp_path / "big.xlsx")

        assert "외 5행" in result.extracted_text


# ===================================================================
# attachment_parser.py — parse_word more paths
# ===================================================================


class TestParseWordPaths:
    def test_parse_doc_legacy(self, tmp_path, _clean_env):
        """Legacy .doc delegates to _parse_legacy_doc."""
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentParseResult

        fake_result = AttachmentParseResult(
            extracted_text="Legacy doc text", extracted_tables=[], confidence=0.7,
        )
        with patch.object(
            AttachmentParser, "_parse_legacy_doc", return_value=fake_result,
        ):
            result = AttachmentParser.parse_word(tmp_path / "old.doc")

        assert result.extracted_text == "Legacy doc text"

    def test_parse_docx_empty_paragraphs(self, tmp_path):
        """Docx with only whitespace paragraphs returns zero confidence."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_para = MagicMock()
        mock_para.text = "   "  # whitespace only

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []

        mock_docx_module = MagicMock()
        mock_docx_module.Document.return_value = mock_doc

        with patch.dict("sys.modules", {"docx": mock_docx_module}):
            result = AttachmentParser.parse_word(tmp_path / "empty.docx")

        assert result.confidence == 0.0


# ===================================================================
# attachment_parser.py — _parse_image_sync
# ===================================================================


class TestParseImageSync:
    def test_ocr_off_mode(self, tmp_path, _clean_env):
        """OCR off returns metadata-only result."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "off"})

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        result = AttachmentParser._parse_image_sync(
            tmp_path / "test.png", content, use_ocr=True,
        )
        assert result.ocr_skip_reason == "disabled"
        assert result.confidence == 0.5

    def test_ocr_disabled_by_use_ocr_flag(self, tmp_path, _clean_env):
        """use_ocr=False returns metadata-only result."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "force"})

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        result = AttachmentParser._parse_image_sync(
            tmp_path / "test.png", content, use_ocr=False,
        )
        assert result.ocr_skip_reason == "disabled"

    def test_image_too_large(self, tmp_path, _clean_env):
        """Large image is skipped."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "force"})

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        small_content = buf.getvalue()

        # Fake large content but with valid PIL header
        large_content = small_content + b"\x00" * (10_000_001 - len(small_content))

        result = AttachmentParser._parse_image_sync(
            tmp_path / "big.png", large_content, use_ocr=True,
        )
        assert result.ocr_skip_reason == "image_too_large"

    def test_budget_zero_images(self, tmp_path, _clean_env):
        """Zero budget for images returns deferred."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run(
            "test",
            overrides={
                "attachment_ocr_mode": "force",
                "ocr_max_images_per_attachment": 0,
            },
        )

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        result = AttachmentParser._parse_image_sync(
            tmp_path / "test.png", content, use_ocr=True,
        )
        assert result.ocr_skip_reason == "budget_exceeded"
        assert result.ocr_units_deferred == 1

    def test_exception_returns_error(self, tmp_path, _clean_env):
        """Exception returns error result."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test")

        result = AttachmentParser._parse_image_sync(
            tmp_path / "bad.png", b"not an image", use_ocr=True,
        )
        assert result.confidence == 0.0
        assert "오류" in result.extracted_text

    def test_ocr_performed_successfully(self, tmp_path, _clean_env):
        """Successful OCR returns extracted text."""
        from src.connectors.confluence.attachment_parser import AttachmentParser

        AttachmentParser.configure_run("test", overrides={"attachment_ocr_mode": "force"})

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        fake_result = MagicMock()
        fake_result.extracted_text = "OCR text"
        with patch.object(
            AttachmentParser, "_perform_image_ocr", return_value=fake_result,
        ):
            result = AttachmentParser._parse_image_sync(
                tmp_path / "test.png", content, use_ocr=True,
            )

        assert result.extracted_text == "OCR text"


# ===================================================================
# attachment_parser.py — _accumulate_ocr_result
# ===================================================================


class TestAccumulateOcrResult:
    def test_accumulates_basic(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []
        item = {"attempted": 1, "extracted": 1, "deferred": 0, "chars": 50,
                "text": "OCR text", "timed_out_item": None}
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts)
        assert totals["attempted"] == 1
        assert totals["extracted"] == 1
        assert totals["chars"] == 50
        assert text_parts == ["OCR text"]

    def test_accumulates_timed_out(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []
        timed_out = []
        item = {"attempted": 1, "extracted": 0, "deferred": 0, "chars": 0,
                "text": None, "timed_out_item": (1, b"img")}
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts, timed_out)
        assert timed_out == [(1, b"img")]
        assert text_parts == []

    def test_no_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        totals = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        text_parts = []
        item = {"attempted": 0, "extracted": 0, "deferred": 1, "chars": 0}
        AttachmentParser._accumulate_ocr_result(item, totals, text_parts)
        assert totals["deferred"] == 1
        assert text_parts == []


# ===================================================================
# attachment_parser.py — _collect_image_shape
# ===================================================================


class TestCollectImageShape:
    def test_collects_large_image(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        shape = MagicMock()
        shape.image.blob = b"x" * 20_000
        images = []
        AttachmentParser._collect_image_shape(shape, 1, images)
        assert len(images) == 1
        assert images[0] == (1, b"x" * 20_000)

    def test_skips_small_image(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        shape = MagicMock()
        shape.image.blob = b"x" * 5_000
        images = []
        AttachmentParser._collect_image_shape(shape, 1, images)
        assert len(images) == 0

    def test_handles_exception(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        shape = MagicMock()
        shape.image.blob = property(lambda s: (_ for _ in ()).throw(Exception("fail")))
        type(shape.image).blob = property(lambda s: (_ for _ in ()).throw(Exception("fail")))
        images = []
        # Should not raise
        AttachmentParser._collect_image_shape(shape, 1, images)
        assert len(images) == 0


# ===================================================================
# attachment_parser.py — _extract_pptx_table
# ===================================================================


class TestExtractPptxTable:
    def test_basic_table(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_cell_h1 = MagicMock()
        mock_cell_h1.text = "Name"
        mock_cell_h2 = MagicMock()
        mock_cell_h2.text = "Age"
        mock_row_h = MagicMock()
        mock_row_h.cells = [mock_cell_h1, mock_cell_h2]

        mock_cell_v1 = MagicMock()
        mock_cell_v1.text = "Alice"
        mock_cell_v2 = MagicMock()
        mock_cell_v2.text = "30"
        mock_row_v = MagicMock()
        mock_row_v.cells = [mock_cell_v1, mock_cell_v2]

        mock_table = MagicMock()
        mock_table.rows = [mock_row_h, mock_row_v]

        result = AttachmentParser._extract_pptx_table(mock_table, 1)
        assert result is not None
        assert result["slide"] == 1
        assert result["headers"] == ["Name", "Age"]
        assert len(result["rows"]) == 1

    def test_empty_table(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_table = MagicMock()
        mock_table.rows = []

        result = AttachmentParser._extract_pptx_table(mock_table, 1)
        assert result is None

    def test_header_only_table(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        mock_cell = MagicMock()
        mock_cell.text = "Header"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell]

        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        result = AttachmentParser._extract_pptx_table(mock_table, 1)
        assert result is not None
        assert result["rows"] == []


# ===================================================================
# attachment_parser.py — _resize_image_if_needed
# ===================================================================


class TestResizeImageIfNeeded:
    def test_small_image_rejected(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (10, 10))
        result = AttachmentParser._resize_image_if_needed(img)
        assert result is None

    def test_normal_image_unchanged(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (200, 200))
        result = AttachmentParser._resize_image_if_needed(img)
        assert result is not None
        assert result.size == (200, 200)

    def test_large_image_downscaled(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (4000, 4000))
        result = AttachmentParser._resize_image_if_needed(img, max_size=2048)
        assert result is not None
        assert result.size[0] <= 2048
        assert result.size[1] <= 2048

    def test_extreme_aspect_ratio_padded(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (1000, 50))
        result = AttachmentParser._resize_image_if_needed(img)
        assert result is not None
        # Should have been padded to fix extreme aspect ratio
        assert result.size[1] > 50


# ===================================================================
# attachment_parser.py — _build_ppt_result
# ===================================================================


class TestBuildPptResult:
    def test_with_content(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        result = AttachmentParser._build_ppt_result(
            "Full text here", [{"t": 1}], policy, True,
            5, 3, 2, 100, 50,
        )
        assert result.confidence == 0.85
        assert result.ocr_applied is True
        assert result.ocr_units_attempted == 5
        assert result.ocr_units_extracted == 3
        assert result.ocr_units_deferred == 2

    def test_empty_content(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=True, layout_analysis_enabled=True,
        )
        result = AttachmentParser._build_ppt_result(
            "", [], policy, True, 0, 0, 0, 0, 0,
        )
        assert result.confidence == 0.0


# ===================================================================
# attachment_parser.py — _compute_pdf_confidence
# ===================================================================


class TestComputePdfConfidence:
    def test_text_only(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(True, 0) == 0.9

    def test_text_with_ocr(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(True, 3) == 0.7

    def test_no_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        assert AttachmentParser._compute_pdf_confidence(False, 0) == 0.0


# ===================================================================
# attachment_parser.py — _process_textless_pdf_page
# ===================================================================


class TestProcessTextlessPdfPage:
    def test_off_mode_returns_early(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="off", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        text_parts = []
        counters = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}
        AttachmentParser._process_textless_pdf_page(
            MagicMock(), 1, 5, policy, None, text_parts, counters,
        )
        assert counters["attempted"] == 0

    def test_budget_exceeded_defers(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=2, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        text_parts = []
        counters = {"attempted": 2, "extracted": 0, "deferred": 0, "chars": 0}
        AttachmentParser._process_textless_pdf_page(
            MagicMock(), 3, 5, policy, None, text_parts, counters,
        )
        assert counters["deferred"] == 1
        assert counters["attempted"] == 2

    def test_ocr_succeeds(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        text_parts = []
        counters = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}

        with patch.object(
            AttachmentParser, "_ocr_pdf_page", return_value="OCR page text",
        ):
            AttachmentParser._process_textless_pdf_page(
                MagicMock(), 1, 5, policy, None, text_parts, counters,
            )

        assert counters["attempted"] == 1
        assert counters["extracted"] == 1
        assert len(text_parts) == 1
        assert "OCR page text" in text_parts[0]

    def test_ocr_exception_caught(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy

        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        text_parts = []
        counters = {"attempted": 0, "extracted": 0, "deferred": 0, "chars": 0}

        with patch.object(
            AttachmentParser, "_ocr_pdf_page", side_effect=Exception("OCR crash"),
        ):
            AttachmentParser._process_textless_pdf_page(
                MagicMock(), 1, 5, policy, None, text_parts, counters,
            )

        assert counters["attempted"] == 1
        assert counters["extracted"] == 0


# ===================================================================
# attachment_parser.py — module-level helpers
# ===================================================================


class TestModuleLevelHelpers:
    def test_resolve_ocr_mode_from_overrides(self, _clean_env):
        from src.connectors.confluence.attachment_parser import _resolve_ocr_mode

        result = _resolve_ocr_mode({"attachment_ocr_mode": "auto"}, {})
        assert result == "auto"

    def test_resolve_ocr_mode_invalid_falls_back(self, _clean_env):
        from src.connectors.confluence.attachment_parser import _resolve_ocr_mode

        result = _resolve_ocr_mode({"attachment_ocr_mode": "banana"}, {})
        assert result == "force"

    def test_resolve_int_field_from_overrides(self):
        from src.connectors.confluence.attachment_parser import _resolve_int_field

        result = _resolve_int_field(
            {"ocr_max_pdf_pages": 42}, {}, "ocr_max_pdf_pages",
            "KNOWLEDGE_CRAWL_OCR_MAX_PDF_PAGES", 100,
        )
        assert result == 42

    def test_resolve_int_field_from_source_defaults(self):
        from src.connectors.confluence.attachment_parser import _resolve_int_field

        result = _resolve_int_field(
            {}, {"ocr_max_pdf_pages": 7}, "ocr_max_pdf_pages",
            "KNOWLEDGE_CRAWL_OCR_MAX_PDF_PAGES", 100,
        )
        assert result == 7

    def test_resolve_bool_field_from_overrides(self):
        from src.connectors.confluence.attachment_parser import _resolve_bool_field

        result = _resolve_bool_field(
            {"slide_render_enabled": False}, {},
            "slide_render_enabled", "KNOWLEDGE_CRAWL_SLIDE_RENDER_ENABLED", True,
        )
        assert result is False

    def test_get_ocr_feature_flags_import_error(self, monkeypatch):
        """Falls back to env when FeatureFlags not importable."""
        from src.connectors.confluence.attachment_parser import _get_ocr_feature_flags

        monkeypatch.setenv("KNOWLEDGE_OCR_PREPROCESS_ENABLED", "false")
        monkeypatch.setenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "true")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "feature_flags" in name:
                raise ImportError("no flags")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            pre, post = _get_ocr_feature_flags()
        assert pre is False
        assert post is True

    def test_get_ocr_postprocess_flag_import_error(self, monkeypatch):
        from src.connectors.confluence.attachment_parser import _get_ocr_postprocess_flag

        monkeypatch.setenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "false")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "feature_flags" in name:
                raise ImportError("no flags")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = _get_ocr_postprocess_flag()
        assert result is False


# ===================================================================
# attachment_parser.py — _fallback_standard_ocr
# ===================================================================


class TestFallbackStandardOcr:
    def test_successful_ocr(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))

        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            return_value=("Standard text", 0.8, []),
        ):
            result = AttachmentParser._fallback_standard_ocr(img, 1)

        assert result == "Standard text"

    def test_low_confidence_returns_none(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))

        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            return_value=("bad text", 0.2, []),
        ):
            result = AttachmentParser._fallback_standard_ocr(img, 1)

        assert result is None

    def test_none_text_returns_none(self, _clean_env):
        from src.connectors.confluence.attachment_parser import AttachmentParser

        from PIL import Image as _PilImage
        img = _PilImage.new("RGB", (100, 100), (255, 255, 255))

        with patch.object(
            AttachmentParser, "_ocr_extract_safe",
            return_value=(None, 0.0, []),
        ):
            result = AttachmentParser._fallback_standard_ocr(img, 1)

        assert result is None


# ===================================================================
# Additional coverage: _process_one_shape_ocr, _try_cli_doc_extract
# ===================================================================

class TestProcessOneShapeOcr:
    def test_deferred_when_budget_exceeded(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=1,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        attempted = {1}  # already at limit
        result = AttachmentParser._process_one_shape_ocr(
            2, b"img", policy, 5, None, False, False, attempted, set(),
        )
        assert result == {"deferred": 1}

    def test_none_when_no_ocr(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        with patch.object(AttachmentParser, '_get_ocr_instance', return_value=None):
            result = AttachmentParser._process_one_shape_ocr(
                1, b"img", policy, 5, None, False, False, set(), set(),
            )
        assert result is None

    def test_success_with_text(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        with (
            patch.object(AttachmentParser, '_get_ocr_instance', return_value=MagicMock()),
            patch.object(AttachmentParser, '_ocr_single_shape_image', return_value=("OCR text here", 0.9, False)),
        ):
            extracted = set()
            result = AttachmentParser._process_one_shape_ocr(
                1, b"img", policy, 5, None, False, False, set(), extracted,
            )
        assert result["text"] is not None
        assert result["extracted"] == 1
        assert 1 in extracted

    def test_timed_out(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        with (
            patch.object(AttachmentParser, '_get_ocr_instance', return_value=MagicMock()),
            patch.object(AttachmentParser, '_ocr_single_shape_image', return_value=(None, 0.0, True)),
        ):
            result = AttachmentParser._process_one_shape_ocr(
                1, b"img", policy, 5, None, False, False, set(), set(),
            )
        assert result["timed_out_item"] == (1, b"img")

    def test_exception(self):
        from src.connectors.confluence.attachment_parser import AttachmentParser
        from src.connectors.confluence.models import AttachmentOCRPolicy
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto", ocr_min_text_chars=100,
            ocr_max_pdf_pages=10, ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False, layout_analysis_enabled=False,
        )
        with (
            patch.object(AttachmentParser, '_get_ocr_instance', return_value=MagicMock()),
            patch.object(AttachmentParser, '_ocr_single_shape_image', side_effect=RuntimeError("fail")),
        ):
            result = AttachmentParser._process_one_shape_ocr(
                1, b"img", policy, 5, None, False, False, set(), set(),
            )
        assert result["text"] is None


class TestTryCliDocExtract:
    def test_no_tool_path(self):
        from src.connectors.confluence.attachment_parser import _try_cli_doc_extract
        result = _try_cli_doc_extract(None, Path("/fake"), confidence=0.5)
        assert result is None

    def test_success(self):
        from src.connectors.confluence.attachment_parser import _try_cli_doc_extract
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Extracted text")
            result = _try_cli_doc_extract("/usr/bin/antiword", Path("/fake.doc"), confidence=0.7)
        assert result is not None
        assert result.extracted_text == "Extracted text"

    def test_failure(self):
        from src.connectors.confluence.attachment_parser import _try_cli_doc_extract
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _try_cli_doc_extract("/usr/bin/antiword", Path("/fake.doc"), confidence=0.7)
        assert result is None

    def test_exception(self):
        from src.connectors.confluence.attachment_parser import _try_cli_doc_extract
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("timeout")
            result = _try_cli_doc_extract("/usr/bin/antiword", Path("/fake.doc"), confidence=0.7)
        assert result is None
