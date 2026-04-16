"""Coverage backfill — DistillService helper functions.

Tests _prefer_reformatted logic and DistillService init.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.distill.service import DistillService, _prefer_reformatted


class TestPreferReformatted:
    """Tests for _prefer_reformatted — training data prioritization."""

    def test_no_reformatted_returns_original(self) -> None:
        rows = [
            {"id": "1", "source_type": "usage_log"},
            {"id": "2", "source_type": "test_seed"},
        ]
        result = _prefer_reformatted(rows)
        assert len(result) == 2

    def test_reformatted_replaces_original(self) -> None:
        rows = [
            {"id": "orig-1", "source_type": "usage_log"},
            {"id": "ref-1", "source_type": "reformatted", "augmented_from": "orig-1"},
        ]
        result = _prefer_reformatted(rows)
        # orig-1 should be removed, ref-1 should remain
        ids = {r["id"] for r in result}
        assert "ref-1" in ids
        assert "orig-1" not in ids

    def test_reformatted_aug_kept_alongside(self) -> None:
        rows = [
            {"id": "orig-1", "source_type": "usage_log"},
            {"id": "ref-1", "source_type": "reformatted", "augmented_from": "orig-1"},
            {"id": "aug-1", "source_type": "reformatted_aug", "augmented_from": "ref-1"},
        ]
        result = _prefer_reformatted(rows)
        ids = {r["id"] for r in result}
        assert "ref-1" in ids
        assert "aug-1" in ids
        assert "orig-1" not in ids

    def test_no_augmented_from_keeps_original(self) -> None:
        rows = [
            {"id": "orig-1", "source_type": "usage_log"},
            {"id": "ref-1", "source_type": "reformatted"},  # No augmented_from
        ]
        result = _prefer_reformatted(rows)
        ids = {r["id"] for r in result}
        # orig-1 not replaced (no augmented_from link)
        assert "orig-1" in ids
        assert "ref-1" in ids

    def test_empty_input(self) -> None:
        assert _prefer_reformatted([]) == []

    def test_only_reformatted(self) -> None:
        rows = [
            {"id": "ref-1", "source_type": "reformatted", "augmented_from": "orig-1"},
        ]
        result = _prefer_reformatted(rows)
        assert len(result) == 1

    def test_mixed_sources_untouched(self) -> None:
        rows = [
            {"id": "1", "source_type": "usage_log"},
            {"id": "2", "source_type": "test_seed"},
            {"id": "3", "source_type": "manual"},
        ]
        result = _prefer_reformatted(rows)
        assert len(result) == 3


class TestDistillServiceInit:
    def test_default_qdrant_url_from_settings(self) -> None:
        config = MagicMock()
        session_factory = MagicMock()
        service = DistillService(config, session_factory)
        assert service.qdrant_url  # Should have a default value

    def test_custom_qdrant_url(self) -> None:
        config = MagicMock()
        session_factory = MagicMock()
        service = DistillService(config, session_factory, qdrant_url="http://custom:6333")
        assert service.qdrant_url == "http://custom:6333"

    def test_embedder_stored(self) -> None:
        config = MagicMock()
        session_factory = MagicMock()
        embedder = MagicMock()
        service = DistillService(config, session_factory, embedder=embedder)
        assert service.embedder is embedder

    def test_sagemaker_client_stored(self) -> None:
        config = MagicMock()
        session_factory = MagicMock()
        llm = MagicMock()
        service = DistillService(config, session_factory, sagemaker_client=llm)
        assert service.llm is llm
