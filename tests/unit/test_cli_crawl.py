"""Unit tests for cli/crawl.py."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cli.crawl import (
    SUPPORTED_EXTENSIONS,
    _build_doc,
    _load_crawl_state,
    _log_deleted_files,
    _read_text_content,
    _save_crawl_state,
    crawl_directory,
)


# ---------------------------------------------------------------------------
# _read_text_content
# ---------------------------------------------------------------------------


class TestReadTextContent:
    def test_txt_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("안녕하세요", encoding="utf-8")
        assert _read_text_content(f) == "안녕하세요"

    def test_txt_euckr_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_bytes("안녕하세요".encode("euc-kr"))
        assert _read_text_content(f) == "안녕하세요"

    def test_binary_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_bytes(b"\xff\xfe\xfd\xfc\xfb\xfa")
        result = _read_text_content(f)
        assert "Binary file" in result

    def test_non_text_extension_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "image.pdf"
        f.write_text("some content")
        assert _read_text_content(f) == ""

    def test_md_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("# Title", encoding="utf-8")
        assert _read_text_content(f) == "# Title"

    def test_yaml_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("key: value", encoding="utf-8")
        assert _read_text_content(f) == "key: value"

    def test_yml_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yml"
        f.write_text("key: value", encoding="utf-8")
        assert _read_text_content(f) == "key: value"

    def test_json_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text('{"a":1}', encoding="utf-8")
        assert _read_text_content(f) == '{"a":1}'


# ---------------------------------------------------------------------------
# _build_doc
# ---------------------------------------------------------------------------


class TestBuildDoc:
    def test_basic_structure(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "test.txt"
        f.parent.mkdir()
        f.write_text("content", encoding="utf-8")
        content_hash = hashlib.sha256(f.read_bytes()).hexdigest()

        doc = _build_doc(f, tmp_path, content_hash)

        assert doc["doc_id"] == content_hash[:16]
        assert doc["title"] == "test.txt"
        assert doc["content"] == "content"
        assert doc["content_hash"] == content_hash
        assert doc["source_uri"] == str(f.absolute())
        assert doc["metadata"]["file_name"] == "test.txt"
        assert doc["metadata"]["file_extension"] == ".txt"
        assert doc["metadata"]["relative_path"] == os.path.join("sub", "test.txt")
        assert doc["metadata"]["crawl_source"] == "local_filesystem"
        assert doc["metadata"]["file_size"] > 0
        assert doc["updated_at"]  # ISO format timestamp


# ---------------------------------------------------------------------------
# _log_deleted_files
# ---------------------------------------------------------------------------


class TestLogDeletedFiles:
    def test_full_mode_skips(self) -> None:
        # Should not raise or log anything
        _log_deleted_files(full=True, prev_state={"a": "h1"}, new_state={})

    def test_empty_prev_state_skips(self) -> None:
        _log_deleted_files(full=False, prev_state={}, new_state={})

    def test_detects_deleted_files(self) -> None:
        prev = {"file1.txt": "h1", "file2.txt": "h2", "file3.txt": "h3"}
        new = {"file1.txt": "h1"}
        # Should not raise — just logs
        _log_deleted_files(full=False, prev_state=prev, new_state=new)

    def test_no_deletions(self) -> None:
        state = {"file1.txt": "h1"}
        _log_deleted_files(full=False, prev_state=state, new_state=state)


# ---------------------------------------------------------------------------
# _load_crawl_state / _save_crawl_state
# ---------------------------------------------------------------------------


class TestCrawlState:
    def test_load_nonexistent(self, tmp_path: Path) -> None:
        assert _load_crawl_state(tmp_path) == {}

    def test_save_and_load(self, tmp_path: Path) -> None:
        state = {"file1.txt": "abc123", "file2.txt": "def456"}
        _save_crawl_state(tmp_path, state)
        loaded = _load_crawl_state(tmp_path)
        assert loaded == state

    def test_load_corrupt_json(self, tmp_path: Path) -> None:
        state_file = tmp_path / ".crawl_state.json"
        state_file.write_text("not valid json{{{")
        assert _load_crawl_state(tmp_path) == {}


# ---------------------------------------------------------------------------
# crawl_directory (integration-style with tmp_path)
# ---------------------------------------------------------------------------


class TestCrawlDirectory:
    def test_crawl_with_supported_files(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "doc.txt").write_text("hello", encoding="utf-8")
        (source / "doc.md").write_text("# Title", encoding="utf-8")
        (source / "skip.exe").write_text("binary")  # unsupported

        output = tmp_path / "output"
        crawl_directory(str(source), str(output))

        output_file = output / "crawl_results.jsonl"
        assert output_file.exists()

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2  # .txt and .md, not .exe

        doc = json.loads(lines[0])
        assert "doc_id" in doc
        assert "content_hash" in doc

    def test_crawl_nonexistent_source(self, tmp_path: Path) -> None:
        output = tmp_path / "output"
        crawl_directory("/nonexistent/dir", str(output))
        # Should not crash, just log error

    def test_incremental_skips_unchanged(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "doc.txt").write_text("hello", encoding="utf-8")
        output = tmp_path / "output"

        # First crawl
        crawl_directory(str(source), str(output))
        lines1 = (output / "crawl_results.jsonl").read_text().strip().split("\n")
        assert len(lines1) == 1

        # Second crawl (incremental, same content)
        crawl_directory(str(source), str(output))
        lines2 = (output / "crawl_results.jsonl").read_text().strip().split("\n")
        # File unchanged, so empty JSONL
        assert lines2 == [""]

    def test_full_mode_re_crawls(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "doc.txt").write_text("hello", encoding="utf-8")
        output = tmp_path / "output"

        crawl_directory(str(source), str(output))
        crawl_directory(str(source), str(output), full=True)

        lines = (output / "crawl_results.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1  # re-crawled even though unchanged

    def test_incremental_detects_changes(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "doc.txt").write_text("version1", encoding="utf-8")
        output = tmp_path / "output"

        crawl_directory(str(source), str(output))

        # Modify file
        (source / "doc.txt").write_text("version2", encoding="utf-8")
        crawl_directory(str(source), str(output))

        lines = (output / "crawl_results.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        doc = json.loads(lines[0])
        assert doc["content"] == "version2"
