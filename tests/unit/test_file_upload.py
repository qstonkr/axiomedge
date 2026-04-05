"""Unit tests for src/connectors/file_upload.py

Tests FileUploadConnector: fetch with valid/missing files, fingerprint-based
skip, empty parse result, lazy_fetch, and _parse_datetime helper.
File I/O and document parsing are mocked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.file_upload import FileUploadConnector, _parse_datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def connector() -> FileUploadConnector:
    return FileUploadConnector()


# ---------------------------------------------------------------------------
# source_type / health_check
# ---------------------------------------------------------------------------

class TestBasics:
    def test_source_type(self, connector: FileUploadConnector) -> None:
        assert connector.source_type == "file_upload"

    @pytest.mark.asyncio
    async def test_health_check(self, connector: FileUploadConnector) -> None:
        assert await connector.health_check() is True


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

class TestFetch:
    @pytest.mark.asyncio
    async def test_missing_entry_point(self, connector: FileUploadConnector) -> None:
        result = await connector.fetch({})
        assert result.success is False
        assert "requires" in (result.error or "")

    @pytest.mark.asyncio
    async def test_file_not_found(self, connector: FileUploadConnector) -> None:
        result = await connector.fetch({"entry_point": "/nonexistent/file.pdf"})
        assert result.success is False
        assert "Failed to read" in (result.error or "") or "not found" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_successful_fetch(self, connector: FileUploadConnector, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello content", encoding="utf-8")

        with patch(
            "src.connectors.file_upload.parse_file",
            return_value="Parsed text content",
        ):
            result = await connector.fetch(
                {"entry_point": str(test_file), "title": "Test Doc"},
                force=True,
            )

        assert result.success is True
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert doc.title == "Test Doc"
        assert doc.content == "Parsed text content"
        assert doc.content_hash != ""

    @pytest.mark.asyncio
    async def test_fingerprint_skip(self, connector: FileUploadConnector, tmp_path: Path) -> None:
        test_file = tmp_path / "skip.txt"
        content = b"same content"
        test_file.write_bytes(content)

        import hashlib
        fingerprint = hashlib.sha256(content).hexdigest()

        result = await connector.fetch(
            {"entry_point": str(test_file)},
            force=False,
            last_fingerprint=fingerprint,
        )
        assert result.success is True
        assert result.skipped is True
        assert len(result.documents) == 0

    @pytest.mark.asyncio
    async def test_empty_parse_result(self, connector: FileUploadConnector, tmp_path: Path) -> None:
        test_file = tmp_path / "empty.txt"
        test_file.write_text("raw", encoding="utf-8")

        with patch("src.connectors.file_upload.parse_file", return_value="   "):
            result = await connector.fetch({"entry_point": str(test_file)}, force=True)

        assert result.success is False
        assert "empty" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_config_key_variants(self, connector: FileUploadConnector, tmp_path: Path) -> None:
        """file_uri and file_path are accepted as alternatives to entry_point."""
        test_file = tmp_path / "alt.txt"
        test_file.write_text("content", encoding="utf-8")

        with patch("src.connectors.file_upload.parse_file", return_value="parsed"):
            result = await connector.fetch({"file_path": str(test_file)}, force=True)
        assert result.success is True


# ---------------------------------------------------------------------------
# lazy_fetch
# ---------------------------------------------------------------------------

class TestLazyFetch:
    @pytest.mark.asyncio
    async def test_lazy_fetch_yields_documents(
        self, connector: FileUploadConnector, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "lazy.txt"
        test_file.write_text("content", encoding="utf-8")

        with patch("src.connectors.file_upload.parse_file", return_value="parsed"):
            docs = []
            async for doc in connector.lazy_fetch(
                {"entry_point": str(test_file)}, force=True
            ):
                docs.append(doc)
        assert len(docs) == 1

    @pytest.mark.asyncio
    async def test_lazy_fetch_empty_on_failure(self, connector: FileUploadConnector) -> None:
        docs = []
        async for doc in connector.lazy_fetch({}):
            docs.append(doc)
        assert len(docs) == 0


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self) -> None:
        now = datetime.now(timezone.utc)
        assert _parse_datetime(now) is now

    def test_iso_string(self) -> None:
        result = _parse_datetime("2026-01-15T10:30:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026

    def test_z_suffix(self) -> None:
        result = _parse_datetime("2026-01-15T10:30:00Z")
        assert isinstance(result, datetime)

    def test_empty_string(self) -> None:
        assert _parse_datetime("") is None

    def test_invalid_string(self) -> None:
        assert _parse_datetime("not-a-date") is None
