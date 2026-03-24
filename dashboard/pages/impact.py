"""영향도 / 사용량 -- 사용량, 영향도, KnowledgeTier 통합 페이지

실 데이터 기반 — API가 반환하지 않는 필드를 표시하지 않음.

Created: 2026-02-20
Updated: 2026-03-12 — 실 데이터 기반 재작성 (가상 메트릭 제거)
"""

import streamlit as st

st.set_page_config(page_title="영향도 / 사용량", page_icon="📊", layout="wide")


import pandas as pd
import plotly.graph_objects as go

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("영향도 / 사용량")
st.caption("KB별 통계, 영향도 지표, Knowledge Tier 분포를 확인합니다.")

# ---------------------------------------------------------------------------
# 공통: KB 선택
# ---------------------------------------------------------------------------
kbs_result = api_client.list_kbs()
if api_failed(kbs_result):
    st.error("API 연결 실패")
    if st.button("재시도", key="retry_kbs"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

kb_items = kbs_result.get("items", [])
kb_options = {kb.get("name", kb.get("id", "")): kb.get("id", kb.get("kb_id", "")) for kb in kb_items}

if not kb_options:
    st.info("등록된 KB가 없습니다.")
    st.stop()

selected_kb_name = st.selectbox("KB 선택", list(kb_options.keys()), key="impact_kb")
kb_id = kb_options[selected_kb_name]

tab_usage, tab_impact, tab_tier = st.tabs(["KB 통계", "영향도", "KnowledgeTier"])

# ============================================================================
# 1) KB 통계
# ============================================================================
with tab_usage:
    # Per-KB stats: GET /{kb_id}/stats → KBPerStatsResponse
    per_stats = api_client.get_kb_stats(kb_id)
    if api_failed(per_stats):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_usage"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader(f"KB 통계: {selected_kb_name}")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("문서 수", f"{per_stats.get('document_count', 0):,}")
        with col2:
            st.metric("동기화 상태", per_stats.get("sync_status", "-"))
        with col3:
            last_synced = per_stats.get("last_synced_at")
            st.metric("최근 동기화", last_synced[:10] if last_synced else "-")


# ============================================================================
# 2) 영향도
# ============================================================================
with tab_impact:
    impact_data = api_client.get_kb_impact(kb_id)
    if api_failed(impact_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_impact"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("KB 영향도")

        # 핵심 메트릭
        col1, col2 = st.columns(2)
        with col1:
            st.metric("총 검색 수", f"{impact_data.get('total_queries', 0):,}건")
        with col2:
            st.metric("총 인용 수", f"{impact_data.get('total_citations', 0):,}건")

        # 상위 문서 (top_documents)
        st.markdown("---")
        top_documents = impact_data.get("top_documents", [])
        if top_documents:
            st.subheader("상위 인용 문서")
            df = pd.DataFrame(top_documents)
            display_cols = []
            col_map = {}
            for col, label in [
                ("title", "문서명"),
                ("doc_id", "문서 ID"),
                ("citation_count", "인용 수"),
            ]:
                if col in df.columns:
                    display_cols.append(col)
                    col_map[col] = label

            if display_cols:
                df_display = df[display_cols].rename(columns=col_map)
                st.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            st.info("인용 데이터가 없습니다.")

        # 영향도 랭킹 (별도 API)
        st.markdown("---")
        rankings_data = api_client.get_kb_impact_rankings(kb_id)
        if not api_failed(rankings_data):
            ranking_items = rankings_data.get("items", [])
            if ranking_items:
                st.subheader("영향도 랭킹")
                df_rank = pd.DataFrame(ranking_items)
                rank_cols = []
                rank_map = {}
                for col, label in [
                    ("rank", "순위"),
                    ("title", "문서명"),
                    ("citation_count", "인용 수"),
                ]:
                    if col in df_rank.columns:
                        rank_cols.append(col)
                        rank_map[col] = label

                if rank_cols:
                    df_rank_display = df_rank[rank_cols].rename(columns=rank_map)
                    st.dataframe(df_rank_display, use_container_width=True, hide_index=True)


# ============================================================================
# 3) KnowledgeTier
# ============================================================================
with tab_tier:
    tier_data = api_client.get_kb_value_tiers(kb_id)
    if api_failed(tier_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_tier"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("Knowledge Tier 분포")

        # 4 tiers (백엔드 실제 반환: GOLD, SILVER, BRONZE, STANDARD)
        TIERS = {
            "GOLD": {"label": "Gold", "color": "#FFD700", "desc": "KTS ≥ 85"},
            "SILVER": {"label": "Silver", "color": "#C0C0C0", "desc": "KTS ≥ 70"},
            "BRONZE": {"label": "Bronze", "color": "#CD7F32", "desc": "KTS ≥ 50"},
            "STANDARD": {"label": "Standard", "color": "#4A90D9", "desc": "KTS < 50"},
        }

        # 백엔드 필드명: "tiers" (not "distribution")
        tiers_dist = tier_data.get("tiers", {})

        if tiers_dist and any(v > 0 for v in tiers_dist.values()):
            labels = []
            values = []
            colors = []
            for tier_key, tier_info in TIERS.items():
                count = tiers_dist.get(tier_key, 0)
                labels.append(tier_info["label"])
                values.append(count)
                colors.append(tier_info["color"])

            fig = go.Figure(
                go.Pie(
                    labels=labels, values=values,
                    marker=dict(colors=colors),
                    textinfo="label+percent+value",
                    hole=0.35,
                )
            )
            fig.update_layout(
                title="Tier 분포", height=400,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

            # Tier별 메트릭
            st.markdown("---")
            st.subheader("Tier별 문서 수")
            tier_cols = st.columns(4)
            for i, (tier_key, tier_info) in enumerate(TIERS.items()):
                with tier_cols[i]:
                    count = tiers_dist.get(tier_key, 0)
                    st.metric(tier_info["label"], f"{count:,}건")
                    st.caption(tier_info["desc"])
        else:
            st.info("Tier 분포 데이터가 없습니다.")
