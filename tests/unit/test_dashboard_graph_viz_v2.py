"""Unit tests for dashboard/components/graph_viz_v2.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Mock streamlit before importing dashboard modules
st_mock = MagicMock()
st_mock.session_state = MagicMock()
st_mock.cache_data = MagicMock()
st_mock.cache_resource = MagicMock()
sys.modules.setdefault("streamlit", st_mock)
st_mock = sys.modules["streamlit"]
sys.modules.setdefault("streamlit.components", MagicMock())
sys.modules.setdefault("streamlit.components.v1", MagicMock())


from components.graph_viz import GraphEdge, GraphNode, NodeType, NODE_SHAPES
from components.graph_viz_v2 import (
    EDGE_COLORS,
    NODE_COLORS_V2,
    _node_border_style,
    _build_html,
    _build_interaction_js,
    _build_toast_js,
)


# ── _node_border_style ──


class TestNodeBorderStyle:
    def test_selected_node(self):
        bw, bc, sz = _node_border_style("n1", 20, "n1", set(), set(), set(), set())
        assert bw == 5
        assert bc == "#FFD700"
        assert sz == 40  # max(20, 40)

    def test_path_node(self):
        bw, bc, sz = _node_border_style("n1", 20, None, {"n1"}, set(), set(), set())
        assert bw == 4
        assert bc == "#FF1744"
        assert sz == 30

    def test_highlight_node(self):
        bw, bc, sz = _node_border_style("n1", 20, None, set(), {"n1"}, set(), set())
        assert bw == 4
        assert bc == "#FF9100"
        assert sz == 30

    def test_expanded_node(self):
        bw, bc, sz = _node_border_style("n1", 20, None, set(), set(), {"n1"}, set())
        assert bw == 4
        assert bc == "#00E5FF"
        assert sz == 35

    def test_search_node(self):
        bw, bc, sz = _node_border_style("n1", 25, None, set(), set(), set(), {"n1"})
        assert bw == 5
        assert bc == "#FFD700"
        assert sz == 25  # keeps base_size

    def test_default_node(self):
        bw, bc, sz = _node_border_style("n1", 25, None, set(), set(), set(), set())
        assert bw == 2
        assert bc is None
        assert sz == 25

    def test_priority_order_selected_over_path(self):
        """Selected takes priority over path."""
        bw, bc, _ = _node_border_style("n1", 20, "n1", {"n1"}, {"n1"}, {"n1"}, {"n1"})
        assert bc == "#FFD700"  # selected color

    def test_priority_order_path_over_highlight(self):
        bw, bc, _ = _node_border_style("n1", 20, None, {"n1"}, {"n1"}, {"n1"}, {"n1"})
        assert bc == "#FF1744"  # path color

    def test_large_base_size_preserved_for_selected(self):
        bw, bc, sz = _node_border_style("n1", 60, "n1", set(), set(), set(), set())
        assert sz == 60  # max(60, 40) = 60


# ── Color/Shape dicts ──


class TestNodeColorsV2:
    def test_all_node_types_have_color(self):
        for nt in NodeType:
            assert nt in NODE_COLORS_V2, f"Missing color for {nt}"

    def test_colors_are_hex(self):
        import re
        for nt, color in NODE_COLORS_V2.items():
            assert re.match(r"^#[0-9A-Fa-f]{6}$", color), f"Bad color for {nt}: {color}"


class TestEdgeColors:
    def test_has_entries(self):
        assert len(EDGE_COLORS) >= 20

    def test_all_values_are_hex(self):
        import re
        for rel, color in EDGE_COLORS.items():
            assert re.match(r"^#[0-9A-Fa-f]{6}$", color), f"Bad color for {rel}: {color}"


# ── _build_html ──


class TestBuildHtml:
    def test_returns_valid_html(self):
        html = _build_html([], [], 600, "#1a1a2e", "", "")
        assert "<!DOCTYPE html>" in html
        assert "vis-network" in html
        assert "#1a1a2e" in html

    def test_sanitizes_bad_bgcolor(self):
        html = _build_html([], [], 600, "javascript:alert(1)", "", "")
        # The bad bgcolor should be replaced with the default
        assert "background-color: #1a1a2e" in html

    def test_includes_nodes_and_edges_data(self):
        nodes = [{"id": "n1", "label": "Test"}]
        edges = [{"from": "n1", "to": "n2"}]
        html = _build_html(nodes, edges, 600, "#1a1a2e", "", "")
        assert "Test" in html
        assert "n1" in html

    def test_includes_expand_js_when_provided(self):
        html = _build_html([], [], 600, "#1a1a2e", "// expand code", "")
        assert "// expand code" in html

    def test_height_in_html(self):
        html = _build_html([], [], 800, "#1a1a2e", "", "")
        assert "800px" in html


# ── JS builders ──


class TestJsBuilders:
    def test_interaction_js_has_hover_handler(self):
        js = _build_interaction_js()
        assert "hoverNode" in js
        assert "blurNode" in js
        assert "btnFit" in js
        assert "btnExport" in js

    def test_toast_js_has_function(self):
        js = _build_toast_js()
        assert "showToast" in js
