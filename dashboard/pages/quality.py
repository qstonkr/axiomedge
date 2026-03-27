"""품질 관리 -- 문서 품질, RAG 평가, KTS 신뢰도, 투명성 통합 페이지

Created: 2026-02-20
Updated: 2026-02-21 - Qdrant fallback when trust scores unavailable
"""

import streamlit as st

st.set_page_config(page_title="품질 관리", page_icon="📈", layout="wide")


import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("품질 관리")
st.caption("문서 품질, RAG 평가 메트릭, KTS 신뢰도, 투명성 지표를 관리합니다.")

tab_quality, tab_rag, tab_kts, tab_transparency = st.tabs(
    ["문서 품질", "RAG 평가", "KTS 신뢰도", "투명성"]
)

# ============================================================================
# 1) 문서 품질
# ============================================================================
with tab_quality:
    kbs_result = api_client.list_kbs()
    if api_failed(kbs_result):
        st.error("KB 목록 API 연결 실패")
        if st.button("재시도", key="retry_q_kbs"):
            st.cache_data.clear()
            st.rerun()
    else:
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        kb_options = {kb.get("name", kb.get("id", "")): kb.get("id", kb.get("kb_id", "")) for kb in kb_items}

        if kb_options:
            selected_kb = st.selectbox("KB 선택", list(kb_options.keys()), key="q_kb")
            kb_id = kb_options[selected_kb]

            # Try trust scores first
            trust_data = api_client.get_kb_trust_scores(kb_id)
            trust_items = []
            if not api_failed(trust_data):
                trust_items = trust_data.get("items", trust_data.get("scores", []))

            if trust_items:
                # ── KTS 6-Signal 레이더 차트 (trust score 데이터 있을 때) ──
                st.subheader("KTS 6-Signal 레이더 차트")

                SIGNALS = {
                    "accuracy": {"label": "정확도", "weight": 0.25, "field": "hallucination_score"},
                    "source_credibility": {"label": "출처 신뢰도", "weight": 0.20, "field": "source_credibility"},
                    "freshness": {"label": "신선도", "weight": 0.20, "field": "freshness_score"},
                    "consistency": {"label": "일관성", "weight": 0.15, "field": "consistency_score"},
                    "usage_feedback": {"label": "사용자 피드백", "weight": 0.10, "field": "usage_score"},
                    "expert_validation": {"label": "전문가 검증", "weight": 0.10, "field": "user_validation_score"},
                }

                scores: dict[str, float] = {}
                for key, sig in SIGNALS.items():
                    field = sig["field"]
                    vals = [item.get(field, 0) for item in trust_items if isinstance(item, dict)]
                    scores[key] = sum(vals) / len(vals) if vals else 0

                categories = [s["label"] for s in SIGNALS.values()]
                values = [scores.get(k, 0) for k in SIGNALS]

                fig = go.Figure()
                fig.add_trace(
                    go.Scatterpolar(
                        r=values + [values[0]],
                        theta=categories + [categories[0]],
                        fill="toself",
                        name="KTS 점수",
                        fillcolor="rgba(74, 144, 217, 0.3)",
                        line=dict(color="#4A90D9"),
                    )
                )
                fig.update_layout(
                    polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
                    title="6-Signal 레이더", height=400,
                    margin=dict(l=60, r=60, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                st.markdown("**가중치 배분:**")
                wcols = st.columns(6)
                for i, (key, sig) in enumerate(SIGNALS.items()):
                    with wcols[i]:
                        score = scores.get(key, 0)
                        st.metric(sig["label"], f"{score:.2f}", help=f"가중치: {sig['weight']}")

                st.markdown("---")
                st.subheader("ConfidenceTier 분포")
                CONFIDENCE_TIERS = {
                    "HIGH": {"color": "#2ECC71", "label": "높음"},
                    "MEDIUM": {"color": "#F39C12", "label": "보통"},
                    "LOW": {"color": "#E74C3C", "label": "낮음"},
                    "UNVERIFIED": {"color": "#95A5A6", "label": "미검증"},
                }

                tier_dist: dict[str, int] = {}
                for item in trust_items:
                    if isinstance(item, dict):
                        tier = item.get("confidence_tier", "uncertain").upper()
                        if tier == "UNCERTAIN":
                            tier = "UNVERIFIED"
                        tier_dist[tier] = tier_dist.get(tier, 0) + 1

                if tier_dist:
                    labels = [v["label"] for v in CONFIDENCE_TIERS.values()]
                    values = [tier_dist.get(k, 0) for k in CONFIDENCE_TIERS]
                    colors = [v["color"] for v in CONFIDENCE_TIERS.values()]

                    fig2 = go.Figure(
                        go.Pie(
                            labels=labels, values=values,
                            marker=dict(colors=colors),
                            textinfo="label+percent+value",
                            hole=0.3,
                        )
                    )
                    fig2.update_layout(title="Confidence Tier 분포", height=350)
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("Confidence Tier 데이터가 없습니다.")

            else:
                # ── Qdrant 기반 문서 품질 현황 (trust score 없을 때) ──
                st.info("KTS Trust Score가 아직 계산되지 않았습니다. Qdrant 크롤링 데이터 기반 품질 현황을 표시합니다.")

                # Fetch KB stats + documents + categories
                kb_stats = api_client.get_kb_stats(kb_id)
                doc_data = api_client.get_kb_documents(kb_id, page_size=100)
                cat_data = api_client.get_kb_categories(kb_id)

                doc_count = 0
                if not api_failed(kb_stats):
                    doc_count = kb_stats.get("document_count", 0)

                doc_items = []
                if not api_failed(doc_data):
                    doc_items = doc_data.get("items", doc_data.get("documents", []))

                cat_items = []
                if not api_failed(cat_data):
                    cat_items = cat_data.get("categories", [])

                # ── Summary metrics ──
                st.subheader("문서 현황 요약")
                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    st.metric("총 문서 (벡터)", f"{doc_count:,}건")
                with m2:
                    sources = {}
                    for d in doc_items:
                        src = d.get("source_type", "unknown")
                        sources[src] = sources.get(src, 0) + 1
                    primary_source = max(sources, key=sources.get) if sources else "-"
                    st.metric("주요 소스", primary_source)
                with m3:
                    unique_titles = len(set(d.get("title", "") for d in doc_items))
                    st.metric("고유 문서", f"{unique_titles}건")
                with m4:
                    chunk_ratio = (len(doc_items) / unique_titles) if unique_titles > 0 else 0
                    st.metric("평균 청크/문서", f"{chunk_ratio:.1f}")

                # ── Source distribution ──
                if sources:
                    st.markdown("---")
                    st.subheader("소스 유형 분포")
                    src_colors = {"confluence": "#0052CC", "jira": "#0065FF", "git": "#F05032", "unknown": "#95A5A6"}
                    fig_src = go.Figure(
                        go.Pie(
                            labels=list(sources.keys()),
                            values=list(sources.values()),
                            marker=dict(colors=[src_colors.get(s, "#BDC3C7") for s in sources]),
                            textinfo="label+percent+value",
                            hole=0.3,
                        )
                    )
                    fig_src.update_layout(title="데이터 소스 분포", height=300)
                    st.plotly_chart(fig_src, use_container_width=True)

                # ── Category distribution ──
                if cat_items:
                    st.markdown("---")
                    st.subheader("카테고리 분포")
                    cat_names = [c.get("name", "") for c in cat_items]
                    cat_counts = [c.get("document_count", 0) for c in cat_items]
                    fig_cat = go.Figure(
                        go.Bar(x=cat_names, y=cat_counts, marker_color="#4A90D9")
                    )
                    fig_cat.update_layout(
                        title="카테고리별 문서 수",
                        xaxis_title="카테고리", yaxis_title="문서 수",
                        height=300,
                    )
                    st.plotly_chart(fig_cat, use_container_width=True)

                # ── Freshness analysis ──
                st.markdown("---")
                st.subheader("신선도 분석")
                dates = [d.get("updated_at", "") for d in doc_items if d.get("updated_at")]
                if dates:
                    now = datetime.now(timezone.utc)
                    age_buckets = {"< 7일": 0, "7-30일": 0, "30-90일": 0, "90일+": 0}
                    for date_str in dates:
                        try:
                            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                            age_days = (now - dt).days
                            if age_days < 7:
                                age_buckets["< 7일"] += 1
                            elif age_days < 30:
                                age_buckets["7-30일"] += 1
                            elif age_days < 90:
                                age_buckets["30-90일"] += 1
                            else:
                                age_buckets["90일+"] += 1
                        except (ValueError, TypeError):
                            age_buckets["90일+"] += 1

                    freshness_colors = ["#2ECC71", "#3498DB", "#F39C12", "#E74C3C"]
                    fig_fresh = go.Figure(
                        go.Bar(
                            x=list(age_buckets.keys()),
                            y=list(age_buckets.values()),
                            marker_color=freshness_colors,
                        )
                    )
                    fig_fresh.update_layout(
                        title="문서 업데이트 경과일 분포",
                        xaxis_title="경과일", yaxis_title="문서 수",
                        height=300,
                    )
                    st.plotly_chart(fig_fresh, use_container_width=True)

                    fresh_pct = (age_buckets["< 7일"] + age_buckets["7-30일"]) / len(dates) * 100 if dates else 0
                    if fresh_pct >= 70:
                        st.success(f"신선도 양호: 30일 이내 문서 {fresh_pct:.0f}%")
                    elif fresh_pct >= 40:
                        st.warning(f"신선도 보통: 30일 이내 문서 {fresh_pct:.0f}%")
                    else:
                        st.error(f"신선도 낮음: 30일 이내 문서 {fresh_pct:.0f}%")
                else:
                    st.info("문서 업데이트 일시 정보가 없습니다.")

                # ── Document list ──
                if doc_items:
                    st.markdown("---")
                    st.subheader("문서 목록")
                    rows = []
                    for d in doc_items:
                        rows.append({
                            "제목": d.get("title", "-"),
                            "소스": d.get("source_type", "-"),
                            "상태": d.get("status", "-"),
                            "업데이트": (d.get("updated_at") or "")[:16],
                        })
                    df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("등록된 KB가 없습니다.")

# ============================================================================
# 2) RAG 평가
# ============================================================================
with tab_rag:
    eval_data = api_client.list_evaluation_history()
    if api_failed(eval_data):
        st.warning("RAG 평가 API 데이터를 불러올 수 없습니다.")
        if st.button("재시도", key="retry_rag"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("RAG 평가 이력")

        # API returns "jobs" key, fallback to "items" for compatibility
        evaluations = eval_data.get("jobs", eval_data.get("items", eval_data.get("evaluations", [])))
        if evaluations:
            # Metrics summary
            latest = evaluations[0] if evaluations else {}
            metrics = latest.get("metrics", {})

            m1, m2, m3, m4 = st.columns(4)
            with m1:
                faithfulness = metrics.get("faithfulness", 0)
                threshold_ok = faithfulness >= 0.65
                st.metric(
                    "Faithfulness",
                    f"{faithfulness:.3f}",
                    delta="Pass" if threshold_ok else "Fail",
                    delta_color="normal" if threshold_ok else "inverse",
                )
            with m2:
                relevancy = metrics.get("answer_relevancy", 0)
                st.metric("Answer Relevancy", f"{relevancy:.3f}")
            with m3:
                precision = metrics.get("context_precision", 0)
                st.metric("Context Precision", f"{precision:.3f}")
            with m4:
                overall = metrics.get("overall_score", 0)
                st.metric("Overall Score", f"{overall:.3f}")

            # Quality gate
            st.markdown("---")
            st.markdown("#### Quality Gate")
            gate_threshold = 0.65
            st.markdown(f"- **Faithfulness 임계값**: `>= {gate_threshold}`")
            if faithfulness >= gate_threshold:
                st.success(f"Quality Gate 통과: Faithfulness {faithfulness:.3f} >= {gate_threshold}")
            else:
                st.error(f"Quality Gate 실패: Faithfulness {faithfulness:.3f} < {gate_threshold}")

            # FaithfulnessChecker threshold display
            st.markdown(f"- **FaithfulnessChecker 기준**: 0.7+ (엄격 모드)")

            # Daily/weekly trend chart
            st.markdown("---")
            st.subheader("평가 추이")
            if len(evaluations) > 1:
                dates = [e.get("created_at", e.get("started_at", ""))[:10] for e in evaluations]
                faith_vals = [e.get("metrics", {}).get("faithfulness", 0) for e in evaluations]
                relev_vals = [e.get("metrics", {}).get("answer_relevancy", 0) for e in evaluations]
                prec_vals = [e.get("metrics", {}).get("context_precision", 0) for e in evaluations]

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=dates, y=faith_vals, name="Faithfulness", mode="lines+markers"))
                fig.add_trace(go.Scatter(x=dates, y=relev_vals, name="Answer Relevancy", mode="lines+markers"))
                fig.add_trace(go.Scatter(x=dates, y=prec_vals, name="Context Precision", mode="lines+markers"))
                fig.add_hline(y=gate_threshold, line_dash="dash", line_color="red", annotation_text="Quality Gate")
                fig.update_layout(
                    title="RAG 평가 추이", xaxis_title="날짜", yaxis_title="점수",
                    height=400, yaxis=dict(range=[0, 1]),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("추이 차트를 위해 2개 이상의 평가 기록이 필요합니다.")

            # Evaluation history table
            st.markdown("---")
            st.subheader("평가 기록")
            import pandas as pd

            rows = []
            for e in evaluations:
                m = e.get("metrics", {})
                rows.append({
                    "날짜": (e.get("created_at") or e.get("started_at") or "")[:16],
                    "Faithfulness": f"{m.get('faithfulness', 0):.3f}",
                    "Answer Relevancy": f"{m.get('answer_relevancy', 0):.3f}",
                    "Context Precision": f"{m.get('context_precision', 0):.3f}",
                    "상태": e.get("status", ""),
                    "엔진": e.get("engine", e.get("domain", "")),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("평가 기록이 없습니다.")

# ============================================================================
# 3) KTS 신뢰도
# ============================================================================
with tab_kts:
    kbs_result2 = api_client.list_kbs()
    if api_failed(kbs_result2):
        st.warning("KB 목록 API 데이터를 불러올 수 없습니다.")
    else:
        kb_items2 = kbs_result2.get("items", kbs_result2.get("kbs", []))
        kb_options2 = {kb.get("name", kb.get("id", "")): kb.get("id", kb.get("kb_id", "")) for kb in kb_items2}

        if kb_options2:
            sel_kb2 = st.selectbox("KB 선택", list(kb_options2.keys()), key="kts_kb")
            kb_id2 = kb_options2[sel_kb2]

            dist_data = api_client.get_kb_trust_score_distribution(kb_id2)
            if api_failed(dist_data):
                st.warning("Trust Score 분포 API 데이터를 불러올 수 없습니다.")
                if st.button("재시도", key="retry_kts"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                st.subheader("KTS 신뢰도 분포")

                # FreshnessDomain expiry
                FRESHNESS_DOMAINS = {
                    "REGULATORY": {"label": "규정/정책", "expiry_days": 90, "color": "#E74C3C"},
                    "TECHNICAL": {"label": "기술 문서", "expiry_days": 180, "color": "#F39C12"},
                    "BUSINESS": {"label": "비즈니스", "expiry_days": 365, "color": "#3498DB"},
                    "REFERENCE": {"label": "참조", "expiry_days": None, "color": "#2ECC71"},
                }

                st.markdown("#### 도메인별 신선도 만료 기준")
                d_cols = st.columns(4)
                for i, (domain, info) in enumerate(FRESHNESS_DOMAINS.items()):
                    with d_cols[i]:
                        expiry = f"{info['expiry_days']}일" if info["expiry_days"] else "무제한"
                        st.metric(info["label"], expiry)

                # Trust Score Distribution bar chart (from API distribution field)
                st.markdown("---")
                st.subheader("Confidence Tier 분포")
                distribution = dist_data.get("distribution", {})
                avg_score = dist_data.get("avg_score", 0)

                if distribution and any(v > 0 for v in distribution.values()):
                    TIER_COLORS = {
                        "HIGH": "#2ECC71",
                        "MEDIUM": "#F39C12",
                        "LOW": "#E74C3C",
                        "UNCERTAIN": "#95A5A6",
                    }
                    TIER_LABELS = {
                        "HIGH": "높음",
                        "MEDIUM": "보통",
                        "LOW": "낮음",
                        "UNCERTAIN": "미검증",
                    }

                    labels = [TIER_LABELS.get(k, k) for k in distribution]
                    values = list(distribution.values())
                    colors = [TIER_COLORS.get(k, "#BDC3C7") for k in distribution]

                    fig = go.Figure(
                        go.Bar(x=labels, y=values, marker_color=colors)
                    )
                    fig.update_layout(
                        title=f"Confidence Tier 분포 (평균 KTS: {avg_score:.2f})",
                        xaxis_title="Tier", yaxis_title="문서 수",
                        height=350,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # Summary metrics
                    total_docs = sum(distribution.values())
                    st.markdown(f"- **전체 문서**: {total_docs}건")
                    st.markdown(f"- **평균 KTS 점수**: {avg_score:.2f}")
                    if total_docs > 0:
                        high_pct = distribution.get("HIGH", 0) / total_docs * 100
                        st.markdown(f"- **HIGH 비율**: {high_pct:.1f}%")
                else:
                    st.info("Trust Score 분포 데이터가 없습니다.")

                # 6-signal contribution (from trust scores items)
                st.markdown("---")
                st.subheader("6-Signal 기여도 상세")

                # Fetch trust scores to compute signal contributions
                trust_for_signal = api_client.get_kb_trust_scores(kb_id2)
                if not api_failed(trust_for_signal):
                    signal_items = trust_for_signal.get("items", trust_for_signal.get("scores", []))
                    if signal_items:
                        SIGNAL_FIELDS = {
                            "accuracy": ("정확도 (0.25)", "hallucination_score"),
                            "source_credibility": ("출처 신뢰도 (0.20)", "source_credibility"),
                            "freshness": ("신선도 (0.20)", "freshness_score"),
                            "consistency": ("일관성 (0.15)", "consistency_score"),
                            "usage_feedback": ("사용자 피드백 (0.10)", "usage_score"),
                            "expert_validation": ("전문가 검증 (0.10)", "user_validation_score"),
                        }
                        for sig_key, (sig_label, field_name) in SIGNAL_FIELDS.items():
                            vals = [item.get(field_name, 0) for item in signal_items if isinstance(item, dict)]
                            sig_val = sum(vals) / len(vals) if vals else 0
                            st.progress(min(sig_val, 1.0), text=f"{sig_label}: {sig_val:.3f}")
                    else:
                        st.info("Signal 기여도 데이터가 없습니다.")
                else:
                    st.info("Signal 기여도 데이터가 없습니다.")
        else:
            st.info("등록된 KB가 없습니다.")

# ============================================================================
# 4) 투명성
# ============================================================================
with tab_transparency:
    transp_data = api_client.get_transparency_stats()
    if api_failed(transp_data):
        st.warning("투명성 통계 API 데이터를 불러올 수 없습니다.")
        if st.button("재시도", key="retry_transp"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("투명성 지표")

        # Main metrics from API response (map backend keys to dashboard keys)
        total_citations = transp_data.get("total_citations", transp_data.get("total_documents", 0))
        source_coverage_rate = transp_data.get("source_coverage_rate", transp_data.get("transparency_score", 0))
        avg_sources = transp_data.get("avg_sources_per_response", transp_data.get("with_provenance", 0))

        # Summary metrics
        st.markdown("#### 출처 인용 통계")
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("총 인용 가능 문서", f"{total_citations:,}건")
        with m2:
            st.metric("출처 커버리지", f"{source_coverage_rate:.1%}")
        with m3:
            st.metric("평균 출처 수/응답", f"{avg_sources:.1f}")

        # Source coverage gauge
        st.markdown("---")
        st.markdown("#### 출처 커버리지 게이지")
        st.progress(min(source_coverage_rate, 1.0))

        if source_coverage_rate >= 0.8:
            st.success(f"커버리지 양호: {source_coverage_rate:.1%}")
        elif source_coverage_rate >= 0.5:
            st.warning(f"커버리지 보통: {source_coverage_rate:.1%}")
        elif total_citations > 0:
            st.error(f"커버리지 낮음: {source_coverage_rate:.1%}")
        else:
            st.info("인용 가능 문서가 없습니다. KB 동기화를 실행하세요.")

        # TransparencyFormatter label distribution (static reference)
        st.markdown("---")
        st.markdown("#### TransparencyFormatter 라벨 유형")
        LABEL_TYPES = {
            "Document": {"label": "문서 기반", "desc": "KB 문서에서 직접 인용한 응답"},
            "Inference": {"label": "추론 기반", "desc": "문서 내용을 기반으로 추론한 응답"},
            "General": {"label": "일반 지식", "desc": "LLM 일반 지식에 의한 응답"},
        }
        for key, info in LABEL_TYPES.items():
            st.markdown(f"- **{info['label']}**: {info['desc']}")
