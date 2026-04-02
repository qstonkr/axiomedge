"""Unit tests for src/api/routes/admin.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import admin as admin_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    return state


# ============================================================================
# Graph Stats
# ============================================================================

class TestGraphStats:
    def test_with_graph(self):
        graph = AsyncMock()
        graph.get_stats = AsyncMock(return_value={"nodes": 100, "edges": 200})
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_stats())
        assert result["nodes"] == 100

    def test_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_stats())
        assert result["nodes"] == 0

    def test_exception(self):
        graph = AsyncMock()
        graph.get_stats = AsyncMock(side_effect=RuntimeError("neo4j down"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_stats())
        assert "error" in result


# ============================================================================
# Graph Search
# ============================================================================

class TestGraphSearch:
    def test_search_with_results(self):
        graph = AsyncMock()
        graph.search_entities = AsyncMock(return_value=[
            {"name": "Entity1", "entity_id": "e1", "node_type": "CONCEPT", "score": 0.9,
             "rel_type": "RELATED_TO", "connected_name": "Entity2", "connected_type": "PERSON"},
            {"name": "Entity1", "entity_id": "e1", "node_type": "CONCEPT", "score": 0.9,
             "rel_type": "HAS", "connected_name": "Entity3", "connected_type": "TOOL"},
        ])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_search({"query": "test entity", "max_nodes": 50}))
        assert result["total"] == 1  # Entities are grouped by name
        assert len(result["entities"][0]["relationships"]) == 2

    def test_search_empty(self):
        graph = AsyncMock()
        graph.search_entities = AsyncMock(return_value=[])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_search({"query": "nothing"}))
        assert result["total"] == 0

    def test_search_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_search({"query": "test"}))
        assert result["total"] == 0

    def test_search_exception(self):
        graph = AsyncMock()
        graph.search_entities = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_search({"query": "test"}))
        assert "error" in result

    def test_search_skip_empty_name(self):
        graph = AsyncMock()
        graph.search_entities = AsyncMock(return_value=[
            {"name": "", "entity_id": "", "node_type": "CONCEPT"},
        ])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_search({"query": "test"}))
        assert result["total"] == 0

    def test_search_no_rel_type(self):
        graph = AsyncMock()
        graph.search_entities = AsyncMock(return_value=[
            {"name": "E1", "entity_id": "e1", "node_type": "CONCEPT", "score": 1.0,
             "rel_type": None, "connected_name": None},
        ])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_search({"query": "test"}))
        assert result["total"] == 1
        assert result["entities"][0]["relationships"] == []


# ============================================================================
# Graph Experts
# ============================================================================

class TestGraphExperts:
    def test_find_experts(self):
        graph = AsyncMock()
        graph.find_experts = AsyncMock(return_value=[{"user": "u1", "score": 0.9}])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.find_experts(topic="kubernetes", limit=10))
        assert len(result["experts"]) == 1

    def test_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.find_experts(topic="test", limit=10))
        assert result["experts"] == []

    def test_exception(self):
        graph = AsyncMock()
        graph.find_experts = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.find_experts(topic="test", limit=10))
        assert "error" in result


# ============================================================================
# Graph Expand
# ============================================================================

class TestGraphExpand:
    def test_expand_with_method(self):
        graph = AsyncMock()
        graph.expand_node = AsyncMock(return_value={"node_id": "n1", "neighbors": [{"id": "n2"}]})
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_expand({"node_id": "n1"}))
        assert result["neighbors"][0]["id"] == "n2"

    def test_expand_no_method(self):
        graph = MagicMock(spec=[])  # no expand_node attribute
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_expand({"node_id": "n1"}))
        assert result["neighbors"] == []

    def test_expand_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_expand({"node_id": "n1"}))
        assert result["neighbors"] == []

    def test_expand_exception(self):
        graph = AsyncMock()
        graph.expand_node = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_expand({"node_id": "n1"}))
        assert "error" in result


# ============================================================================
# Graph Integrity Check (POST)
# ============================================================================

class TestGraphIntegrityCheck:
    def test_check_with_graph(self):
        graph = AsyncMock()
        client = AsyncMock()
        graph._client = client

        client.execute_query = AsyncMock(side_effect=[
            [{"cnt": 100}],   # total nodes
            [{"cnt": 200}],   # total edges
            [{"cnt": 5}],     # orphans
            [{"type": "CONCEPT", "name": "orphan1"}],  # orphan samples
            [{"cnt": 3}],     # no category
            [{"cnt": 2}],     # no owner
            [{"cnt": 0}],     # dangling
        ])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_integrity_check())
        assert result["total_nodes"] == 100
        assert result["orphan_count"] == 5
        assert result["missing_relationships"] == 5  # 3 + 2

    def test_check_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_integrity_check())
        assert result["total_nodes"] == 0

    def test_check_exception(self):
        graph = AsyncMock()
        graph._client = AsyncMock()
        graph._client.execute_query = AsyncMock(side_effect=RuntimeError("neo4j"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_integrity_check())
        assert "error" in result

    def test_check_no_orphans(self):
        graph = AsyncMock()
        client = AsyncMock()
        graph._client = client
        client.execute_query = AsyncMock(side_effect=[
            [{"cnt": 10}],   # nodes
            [{"cnt": 20}],   # edges
            [{"cnt": 0}],    # orphans
            [{"cnt": 0}],    # no category
            [{"cnt": 0}],    # no owner
            [{"cnt": 0}],    # dangling
        ])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_integrity_check())
        assert result["orphan_count"] == 0
        assert result["details"] == []


# ============================================================================
# Graph Shortest Path
# ============================================================================

class TestGraphPath:
    def test_path_with_method(self):
        graph = AsyncMock()
        graph.shortest_path = AsyncMock(return_value={"path": ["a", "b", "c"], "length": 2})
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_path({"from_node_id": "a", "to_node_id": "c"}))
        assert result["length"] == 2

    def test_path_no_method(self):
        graph = MagicMock(spec=[])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_path({"from_node_id": "a", "to_node_id": "c"}))
        assert result["path"] == []

    def test_path_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_path({"from_node_id": "a", "to_node_id": "c"}))
        assert result["path"] == []


# ============================================================================
# Graph Communities
# ============================================================================

class TestGraphCommunities:
    def test_communities_with_method(self):
        graph = AsyncMock()
        graph.get_communities = AsyncMock(return_value={"communities": [{"id": 1}], "total": 1})
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_communities())
        assert result["total"] == 1

    def test_no_method(self):
        graph = MagicMock(spec=[])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_communities())
        assert result["total"] == 0

    def test_no_graph(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_communities())
        assert result["total"] == 0


# ============================================================================
# Graph Integrity (GET)
# ============================================================================

class TestGraphIntegrity:
    def test_with_checker(self):
        checker = AsyncMock()
        report = MagicMock()
        report.to_dict.return_value = {"status": "ok", "orphan_nodes": 2, "dangling_edges": 0, "missing_relationships": 0, "total_issues": 2, "issues": []}
        checker.check_integrity = AsyncMock(return_value=report)
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_integrity=checker)):
            result = _run(admin_mod.graph_integrity())
        assert result["status"] == "ok"

    def test_no_checker(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_integrity())
        assert result["status"] == "ok"
        assert result["orphan_nodes"] == 0

    def test_exception(self):
        checker = AsyncMock()
        checker.check_integrity = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_integrity=checker)):
            result = _run(admin_mod.graph_integrity())
        assert result["status"] == "error"


# ============================================================================
# Run Graph Integrity Check (POST)
# ============================================================================

class TestRunGraphIntegrityCheck:
    def test_run_with_checker(self):
        checker = AsyncMock()
        report = MagicMock()
        report.to_dict.return_value = {"status": "ok", "total_issues": 0}
        checker.check_integrity = AsyncMock(return_value=report)
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_integrity=checker)):
            result = _run(admin_mod.run_graph_integrity_check({"kb_id": "kb1"}))
        assert result["success"] is True

    def test_run_no_checker(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.run_graph_integrity_check())
        assert result["success"] is True

    def test_run_with_none_body(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.run_graph_integrity_check(None))
        assert result["success"] is True

    def test_run_exception(self):
        checker = AsyncMock()
        checker.check_integrity = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_integrity=checker)):
            result = _run(admin_mod.run_graph_integrity_check())
        assert result["success"] is False


# ============================================================================
# Graph Impact
# ============================================================================

class TestGraphImpact:
    def test_impact_with_searcher(self):
        @dataclass
        class FakeRelated:
            id: str = "n1"
            name: str = "Node1"
            type: str = "CONCEPT"
            distance: int = 1
            relation_types: list = None
            relevance_score: float = 0.8

            def __post_init__(self):
                if self.relation_types is None:
                    self.relation_types = ["RELATED_TO"]

        searcher = AsyncMock()
        searcher.search_related = AsyncMock(return_value=[FakeRelated()])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(multi_hop_searcher=searcher)):
            result = _run(admin_mod.graph_impact({"node_id": "n0", "max_hops": 2}))
        assert result["total_impacted"] == 1
        assert result["impacted_nodes"][0]["name"] == "Node1"

    def test_impact_no_searcher(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_impact({"node_id": "n0"}))
        assert result["total_impacted"] == 0

    def test_impact_exception(self):
        searcher = AsyncMock()
        searcher.search_related = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(multi_hop_searcher=searcher)):
            result = _run(admin_mod.graph_impact({"node_id": "n0"}))
        assert "error" in result


# ============================================================================
# Graph Health
# ============================================================================

class TestGraphHealth:
    def test_healthy(self):
        graph = AsyncMock()
        graph.get_stats = AsyncMock(return_value={"nodes": 10, "edges": 20})
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_health())
        assert result["status"] == "healthy"

    def test_degraded(self):
        graph = AsyncMock()
        graph.get_stats = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(graph_repo=graph)):
            result = _run(admin_mod.graph_health())
        assert result["status"] == "degraded"

    def test_disconnected(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.graph_health())
        assert result["status"] == "disconnected"


# ============================================================================
# Graph Timeline
# ============================================================================

class TestGraphTimeline:
    def test_timeline(self):
        result = _run(admin_mod.graph_timeline({"node_id": "n1"}))
        assert result["node_id"] == "n1"
        assert result["events"] == []


# ============================================================================
# Qdrant Collections
# ============================================================================

class TestQdrantCollections:
    def test_list_collections(self):
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["col1", "col2"])
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(qdrant_collections=collections)):
            result = _run(admin_mod.list_collections())
        assert result["collections"] == ["col1", "col2"]

    def test_no_collections(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            result = _run(admin_mod.list_collections())
        assert result["collections"] == []

    def test_exception(self):
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(qdrant_collections=collections)):
            result = _run(admin_mod.list_collections())
        assert "error" in result


class TestCollectionStats:
    def test_stats(self):
        store = AsyncMock()
        store.count = AsyncMock(return_value=500)
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(qdrant_store=store)):
            result = _run(admin_mod.collection_stats("col1"))
        assert result["point_count"] == 500

    def test_no_store(self):
        with patch.object(admin_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(Exception) as exc_info:
                _run(admin_mod.collection_stats("col1"))
            assert exc_info.value.status_code == 503

    def test_exception(self):
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(admin_mod, "_get_state", return_value=_mock_state(qdrant_store=store)):
            with pytest.raises(Exception) as exc_info:
                _run(admin_mod.collection_stats("col1"))
            assert exc_info.value.status_code == 500


# ============================================================================
# Config Weights
# ============================================================================

class TestConfigWeights:
    def test_get_weights(self):
        result = _run(admin_mod.get_config_weights())
        assert isinstance(result, dict)

    def test_update_weights(self):
        from src.config_weights import weights
        original = weights.to_dict()
        try:
            result = _run(admin_mod.update_config_weights({"search.top_k": 10}))
            assert "applied" in result
        finally:
            weights.reset()

    def test_update_weights_empty_body(self):
        with pytest.raises(Exception) as exc_info:
            _run(admin_mod.update_config_weights({}))
        assert exc_info.value.status_code == 400

    def test_update_weights_invalid_fields(self):
        with pytest.raises(Exception) as exc_info:
            _run(admin_mod.update_config_weights({"nonexistent.field": 42}))
        assert exc_info.value.status_code == 400

    def test_reset_weights(self):
        result = _run(admin_mod.reset_config_weights())
        assert result["status"] == "reset"
