"""지식 갭 분석

KB별 카테고리 커버리지 히트맵 및 갭 영역 표시.

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="지식 갭", page_icon="📉", layout="wide")


import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("📉 지식 갭")
st.caption("KB별 카테고리 커버리지를 분석하고 부족한 영역을 식별합니다.")


# ---------------------------------------------------------------------------
# KB 선택
# ---------------------------------------------------------------------------
kbs_result = api_client.list_kbs()

if api_failed(kbs_result):
    st.error("API 연결 실패")
    if st.button("🔄 재시도", key="retry_kbs"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
if not kb_items:
    st.info("등록된 KB가 없습니다.")
    st.stop()

kb_options = {kb.get("name", kb.get("kb_id", "")): kb.get("kb_id", kb.get("id", "")) for kb in kb_items}
selected_kb_name = st.selectbox("KB 선택", options=list(kb_options.keys()), key="gap_kb_select")
selected_kb_id = kb_options[selected_kb_name]


# ---------------------------------------------------------------------------
# 갭 데이터 조회
# ---------------------------------------------------------------------------
gaps_result = api_client.get_kb_coverage_gaps(selected_kb_id)

if api_failed(gaps_result):
    st.error("API 연결 실패")
    if st.button("🔄 재시도", key="retry_gaps"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

gaps = gaps_result.get("gaps", gaps_result.get("coverage_gaps", []))
overall_coverage = gaps_result.get("overall_coverage", 0)
total_categories = gaps_result.get("total_categories", 0)
covered_categories = gaps_result.get("covered_categories", 0)


# ---------------------------------------------------------------------------
# 요약 메트릭
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(
        "전체 커버리지",
        f"{overall_coverage:.0%}" if isinstance(overall_coverage, float) else str(overall_coverage),
    )
with col2:
    st.metric("총 카테고리", f"{total_categories}개")
with col3:
    st.metric("커버 카테고리", f"{covered_categories}개")

st.markdown("---")


# ---------------------------------------------------------------------------
# 카테고리 커버리지 히트맵
# ---------------------------------------------------------------------------
st.subheader("카테고리 커버리지 히트맵")

if gaps:
    categories = [g.get("category", g.get("name", f"Cat-{i}")) for i, g in enumerate(gaps)]
    coverages = [g.get("coverage", g.get("coverage_score", 0)) for g in gaps]
    doc_counts = [g.get("document_count", g.get("doc_count", 0)) for g in gaps]
    severities = [g.get("severity", g.get("gap_severity", "LOW")) for g in gaps]

    # 히트맵 (1행 N열)
    import numpy as np

    coverage_matrix = [coverages]
    fig_hm = go.Figure(data=go.Heatmap(
        z=coverage_matrix,
        x=categories,
        y=["커버리지"],
        colorscale=[
            [0.0, "#F44336"],
            [0.3, "#FF9800"],
            [0.6, "#FFC107"],
            [0.8, "#8BC34A"],
            [1.0, "#4CAF50"],
        ],
        zmin=0,
        zmax=1,
        text=[[f"{v:.0%}" for v in coverages]],
        texttemplate="%{text}",
        hovertemplate="카테고리: %{x}<br>커버리지: %{z:.1%}<extra></extra>",
    ))
    fig_hm.update_layout(
        title=f"{selected_kb_name} 카테고리 커버리지",
        margin=dict(l=20, r=20, t=40, b=80),
        xaxis_tickangle=-45,
        height=200,
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    st.markdown("---")

    # ---------------------------------------------------------------------------
    # 갭 영역 상세
    # ---------------------------------------------------------------------------
    st.subheader("갭 영역 상세")

    severity_icons = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }

    # 낮은 커버리지 순 정렬
    sorted_gaps = sorted(gaps, key=lambda g: g.get("coverage", g.get("coverage_score", 0)))

    for gap in sorted_gaps:
        category = gap.get("category", gap.get("name", "-"))
        coverage = gap.get("coverage", gap.get("coverage_score", 0))
        severity = gap.get("severity", gap.get("gap_severity", "LOW"))
        doc_count = gap.get("document_count", gap.get("doc_count", 0))
        sev_icon = severity_icons.get(severity, "⚪")
        recommended_action = gap.get("recommended_action", gap.get("recommendation", ""))

        # 부족한 영역 강조
        is_insufficient = coverage < 0.5

        with st.container(border=True):
            gcol1, gcol2, gcol3, gcol4 = st.columns([3, 1, 1, 1])
            with gcol1:
                label = f"{'**' if is_insufficient else ''}{category}{'**' if is_insufficient else ''}"
                st.markdown(f"{sev_icon} {label}")
            with gcol2:
                st.metric("커버리지", f"{coverage:.0%}")
            with gcol3:
                st.metric("문서 수", f"{doc_count}개")
            with gcol4:
                st.metric("심각도", severity)

            if recommended_action:
                st.caption(f"권장 조치: {recommended_action}")

            # 부족 영역 경고
            if is_insufficient:
                st.warning(f"'{category}' 카테고리의 커버리지가 50% 미만입니다. 문서 보강이 필요합니다.")

    st.markdown("---")

    # ---------------------------------------------------------------------------
    # 갭 테이블 요약
    # ---------------------------------------------------------------------------
    st.subheader("갭 테이블 요약")
    rows = []
    for gap in sorted_gaps:
        rows.append({
            "카테고리": gap.get("category", gap.get("name", "-")),
            "커버리지": f"{gap.get('coverage', gap.get('coverage_score', 0)):.0%}",
            "문서 수": gap.get("document_count", gap.get("doc_count", 0)),
            "심각도": gap.get("severity", gap.get("gap_severity", "-")),
            "권장 조치": gap.get("recommended_action", gap.get("recommendation", "-")),
        })
    df_gaps = pd.DataFrame(rows)
    st.dataframe(df_gaps, use_container_width=True, hide_index=True)

else:
    st.success("모든 카테고리가 충분히 커버되어 있습니다.")


st.markdown("---")
st.caption("📌 커버리지 분석 기준: 카테고리별 문서 수 + 품질 점수 + 신선도 | SSOT: oreo-agents domain/knowledge/coverage/")
