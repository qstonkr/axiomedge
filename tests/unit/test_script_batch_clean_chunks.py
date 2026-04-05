"""Unit tests for scripts/batch_clean_chunks.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.batch_clean_chunks import (
    _log_dry_run_sample,
    _process_chunk,
    get_collection_name,
)


# ---------------------------------------------------------------------------
# get_collection_name
# ---------------------------------------------------------------------------


class TestGetCollectionName:
    def test_simple_kb_id(self) -> None:
        assert get_collection_name("drp") == "kb_drp"

    def test_hyphenated_kb_id(self) -> None:
        assert get_collection_name("a-ari") == "kb_a_ari"
        assert get_collection_name("g-espa") == "kb_g_espa"

    def test_underscore_kb_id(self) -> None:
        assert get_collection_name("itops_general") == "kb_itops_general"


# ---------------------------------------------------------------------------
# _log_dry_run_sample
# ---------------------------------------------------------------------------


class TestLogDryRunSample:
    def test_within_limit_logs(self) -> None:
        # Should not raise for count <= 5
        _log_dry_run_sample(1, "test_doc.pdf", "original content", "cleaned content")

    def test_above_limit_skips(self) -> None:
        # Should not raise for count > 5, just returns
        _log_dry_run_sample(6, "test_doc.pdf", "original", "cleaned")


# ---------------------------------------------------------------------------
# _process_chunk
# ---------------------------------------------------------------------------


class TestProcessChunk:
    def test_empty_content_returns_not_cleaned(self) -> None:
        client = MagicMock()
        point = {"id": "p1", "payload": {"content": "", "document_name": "doc"}}
        was_cleaned, had_error = _process_chunk(client, "kb_test", point, dry_run=False, cleaned_count=0)
        assert was_cleaned is False
        assert had_error is False

    def test_unchanged_content_returns_not_cleaned(self) -> None:
        client = MagicMock()
        content = "This is clean text with no issues."
        point = {"id": "p1", "payload": {"content": content, "document_name": "doc"}}

        with patch("scripts.batch_clean_chunks.clean_chunk_text", return_value=content):
            was_cleaned, had_error = _process_chunk(client, "kb_test", point, dry_run=False, cleaned_count=0)

        assert was_cleaned is False
        assert had_error is False

    def test_changed_content_dry_run(self) -> None:
        client = MagicMock()
        point = {"id": "p1", "payload": {"content": "dirty  text", "document_name": "doc.pdf"}}

        with patch("scripts.batch_clean_chunks.clean_chunk_text", return_value="dirty text"):
            was_cleaned, had_error = _process_chunk(client, "kb_test", point, dry_run=True, cleaned_count=0)

        assert was_cleaned is True
        assert had_error is False
        # Client should not be called in dry_run mode
        client.post.assert_not_called()

    def test_changed_content_applies_update(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        client = MagicMock()
        client.post.return_value = mock_resp

        point = {"id": "p1", "payload": {"content": "dirty  text", "document_name": "doc"}}

        with patch("scripts.batch_clean_chunks.clean_chunk_text", return_value="dirty text"):
            with patch("scripts.batch_clean_chunks.update_payload", return_value=True):
                was_cleaned, had_error = _process_chunk(
                    client, "kb_test", point, dry_run=False, cleaned_count=0,
                )

        assert was_cleaned is True
        assert had_error is False

    def test_update_failure_sets_error(self) -> None:
        client = MagicMock()
        point = {"id": "p1", "payload": {"content": "dirty  text", "document_name": "doc"}}

        with patch("scripts.batch_clean_chunks.clean_chunk_text", return_value="dirty text"):
            with patch("scripts.batch_clean_chunks.update_payload", return_value=False):
                was_cleaned, had_error = _process_chunk(
                    client, "kb_test", point, dry_run=False, cleaned_count=0,
                )

        assert was_cleaned is True
        assert had_error is True
