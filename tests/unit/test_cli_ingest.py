"""Unit tests for cli/ingest.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli.ingest import OnnxSparseEmbedder, _should_skip_file


# ---------------------------------------------------------------------------
# OnnxSparseEmbedder
# ---------------------------------------------------------------------------


class TestOnnxSparseEmbedder:
    @pytest.mark.asyncio
    async def test_embed_sparse_returns_lexical_weights(self) -> None:
        mock_provider = MagicMock()
        mock_provider.encode.return_value = {
            "lexical_weights": [{"token1": 0.5, "token2": 0.3}],
        }
        embedder = OnnxSparseEmbedder(mock_provider)
        result = await embedder.embed_sparse(["test text"])

        assert result == [{"token1": 0.5, "token2": 0.3}]
        mock_provider.encode.assert_called_once_with(["test text"], False, True, False)

    @pytest.mark.asyncio
    async def test_embed_sparse_missing_key_returns_empty(self) -> None:
        mock_provider = MagicMock()
        mock_provider.encode.return_value = {}
        embedder = OnnxSparseEmbedder(mock_provider)
        result = await embedder.embed_sparse(["test"])

        assert result == [{}]

    @pytest.mark.asyncio
    async def test_embed_sparse_multiple_texts(self) -> None:
        mock_provider = MagicMock()
        mock_provider.encode.return_value = {
            "lexical_weights": [{"a": 0.1}, {"b": 0.2}],
        }
        embedder = OnnxSparseEmbedder(mock_provider)
        result = await embedder.embed_sparse(["text1", "text2"])

        assert len(result) == 2


# ---------------------------------------------------------------------------
# _should_skip_file
# ---------------------------------------------------------------------------


class TestShouldSkipFile:
    @pytest.mark.asyncio
    async def test_force_never_skips(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = await _should_skip_file(str(f), force=True, ingested_hashes={"anyhash"})
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_hashes_never_skips(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = await _should_skip_file(str(f), force=False, ingested_hashes=set())
        assert result is False

    @pytest.mark.asyncio
    async def test_matching_hash_skips(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        content = f.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()[:32]

        result = await _should_skip_file(str(f), force=False, ingested_hashes={file_hash})
        assert result is True

    @pytest.mark.asyncio
    async def test_non_matching_hash_does_not_skip(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")

        result = await _should_skip_file(str(f), force=False, ingested_hashes={"nonmatch"})
        assert result is False


# ---------------------------------------------------------------------------
# _get_ingested_hashes
# ---------------------------------------------------------------------------


class TestGetIngestedHashes:
    @pytest.mark.asyncio
    async def test_returns_hashes_from_qdrant(self) -> None:
        from src.cli.ingest import _get_ingested_hashes

        mock_responses = [
            # Collection check
            MagicMock(status_code=200),
            # Scroll response with points
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "result": {
                        "points": [
                            {"payload": {"content_hash": "hash1"}},
                            {"payload": {"content_hash": "hash2"}},
                            {"payload": {}},
                        ],
                        "next_page_offset": None,
                    }
                }),
            ),
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_responses[0])
        mock_client.post = AsyncMock(return_value=mock_responses[1])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            hashes = await _get_ingested_hashes("test-kb", None)

        assert "hash1" in hashes
        assert "hash2" in hashes
        assert len(hashes) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing_collection(self) -> None:
        from src.cli.ingest import _get_ingested_hashes

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(status_code=404))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            hashes = await _get_ingested_hashes("nonexist-kb", None)

        assert hashes == set()

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self) -> None:
        from src.cli.ingest import _get_ingested_hashes

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            hashes = await _get_ingested_hashes("test-kb", None)

        assert hashes == set()
