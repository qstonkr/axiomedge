"""충돌 / 중복 관리

3 탭: 4-Stage 중복, 충돌 분석, 해결 이력

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="충돌 / 중복", page_icon="⚠️", layout="wide")


import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("⚠️ 충돌 / 중복")

tab_dedup, tab_conflict, tab_history = st.tabs(["4-Stage 중복", "충돌 분석", "해결 이력"])


# =============================================================================
# 탭 1: 4-Stage 중복 파이프라인
# =============================================================================
with tab_dedup:
    dedup_result = api_client.get_dedup_stats()

    if api_failed(dedup_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_dedup"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("4-Stage 중복 제거 파이프라인")
        st.caption(
            "Stage 1: Bloom Filter (<1ms) -> Stage 2: MinHash LSH (<10ms) -> "
            "Stage 3: SemHash (~50ms) -> Stage 4: LLM ConflictDetector (~100ms)"
        )

        # Stage별 통계 (map backend keys to dashboard keys)
        raw_stages = dedup_result.get("stages", {})
        STAGE_KEY_MAP = {
            "bloom": "bloom_filter",
            "lsh": "minhash_lsh",
            "llm": "conflict_detector",
        }
        stages = {}
        for k, v in raw_stages.items():
            mapped_key = STAGE_KEY_MAP.get(k, k)
            stages[mapped_key] = v

        stage_defs = [
            {
                "key": "bloom_filter",
                "name": "Stage 1: Bloom Filter",
                "threshold": "<1ms",
                "description": "해시 기반 빠른 중복 필터링 (30-40% 제거)",
            },
            {
                "key": "minhash_lsh",
                "name": "Stage 2: MinHash LSH",
                "threshold": "Jaccard >= 0.80",
                "description": "Jaccard 유사도 기반 후보 쌍 추출",
            },
            {
                "key": "semhash",
                "name": "Stage 3: SemHash",
                "threshold": "Cosine >= 0.90",
                "description": "의미적 유사도 기반 중복 확인",
            },
            {
                "key": "conflict_detector",
                "name": "Stage 4: LLM ConflictDetector",
                "threshold": "6 conflict types",
                "description": "LLM 기반 충돌 유형 분석",
            },
        ]

        # Funnel 차트 데이터 구성
        funnel_labels = []
        funnel_values = []
        total_input = dedup_result.get("total_input", 0)

        for sdef in stage_defs:
            stage_data = stages.get(sdef["key"], {})
            input_count = stage_data.get("input_count", total_input)
            output_count = stage_data.get("output_count", input_count)
            filtered = stage_data.get("filtered_count", input_count - output_count)
            filter_rate = stage_data.get("filter_rate", 0)

            funnel_labels.append(sdef["name"])
            funnel_values.append(input_count if input_count > 0 else total_input)

            # Stage 카드
            with st.container(border=True):
                scol1, scol2, scol3, scol4 = st.columns(4)
                with scol1:
                    st.markdown(f"**{sdef['name']}**")
                    st.caption(sdef["description"])
                with scol2:
                    st.metric("임계값", sdef["threshold"])
                with scol3:
                    st.metric("필터링", f"{filtered:,}건")
                with scol4:
                    st.metric("필터율", f"{filter_rate:.1%}")

            total_input = output_count if output_count > 0 else total_input

        # Funnel 차트
        st.markdown("---")
        st.subheader("필터링 퍼널")
        if any(v > 0 for v in funnel_values):
            fig_funnel = go.Figure(go.Funnel(
                y=funnel_labels,
                x=funnel_values,
                textinfo="value+percent previous",
                marker={"color": ["#4CAF50", "#2196F3", "#FF9800", "#F44336"]},
            ))
            fig_funnel.update_layout(
                title="Stage별 필터링 퍼널",
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_funnel, use_container_width=True)

        st.markdown("---")

        # DedupStatus / Resolution 분포
        st.subheader("중복 상태 / 해결 방법 분포")
        dcol1, dcol2 = st.columns(2)

        with dcol1:
            status_dist = dedup_result.get("status_distribution", {})
            if status_dist:
                fig_status = px.pie(
                    names=list(status_dist.keys()),
                    values=list(status_dist.values()),
                    title="DedupStatus 분포 (5종)",
                    hole=0.3,
                )
                fig_status.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_status, use_container_width=True)
            else:
                st.info("상태 분포 데이터가 없습니다.")

        with dcol2:
            resolution_dist = dedup_result.get("resolution_distribution", {})
            if resolution_dist:
                fig_res = px.pie(
                    names=list(resolution_dist.keys()),
                    values=list(resolution_dist.values()),
                    title="Resolution 분포 (5종)",
                    hole=0.3,
                )
                fig_res.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_res, use_container_width=True)
            else:
                st.info("해결 방법 분포 데이터가 없습니다.")


# =============================================================================
# 탭 2: 충돌 분석
# =============================================================================
with tab_conflict:
    conflicts_result = api_client.get_dedup_conflicts()

    if api_failed(conflicts_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_conflicts"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("충돌 유형별 분석")
        st.caption(
            "ConflictType 6종: FACTUAL / TEMPORAL / SCOPE / PERSPECTIVE / PARTIAL / SUPERSEDED"
        )

        conflicts = conflicts_result.get("items", conflicts_result.get("conflicts", []))

        if conflicts:
            # 충돌 유형 분포
            type_counts: dict[str, int] = {}
            severity_counts: dict[str, int] = {}
            for c in conflicts:
                ct = c.get("conflict_type", "UNKNOWN")
                cs = c.get("severity", "MEDIUM")
                type_counts[ct] = type_counts.get(ct, 0) + 1
                severity_counts[cs] = severity_counts.get(cs, 0) + 1

            ccol1, ccol2 = st.columns(2)
            with ccol1:
                if type_counts:
                    fig_ct = px.bar(
                        x=list(type_counts.keys()),
                        y=list(type_counts.values()),
                        title="ConflictType 분포",
                        labels={"x": "유형", "y": "건수"},
                        color=list(type_counts.keys()),
                    )
                    fig_ct.update_layout(showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig_ct, use_container_width=True)

            with ccol2:
                if severity_counts:
                    severity_colors = {
                        "CRITICAL": "#F44336",
                        "HIGH": "#FF9800",
                        "MEDIUM": "#FFC107",
                        "LOW": "#4CAF50",
                    }
                    fig_sev = px.pie(
                        names=list(severity_counts.keys()),
                        values=list(severity_counts.values()),
                        title="ConflictSeverity 분포",
                        color=list(severity_counts.keys()),
                        color_discrete_map=severity_colors,
                        hole=0.3,
                    )
                    fig_sev.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig_sev, use_container_width=True)

            st.markdown("---")

            # 충돌 목록 (Side-by-side 비교)
            st.subheader("충돌 상세")
            severity_badges = {
                "CRITICAL": "🔴",
                "HIGH": "🟠",
                "MEDIUM": "🟡",
                "LOW": "🟢",
            }

            for idx, conflict in enumerate(conflicts[:20]):
                c_type = conflict.get("conflict_type", "UNKNOWN")
                c_sev = conflict.get("severity", "MEDIUM")
                sev_badge = severity_badges.get(c_sev, "⚪")

                with st.expander(
                    f"{sev_badge} {c_type} ({c_sev}) - "
                    f"{conflict.get('doc_a_title', conflict.get('document_a', '-'))[:30]} vs "
                    f"{conflict.get('doc_b_title', conflict.get('document_b', '-'))[:30]}",
                    expanded=False,
                ):
                    # Side-by-side 비교
                    lcol, rcol = st.columns(2)
                    with lcol:
                        st.markdown("**문서 A**")
                        st.write(conflict.get("doc_a_title", conflict.get("document_a", "-")))
                        st.caption(conflict.get("doc_a_excerpt", conflict.get("excerpt_a", "")))
                    with rcol:
                        st.markdown("**문서 B**")
                        st.write(conflict.get("doc_b_title", conflict.get("document_b", "-")))
                        st.caption(conflict.get("doc_b_excerpt", conflict.get("excerpt_b", "")))

                    st.markdown(f"**충돌 설명:** {conflict.get('description', conflict.get('reason', '-'))}")

                    # 해결 액션 버튼
                    acol1, acol2, acol3 = st.columns(3)
                    conflict_id = conflict.get("id", conflict.get("conflict_id", str(idx)))
                    with acol1:
                        if st.button("병합", key=f"merge_{conflict_id}", type="primary"):
                            res = api_client.resolve_dedup_conflict({
                                "conflict_id": conflict_id,
                                "resolution": "MERGE",
                            })
                            if not api_failed(res):
                                st.success("병합 완료")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error("병합 실패")
                    with acol2:
                        if st.button("둘 다 유지", key=f"keep_{conflict_id}"):
                            res = api_client.resolve_dedup_conflict({
                                "conflict_id": conflict_id,
                                "resolution": "KEEP_BOTH",
                            })
                            if not api_failed(res):
                                st.success("처리 완료")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error("처리 실패")
                    with acol3:
                        if st.button("보관", key=f"archive_{conflict_id}"):
                            res = api_client.resolve_dedup_conflict({
                                "conflict_id": conflict_id,
                                "resolution": "ARCHIVE",
                            })
                            if not api_failed(res):
                                st.success("보관 처리 완료")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error("처리 실패")
        else:
            st.info("탐지된 충돌이 없습니다.")


# =============================================================================
# 탭 3: 해결 이력
# =============================================================================
with tab_history:
    st.subheader("충돌 해결 이력")

    # 해결된 충돌 = status=RESOLVED 인 충돌 조회
    conflicts_all = api_client.get_dedup_conflicts(page_size=50)

    if api_failed(conflicts_all):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_history"):
            st.cache_data.clear()
            st.rerun()
    else:
        all_items = conflicts_all.get("items", conflicts_all.get("conflicts", []))
        resolved = [c for c in all_items if c.get("status", "").upper() in ("RESOLVED", "MERGED", "ARCHIVED", "KEPT")]

        if resolved:
            rows = []
            for r in resolved:
                rows.append({
                    "충돌 ID": str(r.get("id", r.get("conflict_id", "-")))[:12],
                    "유형": r.get("conflict_type", "-"),
                    "해결 방법": r.get("resolution", r.get("resolution_method", "-")),
                    "해결자": r.get("resolved_by", r.get("resolver", "-")),
                    "해결 시간": r.get("resolved_at", r.get("resolved_timestamp", "-"))[:16] if r.get("resolved_at") or r.get("resolved_timestamp") else "-",
                    "문서 A": str(r.get("doc_a_title", r.get("document_a", "-")))[:30],
                    "문서 B": str(r.get("doc_b_title", r.get("document_b", "-")))[:30],
                })
            df_resolved = pd.DataFrame(rows)
            st.dataframe(df_resolved, use_container_width=True, hide_index=True)
        else:
            st.info("해결된 충돌 이력이 없습니다.")


st.markdown("---")
st.caption(
    "📌 4-Stage Dedup Pipeline | "
    "Stage 1: Bloom (<1ms) -> Stage 2: MinHash LSH -> "
    "Stage 3: SemHash -> Stage 4: LLM ConflictDetector"
)
