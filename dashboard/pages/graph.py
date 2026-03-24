"""지식 그래프 탐색

인터랙티브 그래프 탐색 + 지식 건강 대시보드.
8개 뷰 모드: 검색, 전문가, 경로, 영향도, 지식 건강, 이력, 커뮤니티, 시스템 의존성.

Created: 2026-02-20
Updated: 2026-03-02 - 인터랙티브 탐색 + 지식 건강 대시보드 전면 개편
Updated: 2026-03-14 - v2 UX (WCAG colors, hover-highlight, Korean labels, export)
"""

import streamlit as st

st.set_page_config(page_title="지식 그래프", page_icon="🕸️", layout="wide")


import re

import plotly.express as px
import plotly.graph_objects as go

from components.sidebar import render_sidebar
from components.graph_viz import (
    render_knowledge_graph,
    GraphNode,
    GraphEdge,
    NodeType,
    NODE_COLORS,
)
from components.graph_viz_v2 import (
    render_knowledge_graph_v2,
    NODE_COLORS_V2,
)
from components.graph_labels import (
    format_rel_for_filter,
    format_rel_label,
    sanitize_label,
    NODE_TYPE_LABELS_KO,
)
from services import api_client, config as cfg
from services.api_client import api_failed
from services.neo4j_service import NODE_TYPES, ALL_RELATION_TYPES

render_sidebar(show_admin=True)

st.title("🕸️ 지식 그래프")

# ---------------------------------------------------------------------------
# Session State 초기화 (인터랙티브 탐색용)
# ---------------------------------------------------------------------------
if "graph_nodes" not in st.session_state:
    st.session_state.graph_nodes = {}   # {node_id: GraphNode}
if "graph_edges" not in st.session_state:
    st.session_state.graph_edges = {}   # {(src, tgt, rel): GraphEdge}
if "expanded_ids" not in st.session_state:
    st.session_state.expanded_ids = set()
if "selected_node_id" not in st.session_state:
    st.session_state.selected_node_id = None

# ---------------------------------------------------------------------------
# 사이드바 필터
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    st.subheader("그래프 필터")

    # v1/v2 toggle
    graph_version = st.toggle("v2 그래프 (UX 개선)", value=True, key="graph_v2")

    # 뷰 모드 (4개 → 8개)
    view_mode = st.radio(
        "보기 모드",
        [
            "검색 그래프",
            "전문가 찾기",
            "경로 탐색",
            "영향도 분석",
            "지식 건강",
            "이력 탐색",
            "커뮤니티 탐색",
            "시스템 의존성",
        ],
        key="graph_view_mode",
    )

    # 노드/관계 필터는 그래프 뷰에서만 표시
    if view_mode not in ("전문가 찾기", "지식 건강"):
        # 노드 유형 필터
        selected_node_types = st.multiselect(
            "노드 유형 필터",
            options=sorted(NODE_TYPES),
            default=sorted(NODE_TYPES),
            key="node_type_filter",
        )

        # 관계 유형 필터
        if graph_version:
            # v2: compact multiselect with Korean labels
            rel_display_map = {
                format_rel_for_filter(r): r for r in sorted(ALL_RELATION_TYPES)
            }
            selected_display = st.multiselect(
                "관계 유형",
                list(rel_display_map.keys()),
                default=list(rel_display_map.keys()),
                key="rel_v2",
            )
            selected_rels = [rel_display_map[d] for d in selected_display]
        else:
            # v1: checkbox list
            st.markdown("**관계 유형**")
            all_rels = sorted(ALL_RELATION_TYPES)
            select_all = st.checkbox("전체 선택", value=True, key="rel_select_all")
            if select_all:
                selected_rels = all_rels
            else:
                selected_rels = []
                for rel in all_rels:
                    if st.checkbox(rel, value=True, key=f"rel_{rel}"):
                        selected_rels.append(rel)

        # 최대 노드 수
        max_nodes = st.slider("최대 노드 수", 10, 200, 50, key="max_nodes")
    else:
        selected_node_types = sorted(NODE_TYPES)
        selected_rels = sorted(ALL_RELATION_TYPES)
        max_nodes = 50

# Resolve render function and color map based on version toggle
if graph_version:
    _render_graph = render_knowledge_graph_v2
    _NODE_COLORS = NODE_COLORS_V2
else:
    _render_graph = render_knowledge_graph
    _NODE_COLORS = NODE_COLORS


# ---------------------------------------------------------------------------
# 그래프 통계
# ---------------------------------------------------------------------------
stats_result = api_client.get_graph_stats()

if api_failed(stats_result):
    st.warning("그래프 통계 API에 연결할 수 없습니다. 그래프 시각화만 표시합니다.")
    graph_stats = {}
else:
    graph_stats = stats_result

# 통계 메트릭
if graph_stats:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("총 노드", f"{graph_stats.get('total_nodes', 0):,}개")
    with col2:
        st.metric("총 엣지", f"{graph_stats.get('total_edges', 0):,}개")
    with col3:
        node_counts = graph_stats.get("node_types", graph_stats.get("node_counts", {}))
        st.metric("노드 유형", f"{len(node_counts)}종")
    with col4:
        edge_counts = graph_stats.get("edge_types", graph_stats.get("edge_counts", {}))
        st.metric("관계 유형", f"{len(edge_counts)}종")

    st.markdown("---")

    # NODE_COLORS에서 노드 유형명 → 색상 매핑 생성
    _node_color_map = {nt.value: color for nt, color in _NODE_COLORS.items()}

    # 노드 유형별 분포 차트
    if node_counts:
        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
            node_names = list(node_counts.keys())
            node_bar_colors = [_node_color_map.get(n, "#888888") for n in node_names]
            fig_nodes = go.Figure(go.Bar(
                x=node_names,
                y=list(node_counts.values()),
                marker_color=node_bar_colors,
            ))
            fig_nodes.update_layout(
                title="노드 유형별 분포",
                xaxis_title="노드 유형",
                yaxis_title="개수",
                showlegend=False,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_nodes, use_container_width=True)

        with col_chart2:
            if edge_counts:
                fig_edges = px.bar(
                    x=list(edge_counts.keys()),
                    y=list(edge_counts.values()),
                    title="관계 유형별 분포",
                    labels={"x": "관계 유형", "y": "개수"},
                    color=list(edge_counts.keys()),
                )
                fig_edges.update_layout(showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_edges, use_container_width=True)

st.markdown("---")


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _node_type_enum(name: str) -> NodeType:
    """문자열 노드 유형을 NodeType enum으로 변환."""
    try:
        return NodeType(name)
    except ValueError:
        return NodeType.DOCUMENT


def _api_nodes_to_viz(raw_nodes: list[dict], raw_edges: list[dict]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """API 결과를 graph_viz 컴포넌트용으로 변환."""
    nodes = []
    for n in raw_nodes[:max_nodes]:
        ntype = n.get("node_type", n.get("type", "Document"))
        if ntype not in selected_node_types:
            continue
        nodes.append(GraphNode(
            id=n.get("id", n.get("node_id", "")),
            label=n.get("label", n.get("name", "")),
            node_type=_node_type_enum(ntype),
            properties=n.get("properties"),
        ))
    node_ids = {n.id for n in nodes}
    edges = []
    for e in raw_edges:
        rel = e.get("relation_type", e.get("type", "RELATED_TO"))
        if rel not in selected_rels:
            continue
        src = e.get("source", e.get("from", ""))
        tgt = e.get("target", e.get("to", ""))
        if src in node_ids and tgt in node_ids:
            edges.append(GraphEdge(source=src, target=tgt, relation_type=rel, properties=e.get("properties")))
    return nodes, edges


def _merge_to_session(raw_nodes: list[dict], raw_edges: list[dict]) -> None:
    """API 결과를 세션 그래프에 병합 (누적 성장)."""
    for n in raw_nodes:
        nid = n.get("id", n.get("node_id", ""))
        if nid and nid not in st.session_state.graph_nodes:
            ntype = n.get("node_type", n.get("type", "Document"))
            st.session_state.graph_nodes[nid] = GraphNode(
                id=nid,
                label=n.get("label", n.get("name", "")),
                node_type=_node_type_enum(ntype),
                properties=n.get("properties"),
            )
    node_ids = set(st.session_state.graph_nodes.keys())
    for e in raw_edges:
        src = e.get("source", e.get("from", ""))
        tgt = e.get("target", e.get("to", ""))
        if src not in node_ids or tgt not in node_ids:
            continue
        rel = e.get("relation_type", e.get("type", "RELATED_TO"))
        key = (src, tgt, rel)
        if key not in st.session_state.graph_edges:
            st.session_state.graph_edges[key] = GraphEdge(
                source=src, target=tgt, relation_type=rel, properties=e.get("properties"),
            )


def _render_session_graph(
    height: int = 600,
    on_dblclick_expand: bool = False,
    search_match_ids: set[str] | None = None,
) -> None:
    """세션에 누적된 그래프를 렌더링."""
    _is_v2 = st.session_state.get("graph_v2", True)
    all_nodes = list(st.session_state.graph_nodes.values())
    node_ids = set(st.session_state.graph_nodes.keys())
    all_edges = [
        e for e in st.session_state.graph_edges.values()
        if e.source in node_ids and e.target in node_ids
    ]
    if not all_nodes:
        st.info("그래프가 비어 있습니다. 검색 후 노드를 확장해보세요.")
        return

    render_fn = render_knowledge_graph_v2 if _is_v2 else render_knowledge_graph
    kwargs: dict = dict(
        height=height,
        expanded_ids=st.session_state.expanded_ids,
        selected_id=st.session_state.selected_node_id,
        on_dblclick_expand=on_dblclick_expand,
        api_base_url=cfg.DASHBOARD_API_URL if on_dblclick_expand else "",
    )
    if _is_v2 and search_match_ids is not None:
        kwargs["search_match_ids"] = search_match_ids

    render_fn(all_nodes, all_edges, **kwargs)


def _render_table_view() -> None:
    """Accessible table view toggle (G15, WCAG)."""
    import pandas as pd

    with st.expander("테이블 뷰 (접근성)", expanded=False):
        node_rows = [
            {"ID": n.id, "이름": n.label, "유형": n.node_type.value}
            for n in st.session_state.graph_nodes.values()
        ]
        edge_rows = [
            {
                "출발": e.source,
                "도착": e.target,
                "관계": format_rel_label(e.relation_type),
            }
            for e in st.session_state.graph_edges.values()
        ]
        tab_nodes, tab_edges = st.tabs(["노드", "관계"])
        with tab_nodes:
            if node_rows:
                st.dataframe(pd.DataFrame(node_rows), use_container_width=True)
            else:
                st.caption("노드가 없습니다.")
        with tab_edges:
            if edge_rows:
                st.dataframe(pd.DataFrame(edge_rows), use_container_width=True)
            else:
                st.caption("관계가 없습니다.")


def _render_reset_button(key: str) -> None:
    """Graph reset with confirmation (v2: popover, v1: direct button)."""
    if st.session_state.get("graph_v2", True):
        with st.popover("그래프 초기화"):
            n = len(st.session_state.graph_nodes)
            st.warning(f"탐색한 {n}개 노드가 모두 삭제됩니다.")
            if st.button("초기화 확인", type="primary", key=f"confirm_{key}"):
                _do_reset()
    else:
        if st.button("그래프 초기화", key=key):
            _do_reset()


def _do_reset() -> None:
    """Clear session graph state and rerun."""
    st.session_state.graph_nodes = {}
    st.session_state.graph_edges = {}
    st.session_state.expanded_ids = set()
    st.session_state.selected_node_id = None
    st.session_state.graph_search = ""
    st.rerun()


def _get_node_options() -> dict[str, str]:
    """Build selectbox options from session nodes. Returns {display_label: node_id}.

    Appends truncated node ID on collision to guarantee unique keys.
    """
    options: dict[str, str] = {}
    for nid, node in st.session_state.graph_nodes.items():
        clean, _ = sanitize_label(node.label, node.node_type.value)
        type_ko = NODE_TYPE_LABELS_KO.get(node.node_type.value, node.node_type.value)
        display = f"{clean} ({type_ko})"
        if display in options:
            display = f"{display} [{nid[:12]}]"
        options[display] = nid
    return options


# query param fallback (직접 URL 접근 시)
_expand_node_id = st.query_params.get("expand_node")
if _expand_node_id:
    st.query_params.pop("expand_node")
    if _expand_node_id not in st.session_state.expanded_ids:
        expand_result = api_client.graph_expand(_expand_node_id, max_neighbors=30)
        if not api_failed(expand_result):
            all_nodes = expand_result.get("neighbors", [])
            center = expand_result.get("center_node")
            if center:
                all_nodes = [center] + all_nodes
            _merge_to_session(all_nodes, expand_result.get("edges", []))
            st.session_state.expanded_ids.add(_expand_node_id)
    st.rerun()


# ---------------------------------------------------------------------------
# 뷰 모드별 렌더링
# ---------------------------------------------------------------------------

if view_mode == "검색 그래프":
    st.subheader("그래프 검색")

    # ── Search history buttons (v2) ──
    if graph_version and st.session_state.get("search_history"):
        st.caption("최근 검색:")
        history = st.session_state.search_history[-5:]
        cols = st.columns(min(len(history), 5))
        for i, term in enumerate(history):
            with cols[i]:
                if st.button(term, key=f"recent_{i}"):
                    st.session_state.graph_search = term
                    st.rerun()

    search_query = st.text_input("노드 검색", placeholder="예: K8s, 방송시스템, 김철수...", key="graph_search")

    if search_query:
        # Save to search history (v2)
        if graph_version:
            hist = st.session_state.get("search_history", [])
            if search_query not in hist:
                hist.append(search_query)
                st.session_state.search_history = hist[-5:]

        with st.spinner("그래프 검색 중..."):
            search_result = api_client.graph_search(
                search_query,
                max_nodes=max_nodes,
                node_types=selected_node_types if selected_node_types != sorted(NODE_TYPES) else None,
            )
            if api_failed(search_result):
                st.error("API 연결 실패 — Neo4j 환경변수(NEO4J_URI)를 확인하세요.")
                if st.button("재시도", key="retry_graph_search"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                raw_nodes = search_result.get("nodes", [])
                raw_edges = search_result.get("edges", [])
                if raw_nodes:
                    _merge_to_session(raw_nodes, raw_edges)

                    # v2: search match glow + enhanced guidance
                    search_match_ids_set = None
                    if graph_version:
                        search_match_ids_set = {
                            n.get("id", n.get("node_id", "")) for n in raw_nodes
                        }
                        st.info("노드를 더블클릭하면 이웃 노드가 확장됩니다. 호버하면 연결 관계가 하이라이트됩니다.")
                    else:
                        st.caption("노드를 더블클릭하면 이웃 노드가 자동으로 확장됩니다.")

                    _render_session_graph(
                        height=600,
                        on_dblclick_expand=True,
                        search_match_ids=search_match_ids_set,
                    )

                    # ── Node detail panel (v2, G4) ──
                    if graph_version:
                        with st.expander("노드 상세 정보", expanded=False):
                            if st.session_state.get("selected_node_id"):
                                node = st.session_state.graph_nodes.get(
                                    st.session_state.selected_node_id
                                )
                                if node:
                                    col_info, col_props = st.columns([1, 2])
                                    with col_info:
                                        st.write(f"**이름**: {node.label}")
                                        st.write(f"**유형**: {node.node_type.value}")
                                        st.write(f"**ID**: `{node.id}`")
                                    with col_props:
                                        if node.properties:
                                            st.json(node.properties)
                                        else:
                                            st.caption("추가 속성 없음")
                            else:
                                st.caption("그래프에서 노드를 클릭하면 상세 정보가 표시됩니다.")

                    # ── Table view (v2, G15 WCAG) ──
                    if graph_version and st.session_state.graph_nodes:
                        _render_table_view()

                    # ── Reset ──
                    st.markdown("---")
                    _render_reset_button("reset_graph")
                else:
                    st.info("검색 결과가 없습니다. Neo4j에 데이터가 적재되었는지 확인하세요.")
    else:
        # 세션에 이미 데이터가 있으면 표시
        if st.session_state.graph_nodes:
            if graph_version:
                st.info("노드를 더블클릭하면 이웃 노드가 확장됩니다. 호버하면 연결 관계가 하이라이트됩니다.")
            else:
                st.caption("노드를 더블클릭하면 이웃 노드가 자동으로 확장됩니다.")
            _render_session_graph(height=600, on_dblclick_expand=True)

            if graph_version and st.session_state.graph_nodes:
                _render_table_view()

            st.markdown("---")
            _render_reset_button("reset_graph_empty")
        else:
            st.info("검색어를 입력하면 관련 그래프가 표시됩니다.")


elif view_mode == "전문가 찾기":
    st.subheader("전문가 찾기")
    st.caption("주제 키워드와 관련된 문서를 소유/관리하는 전문가를 찾습니다.")

    topic = st.text_input("주제 키워드", placeholder="예: K8s, 배포, CI/CD, DM...", key="expert_topic")
    expert_limit = st.slider("최대 결과 수", 5, 30, 10, key="expert_limit")

    if topic:
        with st.spinner("전문가 검색 중..."):
            result = api_client.graph_experts(topic, limit=expert_limit)
            if api_failed(result):
                st.error("API 연결 실패")
            else:
                experts = result.get("experts", [])
                if experts:
                    for i, exp in enumerate(experts):
                        col_rank, col_name, col_team, col_docs, col_bar = st.columns([0.5, 2, 1.5, 1, 2])
                        with col_rank:
                            st.write(f"**#{i+1}**")
                        with col_name:
                            st.write(f"**{exp.get('name', '')}**")
                        with col_team:
                            st.write(exp.get("team", "-"))
                        with col_docs:
                            st.write(f"{exp.get('doc_count', 0)}건")
                        with col_bar:
                            score = exp.get("expertise_score", 0)
                            st.progress(score, text=f"{score:.0%}")
                else:
                    st.info(f"'{topic}' 관련 전문가를 찾을 수 없습니다.")
    else:
        st.info("주제 키워드를 입력하면 관련 전문가가 표시됩니다.")


elif view_mode == "경로 탐색":
    st.subheader("경로 탐색")
    st.caption("두 노드 간 최단 경로를 찾아 시각화합니다.")

    if graph_version and st.session_state.graph_nodes:
        node_opts = _get_node_options()
        opt_keys = ["(선택하세요)"] + sorted(node_opts.keys())
        col_from, col_to = st.columns(2)
        with col_from:
            sel_from = st.selectbox("시작 노드", opt_keys, key="path_from_v2")
            from_id = node_opts.get(sel_from, "")
        with col_to:
            sel_to = st.selectbox("도착 노드", opt_keys, key="path_to_v2")
            to_id = node_opts.get(sel_to, "")
    elif graph_version:
        st.info("먼저 '검색 그래프'에서 노드를 검색하세요. 검색된 노드가 여기에 자동으로 표시됩니다.")
        from_id, to_id = "", ""
    else:
        col_from, col_to = st.columns(2)
        with col_from:
            from_id = st.text_input("시작 노드 ID", placeholder="예: person-김철수", key="path_from")
        with col_to:
            to_id = st.text_input("도착 노드 ID", placeholder="예: sys-주문결제", key="path_to")

    if from_id and to_id:
        with st.spinner("경로 탐색 중..."):
            result = api_client.graph_path(from_id, to_id)
            if api_failed(result):
                st.error("API 연결 실패")
            else:
                path_nodes = result.get("nodes", [])
                path_edges = result.get("edges", [])
                distance = result.get("distance", 0)
                if path_nodes:
                    st.success(f"경로 발견: {distance} hop")
                    viz_nodes, viz_edges = _api_nodes_to_viz(path_nodes, path_edges)
                    path_ids = {n.id for n in viz_nodes}
                    _render_graph(
                        viz_nodes, viz_edges, height=500,
                        path_node_ids=path_ids,
                    )
                else:
                    st.warning("두 노드 간 경로를 찾을 수 없습니다 (5 hop 이내).")
    elif not graph_version:
        st.info("시작/도착 노드 ID를 입력하면 최단 경로가 표시됩니다.")


elif view_mode == "영향도 분석":
    st.subheader("영향도 분석")
    st.caption("대상 노드에서 N-hop 범위 내 영향을 받는 모든 엔티티를 분석합니다.")

    if graph_version and st.session_state.graph_nodes:
        node_opts = _get_node_options()
        opt_keys = ["(선택하세요)"] + sorted(node_opts.keys())
        sel_impact = st.selectbox("대상 노드", opt_keys, key="impact_node_v2")
        impact_node = node_opts.get(sel_impact, "")
    elif graph_version:
        st.info("먼저 '검색 그래프'에서 노드를 검색하세요. 검색된 노드가 여기에 자동으로 표시됩니다.")
        impact_node = ""
    else:
        impact_node = st.text_input("대상 노드 ID", placeholder="예: sys-주문결제시스템", key="impact_node")

    impact_hops = st.slider("탐색 깊이 (hops)", 1, 3, 2, key="impact_hops")

    if impact_node:
        with st.spinner("영향도 분석 중..."):
            result = api_client.graph_impact(impact_node, max_hops=impact_hops)
            if api_failed(result):
                st.error("API 연결 실패")
            else:
                impacted = result.get("impacted_nodes", [])
                impact_edges = result.get("edges", [])
                summary = result.get("summary", {})

                if impacted:
                    by_type = summary.get("by_type", {})
                    if by_type:
                        cols = st.columns(min(len(by_type), 5))
                        for i, (ntype, cnt) in enumerate(by_type.items()):
                            with cols[i % len(cols)]:
                                st.metric(ntype, f"{cnt}개")

                    center = result.get("center_node")
                    all_raw = impacted[:]
                    if center:
                        all_raw.insert(0, center)
                    viz_nodes, viz_edges = _api_nodes_to_viz(all_raw, impact_edges)
                    impact_ids = {n.id for n in viz_nodes}
                    selected = center.get("id", "") if isinstance(center, dict) else ""
                    _render_graph(
                        viz_nodes, viz_edges, height=500,
                        highlight_ids=impact_ids,
                        selected_id=selected,
                    )
                else:
                    st.info("영향 범위 내 노드가 없습니다.")
    elif not graph_version:
        st.info("대상 노드 ID를 입력하면 영향도 분석 결과가 표시됩니다.")


elif view_mode == "지식 건강":
    st.subheader("지식 건강 대시보드")
    st.caption("조직 지식의 건강 상태를 진단합니다.")

    with st.spinner("지식 건강 분석 중..."):
        health = api_client.graph_health()

    if api_failed(health):
        st.error("API 연결 실패")
    else:
        # --- 고아 노드 ---
        orphans = health.get("orphan_nodes", [])
        with st.expander(f"🔴 고아 노드 ({len(orphans)}개) — 연결 없는 고립 지식", expanded=bool(orphans)):
            if orphans:
                for o in orphans[:20]:
                    st.write(f"- **{o.get('label', '')}** ({o.get('node_type', '')})")
            else:
                st.success("고아 노드가 없습니다.")

        # --- 허브 노드 ---
        hubs = health.get("hub_nodes", [])
        with st.expander(f"⭐ 허브 노드 Top {len(hubs)} — 가장 중요한 지식 자산", expanded=bool(hubs)):
            if hubs:
                for h in hubs:
                    degree = h.get("degree", 0)
                    st.write(f"- **{h.get('label', '')}** ({h.get('node_type', '')}) — {degree}개 연결")
            else:
                st.info("허브 노드 데이터가 없습니다.")

        # --- 지식 사일로 ---
        silos = health.get("silos", [])
        with st.expander(f"⚠️ 지식 사일로 ({len(silos)}건) — 1명만 아는 지식", expanded=bool(silos)):
            if silos:
                for s in silos:
                    risk = s.get("risk_level", "medium")
                    risk_icon = "🔴" if risk == "high" else "🟡"
                    docs = s.get("exclusive_docs", [])
                    st.write(f"{risk_icon} **{s.get('person', '')}** — 단독 문서 {len(docs)}건")
                    if docs:
                        for d in docs[:5]:
                            st.write(f"  - {d}")
            else:
                st.success("지식 사일로가 없습니다.")

        # --- 팀별 커버리지 ---
        coverages = health.get("team_coverage", [])
        with st.expander(f"📊 팀별 커버리지 ({len(coverages)}팀)", expanded=bool(coverages)):
            if coverages:
                for tc in coverages:
                    systems = tc.get("systems", [])
                    documented = tc.get("documented", 0)
                    st.write(f"- **{tc.get('team', '')}**: 시스템 {len(systems)}개, 문서화된 항목 {documented}건")
            else:
                st.info("팀 커버리지 데이터가 없습니다.")

        # --- 오래된 문서 ---
        stale = health.get("stale_docs", [])
        with st.expander(f"🕐 오래된 문서 ({len(stale)}건)", expanded=bool(stale)):
            if stale:
                for sd in stale:
                    connected = sd.get("connected_systems", [])
                    sys_str = f" → {', '.join(connected)}" if connected else ""
                    st.write(f"- **{sd.get('title', '')}** (최종 수정: {sd.get('last_updated', 'N/A')}){sys_str}")
            else:
                st.success("오래된 문서가 없습니다.")


elif view_mode == "이력 탐색":
    st.subheader("이력 탐색")
    st.caption("노드의 현재 관계와 과거(WAS_*, PREVIOUSLY_*) 관계를 분리하여 보여줍니다.")

    if graph_version and st.session_state.graph_nodes:
        node_opts = _get_node_options()
        opt_keys = ["(선택하세요)"] + sorted(node_opts.keys())
        sel_timeline = st.selectbox("대상 노드", opt_keys, key="timeline_node_v2")
        timeline_node = node_opts.get(sel_timeline, "")
    elif graph_version:
        st.info("먼저 '검색 그래프'에서 노드를 검색하세요.")
        timeline_node = ""
    else:
        timeline_node = st.text_input("노드 ID", placeholder="예: person-김철수", key="timeline_node")

    if timeline_node:
        with st.spinner("이력 조회 중..."):
            result = api_client.graph_timeline(timeline_node)
            if api_failed(result):
                st.error("API 연결 실패")
            else:
                node_info = result.get("node")
                current = result.get("current_relations", [])
                history = result.get("history_relations", [])

                if node_info:
                    st.write(f"**대상**: {node_info.get('label', '')} ({node_info.get('node_type', '')})")

                col_cur, col_hist = st.columns(2)
                with col_cur:
                    st.markdown("**현재 관계**")
                    if current:
                        for r in current:
                            st.write(f"- {r.get('relation_type', '')} → {r.get('target', r.get('source', ''))}")
                    else:
                        st.info("현재 관계가 없습니다.")

                with col_hist:
                    st.markdown("**과거 관계**")
                    if history:
                        for r in history:
                            st.write(f"- ~~{r.get('relation_type', '')}~~ → {r.get('target', r.get('source', ''))}")
                    else:
                        st.info("과거 관계가 없습니다.")
    else:
        st.info("노드 ID를 입력하면 현재/과거 관계가 분리되어 표시됩니다.")


elif view_mode == "커뮤니티 탐색":
    st.subheader("커뮤니티 클러스터")
    communities_result = api_client.get_graph_communities()

    if api_failed(communities_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_communities"):
            st.cache_data.clear()
            st.rerun()
    else:
        communities = communities_result.get("communities", [])
        if communities:
            for i, comm in enumerate(communities):
                comm_name = comm.get("name", f"커뮤니티 {i + 1}")
                comm_size = comm.get("size", comm.get("node_count", 0))
                with st.expander(f"{comm_name} ({comm_size}개 노드)", expanded=(i == 0)):
                    comm_nodes = comm.get("nodes", [])
                    comm_edges = comm.get("edges", [])
                    if comm_nodes:
                        viz_nodes, viz_edges = _api_nodes_to_viz(comm_nodes, comm_edges)
                        if viz_nodes:
                            _render_graph(viz_nodes, viz_edges, height=400)
                    members = comm.get("members", [])
                    if members:
                        st.write("**주요 노드:**", ", ".join(str(m) for m in members[:10]))
        else:
            st.info("커뮤니티 데이터가 없습니다.")


elif view_mode == "시스템 의존성":
    st.subheader("시스템 의존성 그래프")
    st.caption("시스템(System) 노드 간 의존 관계를 시각화합니다.")

    if graph_stats:
        node_counts = graph_stats.get("node_types", graph_stats.get("node_counts", {}))
        system_count = node_counts.get("System", 0)
        st.info(f"등록된 시스템 노드: {system_count}개")

    search_sys = st.text_input("시스템 이름 검색", placeholder="예: 주문결제, 방송, DM...", key="sys_search")
    if search_sys:
        with st.spinner("시스템 의존성 검색 중..."):
            result = api_client.graph_search(
                search_sys,
                max_nodes=max_nodes,
                node_types=["System", "Document", "Process"],
            )
            if not api_failed(result):
                raw_nodes = result.get("nodes", [])
                raw_edges = result.get("edges", [])
                if raw_nodes:
                    viz_nodes, viz_edges = _api_nodes_to_viz(raw_nodes, raw_edges)
                    _render_graph(viz_nodes, viz_edges, height=500)
                else:
                    st.info("관련 시스템을 찾을 수 없습니다.")
            else:
                st.error("API 연결 실패")
                if st.button("🔄 재시도", key="retry_sys"):
                    st.cache_data.clear()
                    st.rerun()
    else:
        st.info("시스템 이름을 입력하면 의존성 그래프가 표시됩니다.")


# ---------------------------------------------------------------------------
# 범례
# ---------------------------------------------------------------------------
st.markdown("---")
with st.expander("범례: 노드 유형별 색상", expanded=graph_version):
    legend_cols = st.columns(5)
    _HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,6}$")
    for i, nt in enumerate(NodeType):
        with legend_cols[i % 5]:
            color = _NODE_COLORS.get(nt, "#888")
            safe_color = color if _HEX_COLOR_RE.match(color) else "#888"
            safe_label = str(nt.value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # v2: show count per type from current graph
            count_str = ""
            if graph_version and st.session_state.graph_nodes:
                n = sum(
                    1 for nd in st.session_state.graph_nodes.values()
                    if nd.node_type == nt
                )
                count_str = f" ({n})" if n > 0 else ""
            st.markdown(
                f'<span style="color:{safe_color}; font-weight:bold;">●</span> '
                f'{safe_label}{count_str}',
                unsafe_allow_html=True,
            )

st.caption("Neo4j 기반 지식 그래프 | 데이터: oreo-agents GraphRAG Extractor")
