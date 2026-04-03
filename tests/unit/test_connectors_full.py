"""Comprehensive tests for src/connectors/file_upload.py."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.file_upload import FileUploadConnector, _parse_datetime


# ===========================================================================
# _parse_datetime helper
# ===========================================================================

class TestParseDatetime:
    def test_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self):
        dt = datetime(2024, 1, 1)
        assert _parse_datetime(dt) is dt

    def test_empty_string(self):
        assert _parse_datetime("") is None

    def test_iso_format(self):
        result = _parse_datetime("2024-01-15T10:30:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_z_suffix(self):
        result = _parse_datetime("2024-01-15T10:30:00Z")
        assert result is not None

    def test_invalid(self):
        assert _parse_datetime("not-a-date") is None


# ===========================================================================
# FileUploadConnector
# ===========================================================================

class TestFileUploadConnector:
    def test_source_type(self):
        connector = FileUploadConnector()
        assert connector.source_type == "file_upload"

    async def test_health_check(self):
        connector = FileUploadConnector()
        assert await connector.health_check() is True

    async def test_fetch_no_entry_point(self):
        connector = FileUploadConnector()
        result = await connector.fetch({})
        assert result.success is False
        assert "entry_point" in result.error

    async def test_fetch_nonexistent_file(self):
        connector = FileUploadConnector()
        result = await connector.fetch({"entry_point": "/nonexistent/file.pdf"})
        assert result.success is False
        assert "Failed to read" in result.error

    async def test_fetch_skips_unchanged(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("hello world test content for parsing")
            path = f.name

        import hashlib
        with open(path, "rb") as f:
            fingerprint = hashlib.sha256(f.read()).hexdigest()

        connector = FileUploadConnector()
        result = await connector.fetch(
            {"entry_point": path},
            last_fingerprint=fingerprint,
        )
        assert result.success is True
        assert result.metadata.get("skipped") is True

    async def test_fetch_success(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("This is test content for the file upload connector.")
            path = f.name

        connector = FileUploadConnector()

        with patch("src.connectors.file_upload.parse_file") as mock_parse:
            mock_parse.return_value = "Parsed content from file"
            result = await connector.fetch({"entry_point": path}, force=True)

        assert result.success is True
        assert len(result.documents) == 1
        assert result.documents[0].content == "Parsed content from file"

    async def test_fetch_empty_parse(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("hello")
            path = f.name

        connector = FileUploadConnector()

        with patch("src.connectors.file_upload.parse_file") as mock_parse:
            mock_parse.return_value = "   "  # Whitespace only
            result = await connector.fetch({"entry_point": path}, force=True)

        assert result.success is False
        assert "empty text" in result.error

    async def test_fetch_with_config_options(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, mode="wb") as f:
            f.write(b"fake pdf")
            path = f.name

        connector = FileUploadConnector()

        with patch("src.connectors.file_upload.parse_file") as mock_parse:
            mock_parse.return_value = "Content from PDF"
            result = await connector.fetch({
                "entry_point": path,
                "title": "My Doc",
                "document_id": "custom-id",
                "author_id": "user1",
                "updated_at": "2024-06-15T10:00:00Z",
                "custom_field": "value",
            }, force=True)

        assert result.success is True
        doc = result.documents[0]
        assert doc.doc_id == "custom-id"
        assert doc.title == "My Doc"
        assert doc.author == "user1"

    async def test_lazy_fetch_success(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("lazy content")
            path = f.name

        connector = FileUploadConnector()

        with patch("src.connectors.file_upload.parse_file") as mock_parse:
            mock_parse.return_value = "Lazy parsed"
            docs = []
            async for doc in connector.lazy_fetch({"entry_point": path}, force=True):
                docs.append(doc)

        assert len(docs) == 1

    async def test_lazy_fetch_failure(self):
        connector = FileUploadConnector()
        docs = []
        async for doc in connector.lazy_fetch({}):
            docs.append(doc)
        assert len(docs) == 0

    async def test_fetch_uses_file_uri(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("content")
            path = f.name

        connector = FileUploadConnector()
        with patch("src.connectors.file_upload.parse_file", return_value="parsed"):
            result = await connector.fetch({"file_uri": path}, force=True)
        assert result.success is True

    async def test_fetch_uses_file_path(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("content")
            path = f.name

        connector = FileUploadConnector()
        with patch("src.connectors.file_upload.parse_file", return_value="parsed"):
            result = await connector.fetch({"file_path": path}, force=True)
        assert result.success is True
