"""Unit tests for dashboard/components/graph_viz.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Mock streamlit + pyvis before importing
st_mock = MagicMock()
st_mock.session_state = MagicMock()
st_mock.cache_data = MagicMock()
st_mock.cache_resource = MagicMock()
sys.modules.setdefault("streamlit", st_mock)
st_mock = sys.modules["streamlit"]
sys.modules.setdefault("streamlit.components", MagicMock())
sys.modules.setdefault("streamlit.components.v1", MagicMock())


from components.graph_viz import (
    GraphEdge,
    GraphNode,
    NODE_COLORS,
    NODE_SHAPES,
    NodeType,
    _build_expand_js,
    _fallback_dblclick_js,
    _inject_dblclick_js,
    create_sample_graph,
)


# ── NodeType enum ──


class TestNodeType:
    def test_all_values_are_strings(self):
        for nt in NodeType:
            assert isinstance(nt.value, str)

    def test_person_value(self):
        assert NodeType.PERSON.value == "Person"

    def test_document_value(self):
        assert NodeType.DOCUMENT.value == "Document"

    def test_enum_count(self):
        assert len(NodeType) == 15


# ── GraphNode / GraphEdge dataclasses ──


class TestGraphNode:
    def test_creation(self):
        node = GraphNode("id1", "label1", NodeType.PERSON)
        assert node.id == "id1"
        assert node.label == "label1"
        assert node.node_type == NodeType.PERSON
        assert node.properties is None

    def test_with_properties(self):
        node = GraphNode("id1", "label1", NodeType.TEAM, {"key": "val"})
        assert node.properties == {"key": "val"}


class TestGraphEdge:
    def test_creation(self):
        edge = GraphEdge("src", "tgt", "MEMBER_OF")
        assert edge.source == "src"
        assert edge.target == "tgt"
        assert edge.relation_type == "MEMBER_OF"
        assert edge.properties is None


# ── Dicts ──


class TestNodeDicts:
    def test_all_node_types_have_color(self):
        for nt in NodeType:
            assert nt in NODE_COLORS, f"Missing color for {nt}"

    def test_shapes_subset_of_types(self):
        for nt in NODE_SHAPES:
            assert nt in NodeType.__members__.values()


# ── create_sample_graph ──


class TestCreateSampleGraph:
    def test_returns_nodes_and_edges(self):
        nodes, edges = create_sample_graph()
        assert len(nodes) > 0
        assert len(edges) > 0

    def test_nodes_are_graph_nodes(self):
        nodes, _ = create_sample_graph()
        for n in nodes:
            assert isinstance(n, GraphNode)

    def test_edges_are_graph_edges(self):
        _, edges = create_sample_graph()
        for e in edges:
            assert isinstance(e, GraphEdge)

    def test_edge_sources_in_nodes(self):
        nodes, edges = create_sample_graph()
        node_ids = {n.id for n in nodes}
        for e in edges:
            assert e.source in node_ids, f"Edge source {e.source} not in nodes"
            assert e.target in node_ids, f"Edge target {e.target} not in nodes"


# ── _build_expand_js ──


class TestBuildExpandJs:
    def test_returns_js_string(self):
        js = _build_expand_js("http://localhost:8000")
        assert isinstance(js, str)
        assert "doubleClick" in js
        assert "localhost:8000" in js

    def test_contains_expand_url(self):
        js = _build_expand_js("http://example.com")
        assert "/api/v1/admin/graph/expand" in js


# ── _fallback_dblclick_js ──


class TestFallbackDblclickJs:
    def test_disabled_returns_empty(self):
        assert _fallback_dblclick_js(False) == ""

    def test_no_url_returns_empty(self):
        assert _fallback_dblclick_js(True, "") == ""

    def test_enabled_with_url_returns_js(self):
        js = _fallback_dblclick_js(True, "http://localhost:8000")
        assert "doubleClick" in js


# ── _inject_dblclick_js ──


class TestInjectDblclickJs:
    def test_injects_before_body_close(self):
        html = "<html><body></body></html>"
        result = _inject_dblclick_js(html, "http://localhost:8000")
        assert "<script" in result
        assert result.index("<script") < result.index("</body>")

    def test_contains_expand_handler(self):
        html = "<html><body></body></html>"
        result = _inject_dblclick_js(html, "http://localhost:8000")
        assert "doubleClick" in result
