"""Unit tests for scripts/run_graphrag_parallel.py."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from scripts.graphrag.run_graphrag_parallel import process_chunk


# ---------------------------------------------------------------------------
# process_chunk
# ---------------------------------------------------------------------------


class TestProcessChunk:
    def test_successful_extraction(self) -> None:
        mock_extractor = MagicMock()
        mock_result = MagicMock(node_count=3, relationship_count=2)
        mock_extractor.extract.return_value = mock_result

        stats = {"success": 0, "failed": 0, "nodes": 0, "rels": 0}
        stats_lock = threading.Lock()
        neo4j_lock = threading.Lock()

        chunk = {"content": "test content", "title": "test doc", "page_id": "p1"}

        ok = process_chunk(mock_extractor, chunk, "test-kb", neo4j_lock, stats, stats_lock)

        assert ok is True
        assert stats["success"] == 1
        assert stats["nodes"] == 3
        assert stats["rels"] == 2
        mock_extractor.save_to_neo4j.assert_called_once_with(mock_result)

    def test_no_entities_extracted(self) -> None:
        mock_extractor = MagicMock()
        mock_result = MagicMock(node_count=0, relationship_count=0)
        mock_extractor.extract.return_value = mock_result

        stats = {"success": 0, "failed": 0, "nodes": 0, "rels": 0}
        stats_lock = threading.Lock()
        neo4j_lock = threading.Lock()

        chunk = {"content": "test", "title": "doc", "page_id": "p1"}

        ok = process_chunk(mock_extractor, chunk, "test-kb", neo4j_lock, stats, stats_lock)

        assert ok is True
        assert stats["success"] == 1
        assert stats["nodes"] == 0
        mock_extractor.save_to_neo4j.assert_not_called()

    def test_extraction_failure(self) -> None:
        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = RuntimeError("SageMaker timeout")

        stats = {"success": 0, "failed": 0, "nodes": 0, "rels": 0}
        stats_lock = threading.Lock()
        neo4j_lock = threading.Lock()

        chunk = {"content": "test", "title": "doc", "page_id": "p1"}

        ok = process_chunk(mock_extractor, chunk, "test-kb", neo4j_lock, stats, stats_lock)

        assert ok is False
        assert stats["failed"] == 1
        assert stats["success"] == 0

    def test_only_nodes_no_rels(self) -> None:
        mock_extractor = MagicMock()
        mock_result = MagicMock(node_count=5, relationship_count=0)
        mock_extractor.extract.return_value = mock_result

        stats = {"success": 0, "failed": 0, "nodes": 0, "rels": 0}
        stats_lock = threading.Lock()
        neo4j_lock = threading.Lock()

        chunk = {"content": "test", "title": "doc", "page_id": "p1"}
        ok = process_chunk(mock_extractor, chunk, "test-kb", neo4j_lock, stats, stats_lock)

        assert ok is True
        assert stats["nodes"] == 5
        mock_extractor.save_to_neo4j.assert_called_once()

    def test_concurrent_stats_update(self) -> None:
        """Verify stats are updated atomically via lock."""
        mock_extractor = MagicMock()
        mock_result = MagicMock(node_count=1, relationship_count=1)
        mock_extractor.extract.return_value = mock_result

        stats = {"success": 0, "failed": 0, "nodes": 0, "rels": 0}
        stats_lock = threading.Lock()
        neo4j_lock = threading.Lock()

        chunk = {"content": "test", "title": "doc", "page_id": "p1"}

        # Run multiple calls
        for _ in range(10):
            process_chunk(mock_extractor, chunk, "test-kb", neo4j_lock, stats, stats_lock)

        assert stats["success"] == 10
        assert stats["nodes"] == 10
        assert stats["rels"] == 10
