"""Graph Visualization Component

# DEPRECATED: This module is only imported by graph_viz_v2.py for shared constants
# (NodeType, NODE_COLORS, NODE_SHAPES, GraphNode, GraphEdge).
# New code should import from graph_viz_v2.py directly.

PyVis 기반 지식 그래프 렌더링.

Created: 2026-02-04 (Sprint 10)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

import streamlit.components.v1 as components

try:
    from pyvis.network import Network
except ImportError:
    Network = None


class NodeType(Enum):
    """그래프 노드 유형 (oreo-agents graph_builder.py 생성 노드와 정합)."""

    PERSON = "Person"                # 사람
    TEAM = "Team"                    # 팀/부서
    SYSTEM = "System"                # 시스템/서비스
    DOCUMENT = "Document"            # 문서
    POLICY = "Policy"                # 정책
    LOGIC = "Logic"                  # 비즈니스 로직
    PROCESS = "Process"              # 프로세스
    TERM = "Term"                    # 용어
    PROJECT = "Project"              # 프로젝트
    ROLE = "Role"                    # 역할
    ATTACHMENT = "Attachment"        # 첨부파일
    TOPIC = "Topic"                  # 토픽/주제
    KNOWLEDGE_BASE = "KnowledgeBase" # 지식베이스
    PROCESS_STEP = "ProcessStep"     # 프로세스 단계 (플로우차트)
    ENTITY = "Entity"                # 엔티티 (시각 분석 추출)


# 노드 유형별 색상
NODE_COLORS = {
    NodeType.PERSON: "#2196F3",         # Blue
    NodeType.TEAM: "#FF9800",           # Orange
    NodeType.SYSTEM: "#607D8B",         # Gray
    NodeType.DOCUMENT: "#4CAF50",       # Green
    NodeType.POLICY: "#9C27B0",         # Purple
    NodeType.LOGIC: "#00BCD4",          # Cyan
    NodeType.PROCESS: "#E91E63",        # Pink
    NodeType.TERM: "#009688",           # Teal
    NodeType.PROJECT: "#FFC107",        # Amber
    NodeType.ROLE: "#3F51B5",           # Indigo
    NodeType.ATTACHMENT: "#795548",     # Brown
    NodeType.TOPIC: "#8BC34A",          # Light Green
    NodeType.KNOWLEDGE_BASE: "#FF5722", # Deep Orange
    NodeType.PROCESS_STEP: "#F44336",   # Red
    NodeType.ENTITY: "#673AB7",         # Deep Purple
}

# 노드 유형별 모양
NODE_SHAPES = {
    NodeType.PERSON: "ellipse",
    NodeType.TEAM: "diamond",
    NodeType.SYSTEM: "star",
    NodeType.DOCUMENT: "box",
    NodeType.POLICY: "triangle",
    NodeType.LOGIC: "hexagon",
    NodeType.PROCESS: "square",
    NodeType.TERM: "dot",
    NodeType.PROJECT: "box",
    NodeType.ROLE: "ellipse",
    NodeType.ATTACHMENT: "triangleDown",
    NodeType.TOPIC: "circle",
    NodeType.KNOWLEDGE_BASE: "database",
}


@dataclass
class GraphNode:
    """그래프 노드."""

    id: str
    label: str
    node_type: NodeType
    properties: dict[str, Any] | None = None


@dataclass
class GraphEdge:
    """그래프 엣지."""

    source: str
    target: str
    relation_type: str
    properties: dict[str, Any] | None = None


def render_knowledge_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    height: int = 600,
    bgcolor: str = "#1a1a2e",
    font_color: str = "white",
    expanded_ids: set[str] | None = None,
    selected_id: str | None = None,
    path_node_ids: set[str] | None = None,
    highlight_ids: set[str] | None = None,
    on_dblclick_expand: bool = False,
    api_base_url: str = "",
) -> None:
    """
    PyVis를 사용하여 지식 그래프를 렌더링합니다.

    Args:
        nodes: 그래프 노드 목록
        edges: 그래프 엣지 목록
        height: 그래프 높이 (픽셀)
        bgcolor: 배경 색상
        font_color: 폰트 색상
        expanded_ids: 확장된 노드 ID (크기 35, 테두리 4px)
        selected_id: 선택된 노드 ID (금색 테두리, 크기 40)
        path_node_ids: 경로 노드 ID (빨간 테두리)
        highlight_ids: 영향도 하이라이트 노드 ID (주황 테두리)
    """
    _expanded = expanded_ids or set()
    _path = path_node_ids or set()
    _highlight = highlight_ids or set()

    if Network is None:
        _render_fallback_graph(
            nodes, edges, height,
            expanded_ids=_expanded, selected_id=selected_id,
            path_node_ids=_path, highlight_ids=_highlight,
            on_dblclick_expand=on_dblclick_expand,
            api_base_url=api_base_url,
        )
        return

    # PyVis 네트워크 생성
    net = Network(
        height=f"{height}px",
        width="100%",
        bgcolor=bgcolor,
        font_color=font_color,
        directed=True,
    )

    # 물리 엔진 설정
    net.barnes_hut(
        gravity=-2000,
        central_gravity=0.3,
        spring_length=150,
        spring_strength=0.04,
    )

    # 노드 추가 (하이라이트 적용)
    for node in nodes:
        size = 25
        border_width = 2
        border_color = None

        if node.id == selected_id:
            size = 40
            border_width = 5
            border_color = "#FFD700"  # 금색
        elif node.id in _path:
            size = 30
            border_width = 4
            border_color = "#FF1744"  # 빨간색
        elif node.id in _highlight:
            size = 30
            border_width = 4
            border_color = "#FF9100"  # 주황색
        elif node.id in _expanded:
            size = 35
            border_width = 4
            border_color = "#00E5FF"  # 시안

        color_val = NODE_COLORS.get(node.node_type, "#888888")
        if border_color:
            color_val = {"background": color_val, "border": border_color}

        net.add_node(
            node.id,
            label=node.label,
            color=color_val,
            shape=NODE_SHAPES.get(node.node_type, "dot"),
            title=f"{node.node_type.value}: {node.label}",
            size=size,
            borderWidth=border_width,
            font={"color": "#FFFFFF", "size": 14},
        )

    # 엣지 추가
    for edge in edges:
        net.add_edge(
            edge.source,
            edge.target,
            title=edge.relation_type,
            label=edge.relation_type,
            font={"color": "#CCCCCC", "size": 11, "align": "middle"},
        )

    # HTML 생성 및 렌더링
    html = net.generate_html()

    if on_dblclick_expand and api_base_url:
        html = _inject_dblclick_js(html, api_base_url)

    components.html(html, height=height + 50, scrolling=True)


def _build_expand_js(api_base_url: str) -> str:
    """vis.js 레이어 안에서 직접 fetch → DataSet 추가하는 doubleClick 핸들러."""
    import json

    color_map_js = json.dumps({nt.value: c for nt, c in NODE_COLORS.items()})
    shape_map_js = json.dumps({nt.value: s for nt, s in NODE_SHAPES.items()})
    expand_url = api_base_url + "/api/v1/admin/graph/expand"

    return (
        '            var _expandedIds = {};\n'
        '            console.log("[OREO] doubleClick expand handler attached (in-layer mode)");\n'
        '\n'
        '            network.on("doubleClick", function(params) {\n'
        '                if (!params.nodes || params.nodes.length === 0) return;\n'
        '                var nodeId = params.nodes[0];\n'
        '                if (_expandedIds[nodeId]) return;\n'
        '                _expandedIds[nodeId] = true;\n'
        '                console.log("[OREO] Expanding node:", nodeId);\n'
        '\n'
        '                fetch("' + expand_url + '", {\n'
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
        '                    edges.forEach(function(e) { existingEdgeSet[e.from + "|" + e.to + "|" + e.label] = true; });\n'
        '\n'
        '                    if (data.center_node && existingNodes.indexOf(data.center_node.id) === -1) {\n'
        '                        var cn = data.center_node;\n'
        '                        nodes.add({\n'
        '                            id: cn.id, label: cn.label || cn.name || "",\n'
        '                            color: colorMap[cn.node_type || cn.type] || "#888888",\n'
        '                            shape: shapeMap[cn.node_type || cn.type] || "dot",\n'
        '                            title: (cn.node_type || cn.type || "") + ": " + (cn.label || cn.name || ""),\n'
        '                            size: 25, borderWidth: 2,\n'
        '                            font: {color: "#FFFFFF", size: 14}\n'
        '                        });\n'
        '                    }\n'
        '\n'
        '                    (data.neighbors || []).forEach(function(n) {\n'
        '                        if (existingNodes.indexOf(n.id) === -1) {\n'
        '                            nodes.add({\n'
        '                                id: n.id, label: n.label || n.name || "",\n'
        '                                color: colorMap[n.node_type || n.type] || "#888888",\n'
        '                                shape: shapeMap[n.node_type || n.type] || "dot",\n'
        '                                title: (n.node_type || n.type || "") + ": " + (n.label || n.name || ""),\n'
        '                                size: 25, borderWidth: 2,\n'
        '                            font: {color: "#FFFFFF", size: 14}\n'
        '                            });\n'
        '                            existingNodes.push(n.id);\n'
        '                        }\n'
        '                    });\n'
        '\n'
        '                    (data.edges || []).forEach(function(e) {\n'
        '                        var src = e.source || e.from || "";\n'
        '                        var tgt = e.target || e.to || "";\n'
        '                        var rel = e.relation_type || e.type || "RELATED_TO";\n'
        '                        var key = src + "|" + tgt + "|" + rel;\n'
        '                        if (!existingEdgeSet[key] && existingNodes.indexOf(src) !== -1 && existingNodes.indexOf(tgt) !== -1) {\n'
        '                            edges.add({from: src, to: tgt, label: rel});\n'
        '                            existingEdgeSet[key] = true;\n'
        '                        }\n'
        '                    });\n'
        '\n'
        '                    nodes.update({id: nodeId, borderWidth: 4, color: {border: "#00E5FF", background: colorMap[data.center_node ? (data.center_node.node_type || data.center_node.type) : ""] || "#888888"}});\n'
        '                    console.log("[OREO] Expanded:", (data.neighbors || []).length, "nodes,", (data.edges || []).length, "edges");\n'
        '                })\n'
        '                .catch(function(err) {\n'
        '                    console.error("[OREO] Expand fetch failed:", err);\n'
        '                    _expandedIds[nodeId] = false;\n'
        '                });\n'
        '            });\n'
    )


def _fallback_dblclick_js(enabled: bool, api_base_url: str = "") -> str:
    """Fallback 렌더러용 doubleClick 인라인 JS."""
    if not enabled or not api_base_url:
        return ""
    return _build_expand_js(api_base_url)


def _inject_dblclick_js(html: str, api_base_url: str) -> str:
    """vis.js 네트워크에 doubleClick → in-layer expand 핸들러 삽입."""
    expand_js = _build_expand_js(api_base_url)
    script = f"""
    <script type="text/javascript">
    (function poll() {{
        if (typeof network !== 'undefined' && network && network.on &&
            typeof nodes !== 'undefined' && typeof edges !== 'undefined') {{
            {expand_js}
        }} else {{
            setTimeout(poll, 200);
        }}
    }})();
    </script>
    """
    return html.replace("</body>", script + "\n</body>")


def _render_fallback_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    height: int,
    expanded_ids: set[str] | None = None,
    selected_id: str | None = None,
    path_node_ids: set[str] | None = None,
    highlight_ids: set[str] | None = None,
    on_dblclick_expand: bool = False,
    api_base_url: str = "",
) -> None:
    """
    PyVis가 설치되지 않은 경우 vis.js CDN을 사용한 대체 렌더링.

    Args:
        nodes: 그래프 노드 목록
        edges: 그래프 엣지 목록
        height: 그래프 높이
        expanded_ids: 확장된 노드 ID
        selected_id: 선택된 노드 ID
        path_node_ids: 경로 노드 ID
        highlight_ids: 영향도 하이라이트 노드 ID
    """
    _expanded = expanded_ids or set()
    _path = path_node_ids or set()
    _highlight = highlight_ids or set()

    # 노드 데이터 생성
    nodes_js = []
    for node in nodes:
        size = 25
        border_width = 2
        border_color = None
        bg_color = NODE_COLORS.get(node.node_type, "#888888")

        if node.id == selected_id:
            size, border_width, border_color = 40, 5, "#FFD700"
        elif node.id in _path:
            size, border_width, border_color = 30, 4, "#FF1744"
        elif node.id in _highlight:
            size, border_width, border_color = 30, 4, "#FF9100"
        elif node.id in _expanded:
            size, border_width, border_color = 35, 4, "#00E5FF"

        node_data: dict[str, Any] = {
            "id": node.id,
            "label": node.label,
            "shape": NODE_SHAPES.get(node.node_type, "dot"),
            "title": f"{node.node_type.value}: {node.label}",
            "size": size,
            "borderWidth": border_width,
        }
        if border_color:
            node_data["color"] = {"background": bg_color, "border": border_color}
        else:
            node_data["color"] = bg_color
        nodes_js.append(node_data)

    # 엣지 데이터 생성
    edges_js = []
    for edge in edges:
        edges_js.append({
            "from": edge.source,
            "to": edge.target,
            "label": edge.relation_type,
        })

    # HTML 템플릿
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
        <style>
            #graph-container {{
                width: 100%;
                height: {height}px;
                border: 1px solid #444;
                background-color: #1a1a2e;
            }}
        </style>
    </head>
    <body>
        <div id="graph-container"></div>
        <script>
            var nodes = new vis.DataSet({nodes_js});
            var edges = new vis.DataSet({edges_js});

            var container = document.getElementById('graph-container');
            var data = {{ nodes: nodes, edges: edges }};
            var options = {{
                nodes: {{
                    font: {{ color: 'white', size: 14 }},
                    borderWidth: 2
                }},
                edges: {{
                    font: {{ color: 'white', size: 10, align: 'middle' }},
                    arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
                    color: {{ color: '#888' }}
                }},
                physics: {{
                    enabled: true,
                    barnesHut: {{
                        gravitationalConstant: -2000,
                        centralGravity: 0.3,
                        springLength: 150,
                        springConstant: 0.04
                    }}
                }},
                interaction: {{ hover: true, tooltipDelay: 200 }}
            }};
            var network = new vis.Network(container, data, options);
            {_fallback_dblclick_js(on_dblclick_expand, api_base_url)}
        </script>
    </body>
    </html>
    """

    components.html(html, height=height + 50, scrolling=True)


def create_sample_graph() -> tuple[list[GraphNode], list[GraphEdge]]:
    """데모용 샘플 그래프 생성."""
    nodes = [
        GraphNode("doc-1", "K8s 배포 가이드", NodeType.DOCUMENT),
        GraphNode("doc-2", "Docker 가이드", NodeType.DOCUMENT),
        GraphNode("doc-3", "MISO API 문서", NodeType.DOCUMENT),
        GraphNode("person-1", "김철수", NodeType.PERSON),
        GraphNode("person-2", "이영희", NodeType.PERSON),
        GraphNode("team-1", "인프라팀", NodeType.TEAM),
        GraphNode("team-2", "MISO팀", NodeType.TEAM),
        GraphNode("sys-1", "주문결제 시스템", NodeType.SYSTEM),
        GraphNode("term-1", "DM (데이터마트)", NodeType.TERM),
        GraphNode("policy-1", "배포 정책", NodeType.POLICY),
        GraphNode("process-1", "CI/CD 파이프라인", NodeType.PROCESS),
        GraphNode("role-1", "DevOps 엔지니어", NodeType.ROLE),
    ]

    edges = [
        GraphEdge("person-1", "team-1", "MEMBER_OF"),
        GraphEdge("person-2", "team-2", "MEMBER_OF"),
        GraphEdge("person-1", "sys-1", "MANAGES"),
        GraphEdge("person-1", "doc-1", "OWNS"),
        GraphEdge("person-2", "doc-3", "OWNS"),
        GraphEdge("team-1", "sys-1", "RESPONSIBLE_FOR"),
        GraphEdge("doc-1", "process-1", "DEFINES"),
        GraphEdge("sys-1", "process-1", "IMPLEMENTS"),
        GraphEdge("policy-1", "process-1", "PART_OF"),
        GraphEdge("doc-1", "doc-2", "RELATED_TO"),
        GraphEdge("doc-3", "term-1", "DEFINES"),
        GraphEdge("person-1", "role-1", "PARTICIPATES_IN"),
    ]

    return nodes, edges
