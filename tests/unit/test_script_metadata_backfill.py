"""Unit tests for scripts/run_metadata_backfill.py."""

from __future__ import annotations

from collections import Counter
from unittest.mock import MagicMock, patch

import pytest

from scripts.run_metadata_backfill import (
    _build_point_update,
    _process_backfill_point,
)


# ---------------------------------------------------------------------------
# _build_point_update
# ---------------------------------------------------------------------------


class TestBuildPointUpdate:
    def test_all_fields_set(self) -> None:
        payload = {}
        cached = {"owner": "김철수", "l1_category": "운영", "quality_score": 0.8}
        stats = {"owner_set": 0, "category_set": 0, "score_set": 0}
        cat_counts = Counter()
        owner_counts = Counter()

        result = _build_point_update(payload, cached, force=False, stats=stats,
                                     category_counts=cat_counts, owner_counts=owner_counts)

        assert result["owner"] == "김철수"
        assert result["l1_category"] == "운영"
        assert result["quality_score"] == 0.8
        assert stats["owner_set"] == 1
        assert stats["category_set"] == 1
        assert stats["score_set"] == 1

    def test_skip_existing_owner(self) -> None:
        payload = {"owner": "기존소유자"}
        cached = {"owner": "새소유자", "l1_category": "운영", "quality_score": 0.8}
        stats = {"owner_set": 0, "category_set": 0, "score_set": 0}

        result = _build_point_update(payload, cached, force=False, stats=stats,
                                     category_counts=Counter(), owner_counts=Counter())

        assert "owner" not in result  # existing owner not overwritten

    def test_force_overwrites(self) -> None:
        payload = {"owner": "기존", "l1_category": "기타", "quality_score": 0.5}
        cached = {"owner": "새소유자", "l1_category": "운영", "quality_score": 0.9}
        stats = {"owner_set": 0, "category_set": 0, "score_set": 0}

        result = _build_point_update(payload, cached, force=True, stats=stats,
                                     category_counts=Counter(), owner_counts=Counter())

        assert result["owner"] == "새소유자"
        assert result["l1_category"] == "운영"
        assert result["quality_score"] == 0.9

    def test_empty_cached_owner(self) -> None:
        payload = {}
        cached = {"owner": "", "l1_category": "운영", "quality_score": 0.5}
        stats = {"owner_set": 0, "category_set": 0, "score_set": 0}

        result = _build_point_update(payload, cached, force=False, stats=stats,
                                     category_counts=Counter(), owner_counts=Counter())

        # Empty owner should not be set
        assert "owner" not in result or result.get("owner") == ""


# ---------------------------------------------------------------------------
# _process_backfill_point
# ---------------------------------------------------------------------------


class TestProcessBackfillPoint:
    def test_already_has_all_fields(self) -> None:
        point = {
            "id": "pt1",
            "payload": {
                "owner": "existing",
                "l1_category": "운영",
                "quality_score": 0.8,
                "doc_id": "d1",
            },
        }
        stats = {"already_has": 0, "owner_set": 0, "category_set": 0, "score_set": 0}
        updates = []

        _process_backfill_point(
            point, force=False, doc_cache={}, stats=stats,
            category_counts=Counter(), owner_counts=Counter(), updates=updates,
        )

        assert stats["already_has"] == 1
        assert len(updates) == 0

    def test_computes_and_queues_update(self) -> None:
        point = {
            "id": "pt1",
            "payload": {
                "doc_id": "d1",
                "document_name": "test_doc",
                "content": "some content here",
            },
        }
        stats = {"already_has": 0, "owner_set": 0, "category_set": 0, "score_set": 0}
        updates = []
        doc_cache = {}

        mock_metadata = {"owner": "김철수", "l1_category": "운영", "quality_score": 0.75}

        with patch("scripts.run_metadata_backfill._compute_doc_metadata", return_value=mock_metadata):
            _process_backfill_point(
                point, force=False, doc_cache=doc_cache, stats=stats,
                category_counts=Counter(), owner_counts=Counter(), updates=updates,
            )

        assert len(updates) == 1
        assert updates[0][0] == "pt1"
        assert "d1" in doc_cache

    def test_force_overrides_existing(self) -> None:
        point = {
            "id": "pt1",
            "payload": {
                "owner": "old",
                "l1_category": "기타",
                "quality_score": 0.5,
                "doc_id": "d1",
            },
        }
        stats = {"already_has": 0, "owner_set": 0, "category_set": 0, "score_set": 0}
        updates = []

        mock_metadata = {"owner": "새소유자", "l1_category": "운영", "quality_score": 0.9}

        with patch("scripts.run_metadata_backfill._compute_doc_metadata", return_value=mock_metadata):
            _process_backfill_point(
                point, force=True, doc_cache={}, stats=stats,
                category_counts=Counter(), owner_counts=Counter(), updates=updates,
            )

        assert len(updates) == 1
        assert stats["already_has"] == 0

    def test_uses_doc_cache(self) -> None:
        """When doc_id is already in cache, should not call _compute_doc_metadata."""
        point = {
            "id": "pt1",
            "payload": {"doc_id": "d1"},
        }
        stats = {"already_has": 0, "owner_set": 0, "category_set": 0, "score_set": 0}
        updates = []
        doc_cache = {"d1": {"owner": "cached", "l1_category": "기타", "quality_score": 0.6}}

        with patch("scripts.run_metadata_backfill._compute_doc_metadata") as mock_compute:
            _process_backfill_point(
                point, force=False, doc_cache=doc_cache, stats=stats,
                category_counts=Counter(), owner_counts=Counter(), updates=updates,
            )
            mock_compute.assert_not_called()
