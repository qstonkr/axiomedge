"""Unit tests for scripts/backfill_morphemes.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.backfill.backfill_morphemes import (
    _tally_points,
    _update_point_morphemes,
    extract_morphemes,
)


# ---------------------------------------------------------------------------
# extract_morphemes
# ---------------------------------------------------------------------------


class TestExtractMorphemes:
    def test_extracts_nouns(self) -> None:
        mock_kiwi = MagicMock()

        # Simulate KiwiPy tokens with NNG/NNP/SL tags
        mock_token_1 = MagicMock(form="시스템", tag="NNG")
        mock_token_2 = MagicMock(form="배포", tag="NNG")
        mock_token_3 = MagicMock(form="하다", tag="VV")  # verb, should be excluded
        mock_token_4 = MagicMock(form="Kubernetes", tag="SL")
        mock_token_5 = MagicMock(form="A", tag="SL")  # too short, excluded
        mock_kiwi.tokenize.return_value = [
            mock_token_1, mock_token_2, mock_token_3, mock_token_4, mock_token_5,
        ]

        result = extract_morphemes(mock_kiwi, "시스템 배포를 합니다 Kubernetes A")
        assert "시스템" in result
        assert "배포" in result
        assert "Kubernetes" in result
        assert "하다" not in result
        assert " A " not in result

    def test_empty_text(self) -> None:
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.return_value = []
        result = extract_morphemes(mock_kiwi, "")
        assert result == ""

    def test_exception_returns_empty(self) -> None:
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.side_effect = RuntimeError("tokenize error")
        result = extract_morphemes(mock_kiwi, "test text")
        assert result == ""

    def test_truncates_long_text(self) -> None:
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.return_value = []
        long_text = "x" * 5000
        extract_morphemes(mock_kiwi, long_text)
        # Should be called with truncated text
        call_text = mock_kiwi.tokenize.call_args[0][0]
        assert len(call_text) == 2000


# ---------------------------------------------------------------------------
# _update_point_morphemes
# ---------------------------------------------------------------------------


class TestUpdatePointMorphemes:
    def test_skips_existing_morphemes(self) -> None:
        point = {"id": "p1", "payload": {"content": "text", "morphemes": "existing"}}
        mock_kiwi = MagicMock()

        result = _update_point_morphemes("kb_test", point, mock_kiwi)
        assert result is False
        mock_kiwi.tokenize.assert_not_called()

    def test_skips_empty_extraction(self) -> None:
        point = {"id": "p1", "payload": {"content": "text"}}
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.return_value = []

        result = _update_point_morphemes("kb_test", point, mock_kiwi)
        assert result is False

    def test_updates_when_morphemes_extracted(self) -> None:
        point = {"id": "p1", "payload": {"content": "시스템 배포"}}
        mock_kiwi = MagicMock()
        mock_token = MagicMock(form="시스템", tag="NNG")
        mock_kiwi.tokenize.return_value = [mock_token]

        with patch("scripts.backfill.backfill_morphemes.requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            result = _update_point_morphemes("kb_test", point, mock_kiwi)

        assert result is True
        mock_requests.post.assert_called_once()


# ---------------------------------------------------------------------------
# _tally_points
# ---------------------------------------------------------------------------


class TestTallyPoints:
    def test_counts_updates_and_skips(self) -> None:
        mock_kiwi = MagicMock()

        # Point 1: already has morphemes (skip)
        # Point 2: needs morphemes (update)
        points = [
            {"id": "p1", "payload": {"content": "text", "morphemes": "existing"}},
            {"id": "p2", "payload": {"content": "시스템"}},
        ]

        mock_token = MagicMock(form="시스템", tag="NNG")
        mock_kiwi.tokenize.return_value = [mock_token]

        with patch("scripts.backfill.backfill_morphemes.requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=200)
            updated, skipped = _tally_points(points, "kb_test", mock_kiwi)

        assert updated == 1
        assert skipped == 1
