"""Unit tests for dashboard/services/neo4j_service.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make dashboard modules importable

# Patch streamlit before importing the module
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.neo4j_service import (
    ALLOWED_RELATIONSHIPS,
    ALL_RELATION_TYPES,
    GraphData,
    GraphEdge,
    GraphNode,
    HISTORY_RELATIONSHIPS,
    Neo4jConfig,
    Neo4jService,
    NODE_TYPES,
)


# ===========================================================================
# Dataclass tests
# ===========================================================================

class TestNeo4jConfig:
    def test_defaults(self):
        cfg = Neo4jConfig()
        assert cfg.uri == "bolt://localhost:7687"
        assert cfg.user == "neo4j"
        assert cfg.password == ""
        assert cfg.database == "knowledge-graph"

    def test_custom_values(self):
        cfg = Neo4jConfig(uri="bolt://custom:7687", user="admin", password="pw", database="db")
        assert cfg.uri == "bolt://custom:7687"
        assert cfg.user == "admin"
        assert cfg.password == "pw"
        assert cfg.database == "db"


class TestGraphNode:
    def test_creation(self):
        node = GraphNode(id="n1", label="Test", node_type="Person", properties={"name": "test"})
        assert node.id == "n1"
        assert node.label == "Test"
        assert node.node_type == "Person"
        assert node.properties == {"name": "test"}


class TestGraphEdge:
    def test_creation(self):
        edge = GraphEdge(source="n1", target="n2", relation_type="MANAGES", properties={})
        assert edge.source == "n1"
        assert edge.target == "n2"
        assert edge.relation_type == "MANAGES"


class TestGraphData:
    def test_creation(self):
        data = GraphData(nodes=[], edges=[])
        assert data.nodes == []
        assert data.edges == []


# ===========================================================================
# Constants tests
# ===========================================================================

class TestConstants:
    def test_node_types_is_set(self):
        assert isinstance(NODE_TYPES, set)
        assert "Person" in NODE_TYPES
        assert "Document" in NODE_TYPES

    def test_allowed_relationships_is_set(self):
        assert isinstance(ALLOWED_RELATIONSHIPS, set)
        assert "MANAGES" in ALLOWED_RELATIONSHIPS

    def test_history_relationships_is_set(self):
        assert isinstance(HISTORY_RELATIONSHIPS, set)
        assert "WAS_MEMBER_OF" in HISTORY_RELATIONSHIPS

    def test_all_relation_types_union(self):
        assert ALL_RELATION_TYPES == ALLOWED_RELATIONSHIPS | HISTORY_RELATIONSHIPS


# ===========================================================================
# Neo4jService tests
# ===========================================================================

class TestNeo4jServiceInit:
    def test_init_with_config(self):
        cfg = Neo4jConfig(uri="bolt://test:7687", password="secret")
        svc = Neo4jService(config=cfg)
        assert svc.config.uri == "bolt://test:7687"
        assert svc._driver is None

    def test_init_without_config_uses_app_config(self):
        with patch("services.neo4j_service.app_config") as mock_cfg:
            mock_cfg.NEO4J_URI = "bolt://env:7687"
            mock_cfg.NEO4J_USER = "envuser"
            mock_cfg.NEO4J_PASSWORD = "envpw"
            mock_cfg.NEO4J_DATABASE = "envdb"
            svc = Neo4jService()
            assert svc.config.uri == "bolt://env:7687"
            assert svc.config.user == "envuser"


class TestNeo4jServiceConnect:
    def test_connect_when_neo4j_not_installed(self):
        """When AsyncGraphDatabase is None, connect is a no-op."""
        svc = Neo4jService(config=Neo4jConfig(password="test"))
        with patch("services.neo4j_service.AsyncGraphDatabase", None):
            svc.connect()
            assert svc._driver is None

    def test_connect_with_empty_password_logs_warning(self):
        cfg = Neo4jConfig(password="")
        svc = Neo4jService(config=cfg)
        mock_agd = MagicMock()
        with patch("services.neo4j_service.AsyncGraphDatabase", mock_agd), \
             patch("services.neo4j_service.logger") as mock_logger:
            svc.connect()
            mock_logger.warning.assert_called_once()
            assert mock_agd.driver.called

    def test_connect_creates_driver(self):
        cfg = Neo4jConfig(password="secret")
        svc = Neo4jService(config=cfg)
        mock_agd = MagicMock()
        mock_driver = MagicMock()
        mock_agd.driver.return_value = mock_driver
        with patch("services.neo4j_service.AsyncGraphDatabase", mock_agd):
            svc.connect()
            assert svc._driver is mock_driver
            mock_agd.driver.assert_called_once_with(
                cfg.uri, auth=(cfg.user, cfg.password)
            )


class TestNeo4jServiceClose:
    @pytest.mark.asyncio
    async def test_close_with_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        svc._driver = AsyncMock()
        await svc.close()
        svc._driver.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_without_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        await svc.close()  # should not raise


class TestNeo4jServiceContextManager:
    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        cfg = Neo4jConfig(password="pw")
        svc = Neo4jService(config=cfg)
        mock_agd = MagicMock()
        mock_driver = AsyncMock()
        mock_agd.driver.return_value = mock_driver
        with patch("services.neo4j_service.AsyncGraphDatabase", mock_agd):
            async with svc as s:
                assert s is svc
                assert svc._driver is mock_driver
            mock_driver.close.assert_awaited_once()


class TestNeo4jServiceEmptyFallbacks:
    """When driver is None, all query methods return empty results."""

    @pytest.mark.asyncio
    async def test_search_graph_returns_empty(self):
        svc = Neo4jService(config=Neo4jConfig())
        result = await svc.search_graph("test query")
        assert isinstance(result, GraphData)
        assert result.nodes == []
        assert result.edges == []

    @pytest.mark.asyncio
    async def test_find_experts_returns_empty(self):
        svc = Neo4jService(config=Neo4jConfig())
        result = await svc.find_experts("topic")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_knowledge_path_returns_empty(self):
        svc = Neo4jService(config=Neo4jConfig())
        result = await svc.get_knowledge_path("A", "B")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_graph_stats_returns_empty(self):
        svc = Neo4jService(config=Neo4jConfig())
        result = await svc.get_graph_stats()
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert result["status"] == "disconnected"


# ===========================================================================
# _parse_node / _parse_edge
# ===========================================================================

class TestParseNode:
    def test_none_node_skipped(self):
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(None, None, nodes_map)
        assert nodes_map == {}

    def test_node_with_type_filter_excluded(self):
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": "n1", "name": "Alice"}.get(k)
        node.labels = ["Person"]
        node.id = "n1"
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(node, ["Document"], nodes_map)
        assert nodes_map == {}

    def test_node_with_type_filter_included(self):
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": "n1", "name": "Alice"}.get(k)
        node.labels = ["Person"]
        node.id = "n1"
        node.__iter__ = MagicMock(return_value=iter([("name", "Alice")]))
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(node, ["Person"], nodes_map)
        assert "n1" in nodes_map
        assert nodes_map["n1"].node_type == "Person"
        assert nodes_map["n1"].label == "Alice"

    def test_node_no_type_filter(self):
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": "n2", "name": None, "title": "Doc"}.get(k)
        node.labels = ["Document"]
        node.id = "n2"
        node.__iter__ = MagicMock(return_value=iter([("title", "Doc")]))
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(node, None, nodes_map)
        assert "n2" in nodes_map
        assert nodes_map["n2"].label == "Doc"

    def test_duplicate_node_skipped(self):
        existing = GraphNode(id="n1", label="Old", node_type="Person", properties={})
        nodes_map = {"n1": existing}
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": "n1", "name": "New"}.get(k)
        node.labels = ["Person"]
        Neo4jService._parse_node(node, None, nodes_map)
        assert nodes_map["n1"].label == "Old"  # not overwritten

    def test_node_without_id_uses_internal_id(self):
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": None, "name": "Bob", "title": None}.get(k)
        node.labels = ["Team"]
        node.id = 42
        node.__iter__ = MagicMock(return_value=iter([("name", "Bob")]))
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(node, None, nodes_map)
        assert "42" in nodes_map

    def test_node_no_labels_uses_unknown(self):
        node = MagicMock()
        node.get.side_effect = lambda k: {"id": "n3", "name": "X", "title": None}.get(k)
        node.labels = []
        node.__iter__ = MagicMock(return_value=iter([]))
        nodes_map: dict[str, GraphNode] = {}
        Neo4jService._parse_node(node, None, nodes_map)
        assert nodes_map["n3"].node_type == "Unknown"


class TestParseEdge:
    def test_complete_edge(self):
        rel = MagicMock()
        rel.type = "MANAGES"
        rel.__iter__ = MagicMock(return_value=iter([]))
        start = MagicMock()
        start.get.side_effect = lambda k: {"id": "s1"}.get(k)
        end = MagicMock()
        end.get.side_effect = lambda k: {"id": "e1"}.get(k)
        record = {"rel": rel, "start": start, "end": end}
        edge = Neo4jService._parse_edge(record)
        assert edge is not None
        assert edge.source == "s1"
        assert edge.target == "e1"
        assert edge.relation_type == "MANAGES"

    def test_missing_rel_returns_none(self):
        assert Neo4jService._parse_edge({"rel": None, "start": MagicMock(), "end": MagicMock()}) is None

    def test_missing_start_returns_none(self):
        assert Neo4jService._parse_edge({"rel": MagicMock(), "start": None, "end": MagicMock()}) is None

    def test_missing_end_returns_none(self):
        assert Neo4jService._parse_edge({"rel": MagicMock(), "start": MagicMock(), "end": None}) is None

    def test_edge_uses_internal_id_fallback(self):
        rel = MagicMock()
        rel.type = "RELATED_TO"
        rel.__iter__ = MagicMock(return_value=iter([]))
        start = MagicMock()
        start.get.return_value = None
        start.id = 100
        end = MagicMock()
        end.get.return_value = None
        end.id = 200
        edge = Neo4jService._parse_edge({"rel": rel, "start": start, "end": end})
        assert edge is not None
        assert edge.source == "100"
        assert edge.target == "200"


class TestParseGraphData:
    def test_empty_records(self):
        svc = Neo4jService(config=Neo4jConfig())
        data = svc._parse_graph_data([], None)
        assert data.nodes == []
        assert data.edges == []

    def test_records_with_nodes_and_edges(self):
        svc = Neo4jService(config=Neo4jConfig())

        node_mock = MagicMock()
        node_mock.get.side_effect = lambda k: {"id": "n1", "name": "A", "title": None}.get(k)
        node_mock.labels = ["Person"]
        node_mock.__iter__ = MagicMock(return_value=iter([("name", "A")]))

        rel_mock = MagicMock()
        rel_mock.type = "MANAGES"
        rel_mock.__iter__ = MagicMock(return_value=iter([]))
        start_mock = MagicMock()
        start_mock.get.side_effect = lambda k: {"id": "n1"}.get(k)
        end_mock = MagicMock()
        end_mock.get.side_effect = lambda k: {"id": "n2"}.get(k)

        records = [{"node": node_mock, "rel": rel_mock, "start": start_mock, "end": end_mock}]
        data = svc._parse_graph_data(records, None)
        assert len(data.nodes) == 1
        assert len(data.edges) == 1

    def test_incomplete_edge_skipped(self):
        svc = Neo4jService(config=Neo4jConfig())
        records = [{"node": None, "rel": None, "start": None, "end": None}]
        data = svc._parse_graph_data(records, None)
        assert data.nodes == []
        assert data.edges == []


# ===========================================================================
# Search/query methods with driver
# ===========================================================================

def _make_async_cm(session_mock):
    """Create a proper async context manager that returns session_mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestNeo4jServiceWithDriver:
    @pytest.mark.asyncio
    async def test_search_graph_with_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data.return_value = []
        mock_session.run.return_value = mock_result

        mock_driver = MagicMock()
        mock_driver.session.return_value = _make_async_cm(mock_session)
        svc._driver = mock_driver

        result = await svc.search_graph("test", max_hops=1)
        assert isinstance(result, GraphData)
        mock_session.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_experts_with_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data.return_value = [
            {"name": "Alice", "person_id": "p1", "team": "T1", "doc_count": 5, "expertise_score": 0.25}
        ]
        mock_session.run.return_value = mock_result

        mock_driver = MagicMock()
        mock_driver.session.return_value = _make_async_cm(mock_session)
        svc._driver = mock_driver

        result = await svc.find_experts("topic", min_docs=3)
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_knowledge_path_with_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.data.return_value = [{"nodes": [], "relations": [], "distance": 2}]
        mock_session.run.return_value = mock_result

        mock_driver = MagicMock()
        mock_driver.session.return_value = _make_async_cm(mock_session)
        svc._driver = mock_driver

        result = await svc.get_knowledge_path("A", "B")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_graph_stats_with_driver(self):
        svc = Neo4jService(config=Neo4jConfig())
        mock_result_nodes = AsyncMock()
        mock_result_nodes.data.return_value = [{"label": "Person", "count": 10}]
        mock_result_edges = AsyncMock()
        mock_result_edges.data.return_value = [{"rel_type": "MANAGES", "count": 5}]

        mock_session1 = AsyncMock()
        mock_session1.run.return_value = mock_result_nodes
        mock_session2 = AsyncMock()
        mock_session2.run.return_value = mock_result_edges

        mock_driver = MagicMock()
        mock_driver.session.side_effect = [
            _make_async_cm(mock_session1),
            _make_async_cm(mock_session2),
        ]
        svc._driver = mock_driver

        result = await svc.get_graph_stats()
        assert result["total_nodes"] == 10
        assert result["total_edges"] == 5
        assert result["node_counts"]["Person"] == 10
        assert result["edge_counts"]["MANAGES"] == 5
