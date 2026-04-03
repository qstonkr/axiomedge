"""Unit tests for src/connectors/crawl_result.py -- CrawlResultConnector."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.connectors.crawl_result import CrawlResultConnector
from src.domain.models import RawDocument


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def connector() -> CrawlResultConnector:
    return CrawlResultConnector()


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# Constructor / properties
# ---------------------------------------------------------------------------

class TestBasics:
    def test_source_type(self, connector: CrawlResultConnector):
        assert connector.source_type == "crawl_result"

    async def test_health_check(self, connector: CrawlResultConnector):
        assert await connector.health_check() is True

    def test_default_output_dir(self):
        c = CrawlResultConnector()
        assert c._default_output_dir == Path("/data/crawl")

    def test_custom_output_dir(self):
        c = CrawlResultConnector("/my/crawl")
        assert c._default_output_dir == Path("/my/crawl")


# ---------------------------------------------------------------------------
# _resolve_input_path
# ---------------------------------------------------------------------------

class TestResolveInputPath:
    def test_from_entry_point(self, connector: CrawlResultConnector):
        result = connector._resolve_input_path({"entry_point": "/data/custom"})
        assert result == Path("/data/custom")

    def test_from_output_dir(self, connector: CrawlResultConnector):
        result = connector._resolve_input_path({"output_dir": "/data/out"})
        assert result == Path("/data/out")

    def test_default_fallback(self, connector: CrawlResultConnector):
        result = connector._resolve_input_path({})
        assert result == Path("/data/crawl")


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_iso_format(self):
        dt = CrawlResultConnector._parse_datetime("2024-01-15T10:30:00+00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_z_suffix(self):
        dt = CrawlResultConnector._parse_datetime("2024-01-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_no_tz(self):
        dt = CrawlResultConnector._parse_datetime("2024-01-15T10:30:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_offset_without_colon(self):
        dt = CrawlResultConnector._parse_datetime("2024-01-15T10:30:00+0900")
        assert dt is not None

    def test_empty_string(self):
        assert CrawlResultConnector._parse_datetime("") is None
        assert CrawlResultConnector._parse_datetime(None) is None

    def test_invalid_date(self):
        assert CrawlResultConnector._parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _label_names
# ---------------------------------------------------------------------------

class TestLabelNames:
    def test_valid_labels(self):
        labels = [{"name": "infra"}, {"name": "devops"}, {"name": ""}]
        result = CrawlResultConnector._label_names(labels)
        assert result == ["devops", "infra"]

    def test_not_a_list(self):
        assert CrawlResultConnector._label_names("string") == []
        assert CrawlResultConnector._label_names(None) == []

    def test_empty_list(self):
        assert CrawlResultConnector._label_names([]) == []


# ---------------------------------------------------------------------------
# _build_content
# ---------------------------------------------------------------------------

class TestBuildContent:
    def test_simple_content(self, connector: CrawlResultConnector):
        page = {"content_text": "Hello world"}
        assert connector._build_content(page) == "Hello world"

    def test_with_attachments(self, connector: CrawlResultConnector):
        page = {
            "content_text": "Main content",
            "title": "Page Title",
            "attachments": [
                {"filename": "doc.pdf", "extracted_text": "PDF content"},
            ],
        }
        content = connector._build_content(page)
        assert "Main content" in content
        assert "[Attachment: doc.pdf]" in content
        assert "[parent: Page Title]" in content
        assert "PDF content" in content

    def test_with_comments(self, connector: CrawlResultConnector):
        page = {
            "content_text": "Main",
            "comments": [
                {"content": "Nice post!", "author": "user@example.com"},
            ],
        }
        content = connector._build_content(page)
        assert "[Comment: user@example.com]" in content
        assert "Nice post!" in content

    def test_empty_content(self, connector: CrawlResultConnector):
        assert connector._build_content({}) == ""
        assert connector._build_content({"content_text": "  "}) == ""

    def test_attachment_no_text(self, connector: CrawlResultConnector):
        page = {
            "content_text": "Main",
            "attachments": [{"filename": "empty.pdf", "extracted_text": ""}],
        }
        content = connector._build_content(page)
        assert "empty.pdf" not in content


# ---------------------------------------------------------------------------
# _upsert_page / _page_sort_key
# ---------------------------------------------------------------------------

class TestUpsertPage:
    def test_upsert_new_page(self, connector: CrawlResultConnector):
        pages: dict[str, dict] = {}
        connector._upsert_page(pages, {"page_id": "p1", "version": 1})
        assert "p1" in pages

    def test_upsert_newer_version(self, connector: CrawlResultConnector):
        pages: dict[str, dict] = {"p1": {"page_id": "p1", "version": 1}}
        connector._upsert_page(pages, {"page_id": "p1", "version": 2})
        assert pages["p1"]["version"] == 2

    def test_upsert_older_version_ignored(self, connector: CrawlResultConnector):
        pages: dict[str, dict] = {"p1": {"page_id": "p1", "version": 3}}
        connector._upsert_page(pages, {"page_id": "p1", "version": 1})
        assert pages["p1"]["version"] == 3

    def test_upsert_no_page_id(self, connector: CrawlResultConnector):
        pages: dict[str, dict] = {}
        connector._upsert_page(pages, {"content": "no id"})
        assert len(pages) == 0

    def test_page_sort_key_defaults(self):
        assert CrawlResultConnector._page_sort_key({}) == (0, "")
        assert CrawlResultConnector._page_sort_key({"version": "bad"}) == (0, "")


# ---------------------------------------------------------------------------
# _select_input_files
# ---------------------------------------------------------------------------

class TestSelectInputFiles:
    def test_single_file(self, connector: CrawlResultConnector, tmp_dir: Path):
        f = tmp_dir / "crawl_data.json"
        f.write_text("{}")
        result = connector._select_input_files(f, source_selector="all")
        assert result == [f]

    def test_dir_combined(self, connector: CrawlResultConnector, tmp_dir: Path):
        combined = tmp_dir / "crawl_combined.json"
        combined.write_text("{}")
        result = connector._select_input_files(tmp_dir, source_selector="all")
        assert result == [combined]

    def test_dir_no_combined(self, connector: CrawlResultConnector, tmp_dir: Path):
        f1 = tmp_dir / "crawl_infra.json"
        f1.write_text("{}")
        result = connector._select_input_files(tmp_dir, source_selector="all")
        assert f1 in result

    def test_specific_source(self, connector: CrawlResultConnector, tmp_dir: Path):
        f = tmp_dir / "crawl_infra.json"
        f.write_text("{}")
        result = connector._select_input_files(tmp_dir, source_selector="infra")
        assert result == [f]

    def test_specific_source_jsonl(self, connector: CrawlResultConnector, tmp_dir: Path):
        f = tmp_dir / "crawl_dev.jsonl"
        f.write_text("")
        result = connector._select_input_files(tmp_dir, source_selector="dev")
        assert result == [f]

    def test_nonexistent_dir(self, connector: CrawlResultConnector, tmp_dir: Path):
        result = connector._select_input_files(tmp_dir / "no_such", source_selector="all")
        assert result == []

    def test_fuzzy_match(self, connector: CrawlResultConnector, tmp_dir: Path):
        f = tmp_dir / "crawl_infrastructure.json"
        f.write_text("{}")
        result = connector._select_input_files(tmp_dir, source_selector="infra")
        assert f in result


# ---------------------------------------------------------------------------
# _fingerprint_pages
# ---------------------------------------------------------------------------

class TestFingerprintPages:
    def test_deterministic(self, connector: CrawlResultConnector):
        pages = [{"page_id": "p1", "content_text": "hello", "version": 1}]
        fp1 = connector._fingerprint_pages(pages)
        fp2 = connector._fingerprint_pages(pages)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_different_content_different_fp(self, connector: CrawlResultConnector):
        fp1 = connector._fingerprint_pages([{"page_id": "p1", "content_text": "a", "version": 1}])
        fp2 = connector._fingerprint_pages([{"page_id": "p1", "content_text": "b", "version": 1}])
        assert fp1 != fp2

    def test_empty_pages(self, connector: CrawlResultConnector):
        fp = connector._fingerprint_pages([])
        assert isinstance(fp, str) and len(fp) == 64


# ---------------------------------------------------------------------------
# fetch (integration-style with tmp files)
# ---------------------------------------------------------------------------

class TestFetch:
    async def test_fetch_json(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {
            "pages": [
                {
                    "page_id": "pg1",
                    "title": "Test Page",
                    "content_text": "Hello world",
                    "url": "http://wiki.example.com/pg1",
                    "version": 1,
                }
            ]
        }
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        result = await connector.fetch(
            {"entry_point": str(tmp_dir), "source": "all"}
        )
        assert result.success is True
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert doc.doc_id == "pg1"
        assert doc.title == "Test Page"
        assert doc.content == "Hello world"

    async def test_fetch_jsonl(self, connector: CrawlResultConnector, tmp_dir: Path):
        lines = [
            json.dumps({
                "page_id": "pg1",
                "title": "Line Page",
                "content_text": "Line content",
                "url": "http://example.com/pg1",
                "version": 1,
            }),
        ]
        f = tmp_dir / "crawl_test.jsonl"
        f.write_text("\n".join(lines))

        result = await connector.fetch(
            {"entry_point": str(tmp_dir), "source": "test"}
        )
        assert result.success is True
        assert len(result.documents) == 1

    async def test_fetch_no_files(self, connector: CrawlResultConnector, tmp_dir: Path):
        result = await connector.fetch(
            {"entry_point": str(tmp_dir / "empty"), "source": "all"}
        )
        assert result.success is False
        assert "No crawl JSON files" in (result.error or "")

    async def test_fetch_skips_unchanged(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {"pages": [{"page_id": "pg1", "content_text": "stable", "url": "", "version": 1}]}
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        result1 = await connector.fetch({"entry_point": str(tmp_dir), "source": "all"})
        fp = result1.version_fingerprint

        result2 = await connector.fetch(
            {"entry_point": str(tmp_dir), "source": "all"},
            last_fingerprint=fp,
        )
        assert result2.success is True
        assert result2.documents == []
        assert result2.metadata.get("skipped") is True

    async def test_fetch_with_source_info(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {
            "source_info": {"name": "wiki"},
            "pages": [{"page_id": "pg1", "content_text": "data", "url": "", "version": 1}],
        }
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        result = await connector.fetch({"entry_point": str(tmp_dir), "source": "all"})
        assert result.success is True

    async def test_fetch_empty_pages_skipped(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {"pages": [{"page_id": "pg1", "content_text": "", "url": "", "version": 1}]}
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        result = await connector.fetch({"entry_point": str(tmp_dir), "source": "all"})
        assert result.success is True
        assert len(result.documents) == 0
        assert result.metadata.get("pages_empty") == 1

    async def test_fetch_jsonl_with_pages_array(self, connector: CrawlResultConnector, tmp_dir: Path):
        line = json.dumps({
            "pages": [
                {"page_id": "p1", "content_text": "c1", "url": "", "version": 1},
                {"page_id": "p2", "content_text": "c2", "url": "", "version": 1},
            ]
        })
        f = tmp_dir / "crawl_multi.jsonl"
        f.write_text(line)

        result = await connector.fetch({"entry_point": str(tmp_dir), "source": "multi"})
        assert result.success is True
        assert len(result.documents) == 2

    async def test_fetch_invalid_json(self, connector: CrawlResultConnector, tmp_dir: Path):
        f = tmp_dir / "crawl_bad.json"
        f.write_text("not valid json{{{")

        result = await connector.fetch({"entry_point": str(tmp_dir), "source": "bad"})
        assert result.success is False

    async def test_fetch_with_author_fields(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {
            "pages": [{
                "page_id": "pg1",
                "content_text": "content",
                "url": "",
                "version": 1,
                "creator_email": "user@gs.com",
                "labels": [{"name": "infra"}, {"name": "ops"}],
            }]
        }
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        result = await connector.fetch({"entry_point": str(tmp_dir), "source": "all"})
        doc = result.documents[0]
        assert doc.author == "user@gs.com"
        assert doc.metadata["labels"] == ["infra", "ops"]


# ---------------------------------------------------------------------------
# lazy_fetch
# ---------------------------------------------------------------------------

class TestLazyFetch:
    async def test_lazy_fetch_yields_docs(self, connector: CrawlResultConnector, tmp_dir: Path):
        data = {"pages": [{"page_id": "lf1", "content_text": "lazy", "url": "", "version": 1}]}
        f = tmp_dir / "crawl_combined.json"
        f.write_text(json.dumps(data))

        docs = []
        async for doc in connector.lazy_fetch({"entry_point": str(tmp_dir), "source": "all"}):
            docs.append(doc)
        assert len(docs) == 1

    async def test_lazy_fetch_no_files(self, connector: CrawlResultConnector, tmp_dir: Path):
        docs = []
        async for doc in connector.lazy_fetch({"entry_point": str(tmp_dir / "empty"), "source": "all"}):
            docs.append(doc)
        assert len(docs) == 0


# ---------------------------------------------------------------------------
# _read_jsonl_lines
# ---------------------------------------------------------------------------

class TestReadJsonlLines:
    def test_valid_lines(self, tmp_dir: Path):
        f = tmp_dir / "test.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n\n{"c":3}')
        rows = CrawlResultConnector._read_jsonl_lines(f)
        assert len(rows) == 3

    def test_invalid_lines_skipped(self, tmp_dir: Path):
        f = tmp_dir / "test.jsonl"
        f.write_text('{"valid":1}\nnot json\n[1,2,3]')
        rows = CrawlResultConnector._read_jsonl_lines(f)
        assert len(rows) == 1  # only dict rows
