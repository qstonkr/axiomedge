"""검색 분석

4 탭: 검색 품질, 검색 어댑터, 보안, KB 권한
실 데이터 기반 — API가 반환하지 않는 필드를 표시하지 않음.

Created: 2026-02-20
Updated: 2026-03-12 — 실 데이터 기반 재작성 (가상 메트릭 제거)
"""

import streamlit as st

st.set_page_config(page_title="검색 분석", page_icon="🔍", layout="wide")


import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("🔍 검색 분석")

tab_quality, tab_adapter, tab_security, tab_abac = st.tabs(
    ["검색 품질", "검색 어댑터", "보안", "KB 권한"]
)


# =============================================================================
# 탭 1: 검색 품질
# =============================================================================
with tab_quality:
    analytics_result = api_client.get_search_analytics()

    if api_failed(analytics_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_quality"):
            st.cache_data.clear()
            st.rerun()
    else:
        total_queries = analytics_result.get("total_queries", 0)
        avg_latency_ms = analytics_result.get("avg_latency_ms", 0)
        cache_hit_rate = analytics_result.get("cache_hit_rate", 0)
        top_queries = analytics_result.get("top_queries", [])

        # 핵심 메트릭
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("총 검색 수", f"{total_queries:,}건")
        with col2:
            st.metric("평균 응답 시간", f"{avg_latency_ms:,.0f}ms")
        with col3:
            st.metric("캐시 히트율", f"{cache_hit_rate:.1f}%")

        st.markdown("---")

        # 상위 검색어 차트
        st.subheader("상위 검색어 TOP 10")
        if top_queries:
            queries = [q.get("query", "") for q in top_queries]
            counts = [q.get("count", 0) for q in top_queries]
            fig_top = px.bar(
                x=counts,
                y=queries,
                orientation="h",
                title="검색어별 빈도",
                labels={"x": "검색 횟수", "y": "검색어"},
                text=counts,
            )
            fig_top.update_layout(
                yaxis={"categoryorder": "total ascending"},
                margin=dict(l=20, r=20, t=40, b=20),
                height=400,
            )
            st.plotly_chart(fig_top, use_container_width=True)
        else:
            st.info("검색 이력이 없습니다.")

        # 응답 시간 게이지
        st.subheader("평균 응답 시간")
        fig_latency = go.Figure(go.Indicator(
            mode="gauge+number",
            value=avg_latency_ms,
            number={"suffix": "ms"},
            title={"text": "평균 레이턴시"},
            gauge={
                "axis": {"range": [0, 30000]},
                "bar": {"color": "#2196F3"},
                "steps": [
                    {"range": [0, 5000], "color": "#C8E6C9"},
                    {"range": [5000, 15000], "color": "#FFF9C4"},
                    {"range": [15000, 30000], "color": "#FFCDD2"},
                ],
                "threshold": {
                    "line": {"color": "red", "width": 2},
                    "thickness": 0.75,
                    "value": 15000,
                },
            },
        ))
        fig_latency.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=250)
        st.plotly_chart(fig_latency, use_container_width=True)


# =============================================================================
# 탭 2: 검색 어댑터
# =============================================================================
with tab_adapter:
    adapter_result = api_client.get_search_adapter_stats()

    if api_failed(adapter_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_adapter"):
            st.cache_data.clear()
            st.rerun()
    else:
        adapters = adapter_result.get("adapters", {})

        st.subheader("활성 검색 어댑터")
        if adapters:
            rows = []
            for name, info in adapters.items():
                rows.append({
                    "어댑터": name,
                    "활성": "✅" if info.get("enabled") else "❌",
                    "타입": info.get("type", "-"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("활성 어댑터가 없습니다.")

        st.markdown("---")

        # Feature Flag 상태
        st.subheader("RAG Feature Flags")
        agentic_result = api_client.get_agentic_rag_stats()
        crag_result = api_client.get_crag_stats()

        if not api_failed(agentic_result) or not api_failed(crag_result):
            flag_rows = []
            if not api_failed(agentic_result):
                flag_rows.append({
                    "Feature": "Composite Rerank",
                    "상태": "✅ 활성" if agentic_result.get("composite_rerank_enabled") else "❌ 비활성",
                })
                flag_rows.append({
                    "Feature": "Source Quality",
                    "상태": "✅ 활성" if agentic_result.get("source_quality_enabled") else "❌ 비활성",
                })
            if not api_failed(crag_result):
                corrections_by_type = crag_result.get("corrections_by_type", {})
                flag_rows.append({
                    "Feature": "CRAG Self-Correction",
                    "상태": "✅ 활성" if corrections_by_type.get("self_correction_enabled") else "❌ 비활성",
                })
                flag_rows.append({
                    "Feature": "Inline Quality Gate",
                    "상태": "✅ 활성" if corrections_by_type.get("quality_gate_enabled") else "❌ 비활성",
                })
            st.dataframe(pd.DataFrame(flag_rows), use_container_width=True, hide_index=True)


# =============================================================================
# 탭 3: 보안
# =============================================================================
with tab_security:
    injection_result = api_client.get_search_injection_stats()

    if api_failed(injection_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_security"):
            st.cache_data.clear()
            st.rerun()
    else:
        total_blocked = injection_result.get("total_blocked", 0)
        by_pattern = injection_result.get("by_pattern", {})

        st.subheader("프롬프트 인젝션 필터 차단 통계")
        st.metric("총 차단 수", f"{total_blocked:,}건")

        if by_pattern:
            st.markdown("---")
            st.subheader("패턴별 차단 수")
            fig_patterns = px.bar(
                x=list(by_pattern.keys()),
                y=list(by_pattern.values()),
                title="정규식 패턴별 차단 수",
                labels={"x": "패턴", "y": "차단 수"},
            )
            fig_patterns.update_layout(
                xaxis_tickangle=-45,
                margin=dict(l=20, r=20, t=40, b=80),
            )
            st.plotly_chart(fig_patterns, use_container_width=True)
        else:
            st.info("차단 이력이 없습니다.")


# =============================================================================
# 탭 4: KB 권한
# =============================================================================
with tab_abac:
    st.subheader("ABAC 접근 제어 대시보드")
    st.caption("TenantContext 기반 KB 접근 패턴")

    # Tier별 접근 정책 테이블
    st.markdown("#### Tier별 접근 정책")
    tier_policies = [
        {"Tier": "GLOBAL", "접근 권한": "전체 읽기", "예시 KB": "Infra KB, MISO KB"},
        {"Tier": "BU", "접근 권한": "organization_id 일치", "예시 KB": "CVS KB, SM KB, HS KB"},
        {"Tier": "TEAM", "접근 권한": "department_id 일치 or 명시적 공유", "예시 KB": "팀별 비공개 KB"},
    ]
    st.dataframe(pd.DataFrame(tier_policies), use_container_width=True, hide_index=True)

    st.markdown("---")

    # KB별 접근 패턴 (searchable kbs 기반)
    st.markdown("#### 검색 가능 KB 목록")
    kbs_result = api_client.get_searchable_kbs()
    if api_failed(kbs_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_abac"):
            st.cache_data.clear()
            st.rerun()
    else:
        kbs_list = kbs_result.get("kbs", kbs_result.get("items", []))
        if kbs_list:
            rows = []
            for kb in kbs_list:
                rows.append({
                    "KB 이름": kb.get("name", "-"),
                    "KB ID": kb.get("kb_id", kb.get("id", "-")),
                    "Tier": kb.get("tier", "-"),
                    "접근 제한": kb.get("access_restriction", kb.get("access", "OPEN")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("검색 가능한 KB가 없습니다.")
