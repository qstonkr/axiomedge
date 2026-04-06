"""Tests for src/connectors/confluence/client.py and attachment_parser.py.

Covers the main code paths with mocked external dependencies (httpx, fitz, openpyxl, docx).
"""

from __future__ import annotations

import asyncio
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
