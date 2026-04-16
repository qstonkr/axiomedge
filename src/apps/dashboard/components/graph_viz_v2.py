"""Graph Visualization v2

Enhanced knowledge graph rendering with WCAG AA colors, hover-highlight,
degree-based sizing, Korean labels, and export. Uses vis.js CDN directly.

Created: 2026-03-14
"""

import json
from typing import Any

import streamlit.components.v1 as components

from components.graph_viz import (
    GraphEdge,
    GraphNode,
    NodeType,
    NODE_SHAPES,
)
from components.graph_labels import (
    format_node_label,
    format_rel_label,
    sanitize_label,
    truncate_label,
)

# ── WCAG AA Node Colors (contrast ratio >= 4.5:1 on dark bg) ──

NODE_COLORS_V2: dict[NodeType, str] = {
    # PASS (kept from v1)
    NodeType.PERSON: "#2196F3",
    NodeType.TEAM: "#FF9800",
    NodeType.DOCUMENT: "#4CAF50",
    NodeType.LOGIC: "#00BCD4",
    NodeType.PROCESS: "#E91E63",
    NodeType.PROJECT: "#FFC107",
    NodeType.TOPIC: "#8BC34A",
    NodeType.KNOWLEDGE_BASE: "#FF5722",
    NodeType.PROCESS_STEP: "#F44336",
    # FIXED (6 items that failed WCAG AA -> lighter tones)
    NodeType.SYSTEM: "#90A4AE",
    NodeType.POLICY: "#CE93D8",
    NodeType.TERM: "#4DB6AC",
    NodeType.ROLE: "#7986CB",
    NodeType.ATTACHMENT: "#A1887F",
    NodeType.ENTITY: "#B39DDB",
}

# ── Edge Colors by Relationship Category ──

EDGE_COLORS: dict[str, str] = {
    # Organization (Blue)
    "MEMBER_OF": "#64B5F6",
    "PARTICIPATES_IN": "#64B5F6",
    "BELONGS_TO": "#64B5F6",
    # Management (Orange)
    "MANAGES": "#FFB74D",
    "RESPONSIBLE_FOR": "#FFB74D",
    "OWNS": "#81C784",
    # Definition/Implementation (Purple/Cyan)
    "DEFINES": "#BA68C8",
    "IMPLEMENTS": "#4DD0E1",
    # Structure (Neutral)
    "PART_OF": "#A1887F",
    "RELATED_TO": "#90A4AE",
    "CONNECTS_TO": "#7986CB",
    # Source/Reference (Warm)
    "EXTRACTED_FROM": "#FFD54F",
    "MENTIONS": "#F48FB1",
    "CREATED_BY": "#FFD54F",
    "MODIFIED_BY": "#FFD54F",
    "HAS_ATTACHMENT": "#A1887F",
    "COVERS": "#81C784",
    # Flow (Red)
    "NEXT_STEP": "#EF5350",
    "FLOWS_TO": "#EF5350",
    "SAME_CONCEPT": "#CE93D8",
    # History (Dimmed)
    "WAS_MEMBER_OF": "#3D6B98",
    "PREVIOUSLY_MANAGED": "#997040",
    "PREVIOUSLY_OWNED": "#4D7A4E",
    "WAS_RESPONSIBLE_FOR": "#997040",
    "PREVIOUSLY_PARTICIPATED_IN": "#3D6B98",
    "PREVIOUSLY_DEFINED": "#7A4580",
    "PREVIOUSLY_IMPLEMENTED": "#2D8088",
    "WAS_PART_OF": "#6B5549",
}

_DEFAULT_EDGE_COLOR = "#90A4AE"


def _node_border_style(
    node_id: str,
    base_size: int,
    selected_id: str | None,
    path_ids: set[str],
    highlight_ids: set[str],
    expanded_ids: set[str],
    search_ids: set[str],
) -> tuple[int, str | None, int]:
    """Return (border_width, border_color, adjusted_size) based on node state."""
    if node_id == selected_id:
        return 5, "#FFD700", max(base_size, 40)
    if node_id in path_ids:
        return 4, "#FF1744", max(base_size, 30)
    if node_id in highlight_ids:
        return 4, "#FF9100", max(base_size, 30)
    if node_id in expanded_ids:
        return 4, "#00E5FF", max(base_size, 35)
    if node_id in search_ids:
        return 5, "#FFD700", base_size
    return 2, None, base_size


def render_knowledge_graph_v2(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    height: int = 600,
    bgcolor: str = "#1a1a2e",
    _font_color: str = "white",
    expanded_ids: set[str] | None = None,
    selected_id: str | None = None,
    path_node_ids: set[str] | None = None,
    highlight_ids: set[str] | None = None,
    search_match_ids: set[str] | None = None,
    on_dblclick_expand: bool = False,
    api_base_url: str = "",
) -> None:
    """v2 graph rendering with industry-standard UX."""
    _expanded = expanded_ids or set()
    _path = path_node_ids or set()
    _highlight = highlight_ids or set()
    _search = search_match_ids or set()

    # Degree map for dynamic node sizing
    degree_map: dict[str, int] = {}
    for e in edges:
        degree_map[e.source] = degree_map.get(e.source, 0) + 1
        degree_map[e.target] = degree_map.get(e.target, 0) + 1

    # Build node data
    nodes_js: list[dict[str, Any]] = []
    for node in nodes:
        clean_label, tooltip_extra = sanitize_label(node.label, node.node_type.value)
        display_label = format_node_label(
            truncate_label(clean_label), node.node_type.value
        )
        full_tooltip = f"{node.node_type.value}: {node.label}"
        if tooltip_extra:
            full_tooltip += f"\n{tooltip_extra}"

        # Degree-based size (15-50)
        degree = degree_map.get(node.id, 0)
        base_size = min(50, max(15, 15 + degree * 3))

        bg_color = NODE_COLORS_V2.get(node.node_type, "#888888")
        border_width, border_color, base_size = _node_border_style(
            node.id, base_size, selected_id, _path, _highlight, _expanded, _search,
        )

        node_data: dict[str, Any] = {
            "id": node.id,
            "label": display_label,
            "shape": NODE_SHAPES.get(node.node_type, "dot"),
            "title": full_tooltip,
            "size": base_size,
            "borderWidth": border_width,
            "font": {"color": "#FFFFFF", "size": 12},
        }
        if border_color:
            node_data["color"] = {"background": bg_color, "border": border_color}
        else:
            node_data["color"] = bg_color

        nodes_js.append(node_data)

    # Build edge data
    edges_js: list[dict[str, Any]] = []
    for edge in edges:
        edge_color = EDGE_COLORS.get(edge.relation_type, _DEFAULT_EDGE_COLOR)
        edge_label = format_rel_label(edge.relation_type)
        edges_js.append({
            "from": edge.source,
            "to": edge.target,
            "label": edge_label,
            "title": edge.relation_type,
            "color": {"color": edge_color, "highlight": edge_color, "hover": edge_color},
            "font": {"color": "#CCCCCC", "size": 10, "align": "middle"},
        })

    # Build JS for expand + interaction
    expand_js = _build_expand_js_v2(api_base_url) if on_dblclick_expand and api_base_url else ""
    interaction_js = _build_interaction_js()

    html = _build_html(nodes_js, edges_js, height, bgcolor, expand_js, interaction_js)
    components.html(html, height=height + 50, scrolling=True)


def _build_interaction_js() -> str:
    """Hover-highlight-neighbors + stabilization + fit + export + toast."""
    return """
            // ── Hover-highlight-neighbors ──
            var _origColors = {};
            network.on("hoverNode", function(params) {
                var connected = network.getConnectedNodes(params.node);
                connected.push(params.node);
                var allIds = nodes.getIds();
                var updates = [];
                allIds.forEach(function(id) {
                    var node = nodes.get(id);
                    if (!_origColors[id]) {
                        _origColors[id] = {color: node.color, font: node.font};
                    }
                    if (connected.indexOf(id) === -1) {
                        updates.push({
                            id: id,
                            color: {background: '#333', border: '#555'},
                            font: {color: 'rgba(255,255,255,0.2)', size: 12}
                        });
                    }
                });
                nodes.update(updates);
            });
            network.on("blurNode", function() {
                var updates = [];
                Object.keys(_origColors).forEach(function(id) {
                    updates.push({
                        id: id,
                        color: _origColors[id].color,
                        font: _origColors[id].font || {color: '#FFFFFF', size: 12}
                    });
                });
                nodes.update(updates);
                _origColors = {};
            });

            // ── Stabilization → physics off ──
            network.on("stabilizationIterationsDone", function() {
                network.setOptions({physics: {enabled: false}});
            });

            // ── Floating toolbar (fit + export) ──
            var toolbar = document.createElement('div');
            toolbar.style.cssText = 'position:absolute;top:10px;right:10px;z-index:10;display:flex;gap:6px;';
            toolbar.innerHTML =
                '<button id="btnFit" style="padding:6px 12px;border-radius:6px;background:#2196F3;color:#fff;border:none;cursor:pointer;font-size:13px;">전체보기</button>' +
                '<button id="btnExport" style="padding:6px 12px;border-radius:6px;background:#4CAF50;color:#fff;border:none;cursor:pointer;font-size:13px;">PNG</button>';
            container.parentElement.style.position = 'relative';
            container.parentElement.appendChild(toolbar);
            document.getElementById('btnFit').onclick = function() {
                network.fit({animation: {duration: 500}});
            };
            document.getElementById('btnExport').onclick = function() {
                var canvas = container.querySelector('canvas');
                if (canvas) {
                    var link = document.createElement('a');
                    link.download = 'knowledge-graph.png';
                    link.href = canvas.toDataURL('image/png');
                    link.click();
                }
            };
"""


def _build_toast_js() -> str:
    """Toast notification helper function."""
    return """
            function showToast(msg) {
                var toast = document.createElement('div');
                toast.textContent = msg;
                toast.style.cssText = 'position:absolute;bottom:20px;left:50%;transform:translateX(-50%);' +
                    'background:rgba(33,150,243,0.9);color:#fff;padding:8px 20px;border-radius:20px;' +
                    'font-size:13px;z-index:20;transition:opacity 0.5s;';
                container.parentElement.appendChild(toast);
                setTimeout(function() { toast.style.opacity = '0'; }, 2500);
                setTimeout(function() { toast.remove(); }, 3000);
            }
"""


def _build_expand_js_v2(api_base_url: str) -> str:
    """doubleClick expand handler with toast notification."""
    color_map_js = json.dumps({nt.value: c for nt, c in NODE_COLORS_V2.items()})
    shape_map_js = json.dumps({nt.value: s for nt, s in NODE_SHAPES.items()})
    expand_url_js = json.dumps(api_base_url + "/api/v1/admin/graph/expand")

    return (
        _build_toast_js()
        + '\n'
        '            var _expandedIds = {};\n'
        '            network.on("doubleClick", function(params) {\n'
        '                if (!params.nodes || params.nodes.length === 0) return;\n'
        '                var nodeId = params.nodes[0];\n'
        '                if (_expandedIds[nodeId]) return;\n'
        '                _expandedIds[nodeId] = true;\n'
        '\n'
        '                fetch(' + expand_url_js + ', {\n'
        '                    method: "POST",\n'
        '                    headers: {"Content-Type": "application/json"},\n'
        '                    body: JSON.stringify({node_id: nodeId, max_neighbors: 30})\n'
        '                })\n'
        '                .then(function(r) { return r.json(); })\n'
        '                .then(function(data) {\n'
        '                    var colorMap = ' + color_map_js + ';\n'
        '                    var shapeMap = ' + shape_map_js + ';\n'
        '                    var existingNodes = nodes.getIds();\n'
        '                    var existingEdgeSet = {};\n'
        '                    edges.forEach(function(e) { existingEdgeSet[e.from+"|"+e.to+"|"+e.label] = true; });\n'
        '\n'
        '                    if (data.center_node && existingNodes.indexOf(data.center_node.id) === -1) {\n'
        '                        var cn = data.center_node;\n'
        '                        nodes.add({\n'
        '                            id: cn.id, label: cn.label || cn.name || "",\n'
        '                            color: colorMap[cn.node_type || cn.type] || "#888888",\n'
        '                            shape: shapeMap[cn.node_type || cn.type] || "dot",\n'
        '                            title: (cn.node_type||cn.type||"")+": "+(cn.label||cn.name||""),\n'
        '                            size: 25, borderWidth: 2,\n'
        '                            font: {color:"#FFFFFF", size:12}\n'
        '                        });\n'
        '                    }\n'
        '\n'
        '                    var addedCount = 0;\n'
        '                    (data.neighbors || []).forEach(function(n) {\n'
        '                        if (existingNodes.indexOf(n.id) === -1) {\n'
        '                            nodes.add({\n'
        '                                id: n.id, label: n.label || n.name || "",\n'
        '                                color: colorMap[n.node_type||n.type] || "#888888",\n'
        '                                shape: shapeMap[n.node_type||n.type] || "dot",\n'
        '                                title: (n.node_type||n.type||"")+": "+(n.label||n.name||""),\n'
        '                                size: 25, borderWidth: 2,\n'
        '                                font: {color:"#FFFFFF", size:12}\n'
        '                            });\n'
        '                            existingNodes.push(n.id);\n'
        '                            addedCount++;\n'
        '                        }\n'
        '                    });\n'
        '\n'
        '                    (data.edges || []).forEach(function(e) {\n'
        '                        var src = e.source||e.from||"";\n'
        '                        var tgt = e.target||e.to||"";\n'
        '                        var rel = e.relation_type||e.type||"RELATED_TO";\n'
        '                        var key = src+"|"+tgt+"|"+rel;\n'
        '                        if (!existingEdgeSet[key] && existingNodes.indexOf(src)!==-1 && existingNodes.indexOf(tgt)!==-1) {\n'
        '                            edges.add({from:src, to:tgt, label:rel});\n'
        '                            existingEdgeSet[key] = true;\n'
        '                        }\n'
        '                    });\n'
        '\n'
        '                    nodes.update({id:nodeId, borderWidth:4, color:{border:"#00E5FF",\n'
        '                        background:colorMap[data.center_node?(data.center_node.node_type||data.center_node.type):""]||"#888888"}});\n'
        '                    showToast(addedCount + "개 노드 확장됨");\n'
        '                })\n'
        '                .catch(function(err) {\n'
        '                    console.error("[OREO] Expand fetch failed:", err);\n'
        '                    _expandedIds[nodeId] = false;\n'
        '                    showToast("확장 실패");\n'
        '                });\n'
        '            });\n'
    )


def _build_html(
    nodes_js: list[dict],
    edges_js: list[dict],
    height: int,
    bgcolor: str,
    expand_js: str,
    interaction_js: str,
) -> str:
    """Build complete HTML with vis.js CDN."""
    nodes_json = json.dumps(nodes_js, ensure_ascii=False)
    edges_json = json.dumps(edges_js, ensure_ascii=False)

    # Sanitize bgcolor to prevent CSS injection
    import re
    if not re.match(r"^#[0-9a-fA-F]{3,8}$", bgcolor):
        bgcolor = "#1a1a2e"

    return f"""<!DOCTYPE html>
<html>
<head>
    <script type="text/javascript"
        src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{ margin: 0; padding: 0; overflow: hidden; }}
        #graph-container {{
            width: 100%;
            height: {height}px;
            background-color: {bgcolor};
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.1);
        }}
    </style>
</head>
<body>
    <div id="graph-container"></div>
    <script>
        var container = document.getElementById('graph-container');
        var nodes = new vis.DataSet({nodes_json});
        var edges = new vis.DataSet({edges_json});
        var data = {{ nodes: nodes, edges: edges }};
        var options = {{
            nodes: {{
                font: {{ color: 'white', size: 12 }},
                borderWidth: 2
            }},
            edges: {{
                font: {{ color: '#CCCCCC', size: 10, align: 'middle' }},
                arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                smooth: {{ type: 'continuous' }}
            }},
            physics: {{
                enabled: true,
                barnesHut: {{
                    gravitationalConstant: -2000,
                    centralGravity: 0.3,
                    springLength: 150,
                    springConstant: 0.04
                }},
                stabilization: {{ iterations: 200, fit: true }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 200,
                navigationButtons: true,
                keyboard: {{ enabled: true }},
                hideEdgesOnDrag: true
            }}
        }};
        var network = new vis.Network(container, data, options);

        // ── Interaction handlers ──
        {interaction_js}

        // ── Expand handler ──
        {expand_js}
    </script>
</body>
</html>"""
