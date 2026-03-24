"""파이프라인 현황 (병합)

구 14_operations + ingestion gates 병합.
4 탭: 파이프라인, 커넥터, 인제스천 게이트, 스케줄

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="파이프라인 현황", page_icon="🔄", layout="wide")


import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from components.constants import PIPELINE_STEP_KEYS, PIPELINE_STEP_LABELS
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🔄 파이프라인 현황")

tab_pipeline, tab_connector, tab_gates, tab_schedule = st.tabs(
    ["파이프라인", "커넥터", "인제스천 게이트", "스케줄"]
)


# =============================================================================
# 탭 1: 파이프라인
# =============================================================================
with tab_pipeline:
    metrics_result = api_client.get_pipeline_metrics()

    if api_failed(metrics_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_pipe_metrics"):
            st.cache_data.clear()
            st.rerun()
    else:
        # ── 전체 메트릭 ──
        st.subheader("파이프라인 성능 메트릭")
        total_processed = metrics_result.get("total_documents_processed", 0)
        success_rate = metrics_result.get("success_rate", 0)
        throughput = metrics_result.get("throughput", 0)
        avg_latency = metrics_result.get("avg_latency_ms", 0)
        error_rate = metrics_result.get("error_rate", 1.0 - success_rate if success_rate else 0)

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("처리량 (docs/min)", f"{throughput:.1f}")
        with m2:
            st.metric("평균 지연 (ms)", f"{avg_latency:.0f}")
        with m3:
            st.metric("오류율", f"{error_rate:.1%}" if isinstance(error_rate, float) else str(error_rate))

        st.markdown("---")

        # ── 9-Step 단계별 메트릭 ──
        st.subheader("단계별 성능")
        step_metrics = metrics_result.get("steps", metrics_result.get("step_metrics", {}))

        if step_metrics:
            names = []
            throughputs = []
            latencies = []
            errors = []

            for step_key in PIPELINE_STEP_KEYS:
                info = step_metrics.get(step_key, {})
                names.append(PIPELINE_STEP_LABELS.get(step_key, step_key))
                throughputs.append(info.get("throughput", 0))
                latencies.append(info.get("avg_latency_ms", 0))
                errors.append(info.get("error_rate", 0))

            # 지연 시간 바 차트
            fig_latency = px.bar(
                x=names,
                y=latencies,
                title="단계별 평균 지연 시간 (ms)",
                labels={"x": "단계", "y": "지연 시간 (ms)"},
                color=latencies,
                color_continuous_scale="RdYlGn_r",
            )
            fig_latency.update_layout(margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
            st.plotly_chart(fig_latency, use_container_width=True)

            # 오류율 바 차트
            fig_error = px.bar(
                x=names,
                y=[e * 100 for e in errors],
                title="단계별 오류율 (%)",
                labels={"x": "단계", "y": "오류율 (%)"},
                color=[e * 100 for e in errors],
                color_continuous_scale="RdYlGn_r",
            )
            fig_error.update_layout(margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
            st.plotly_chart(fig_error, use_container_width=True)
        else:
            st.info("단계별 메트릭 데이터가 없습니다.")

        st.markdown("---")

        # ── CRAG 액션 분포 ──
        st.subheader("CRAG 액션 분포")
        crag_result = api_client.get_crag_stats()

        if api_failed(crag_result):
            st.warning("CRAG 통계를 가져올 수 없습니다.")
        else:
            actions = crag_result.get("action_distribution", crag_result.get("actions", {}))
            if actions:
                action_names = list(actions.keys())
                action_counts = list(actions.values())

                crag_colors = {
                    "CORRECT": "#28a745",
                    "AMBIGUOUS": "#ffc107",
                    "INCORRECT": "#dc3545",
                }
                bar_colors = [crag_colors.get(a, "#6c757d") for a in action_names]

                fig_crag = go.Figure(
                    go.Bar(
                        x=action_names,
                        y=action_counts,
                        marker_color=bar_colors,
                        text=action_counts,
                        textposition="auto",
                    )
                )
                fig_crag.update_layout(
                    title="CRAG 판정 분포 (CORRECT / AMBIGUOUS / INCORRECT)",
                    xaxis_title="판정",
                    yaxis_title="건수",
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig_crag, use_container_width=True)
            else:
                st.info("CRAG 액션 데이터가 없습니다.")


# =============================================================================
# 탭 2: 커넥터
# =============================================================================
with tab_connector:
    st.subheader("커넥터 상태")
    sources_result = api_client.list_data_sources()

    if api_failed(sources_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_connector"):
            st.cache_data.clear()
            st.rerun()
    else:
        sources = sources_result.get("items", sources_result.get("sources", []))
        if sources:
            rows = []
            for s in sources:
                health = s.get("health_status", s.get("status", "UNKNOWN"))
                health_badge = {
                    "HEALTHY": "🟢 정상",
                    "ACTIVE": "🟢 정상",
                    "CONNECTED": "🟢 연결됨",
                    "WARNING": "🟡 주의",
                    "ERROR": "🔴 오류",
                    "DISCONNECTED": "🔴 연결 해제",
                    "SYNCING": "🔵 동기화 중",
                }.get(health, f"⚪ {health}")

                rows.append({
                    "이름": s.get("name", "-"),
                    "타입": s.get("connector_type", s.get("source_type", s.get("type", "-"))),
                    "상태": health_badge,
                    "대상 KB": s.get("kb_name", s.get("kb_id", "-")),
                    "문서 수": s.get("document_count", 0),
                    "마지막 동기화": s.get("last_synced_at", s.get("last_sync", "-")),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("등록된 커넥터가 없습니다.")


# =============================================================================
# 탭 3: 인제스천 게이트
# =============================================================================
with tab_gates:
    st.subheader("인제스천 품질 게이트 (15개, 5모듈)")
    st.caption("각 게이트의 통과/차단 비율을 확인합니다.")

    gates_result = api_client.get_pipeline_gates_stats()

    if api_failed(gates_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_gates"):
            st.cache_data.clear()
            st.rerun()
    else:
        gates = gates_result.get("gates", gates_result.get("checks", gates_result.get("items", [])))
        if gates:
            gate_names = []
            pass_counts = []
            block_counts = []

            for gate in gates:
                name = gate.get("name", gate.get("gate_id", "-"))
                passed = gate.get("passed", gate.get("total_passed", gate.get("pass_count", 0)))
                blocked = gate.get("blocked", gate.get("total_blocked", gate.get("block_count", 0)))
                gate_names.append(name)
                pass_counts.append(passed)
                block_counts.append(blocked)

            # 통과/차단 수평 바 차트
            fig_gates = go.Figure()
            fig_gates.add_trace(
                go.Bar(
                    y=gate_names,
                    x=pass_counts,
                    name="통과",
                    orientation="h",
                    marker_color="#28a745",
                )
            )
            fig_gates.add_trace(
                go.Bar(
                    y=gate_names,
                    x=block_counts,
                    name="차단",
                    orientation="h",
                    marker_color="#dc3545",
                )
            )
            fig_gates.update_layout(
                title="게이트별 통과/차단 비율",
                barmode="stack",
                xaxis_title="건수",
                yaxis_title="게이트",
                margin=dict(l=20, r=20, t=40, b=20),
                height=max(400, len(gate_names) * 35),
            )
            st.plotly_chart(fig_gates, use_container_width=True)

            # 차단 문서 상세
            st.markdown("---")
            st.subheader("차단 문서 상세")
            for gate in gates:
                gate_id = gate.get("gate_id", gate.get("id", ""))
                gate_name = gate.get("name", gate_id)
                blocked_count = gate.get("blocked", gate.get("total_blocked", gate.get("block_count", 0)))

                if blocked_count > 0:
                    with st.expander(f"🚫 {gate_name} - 차단 {blocked_count}건"):
                        if gate_id:
                            blocked_result = api_client.get_pipeline_gate_blocked(gate_id)
                            if not api_failed(blocked_result):
                                blocked_docs = blocked_result.get("documents", blocked_result.get("items", []))
                                if blocked_docs:
                                    for doc in blocked_docs[:10]:
                                        st.write(
                                            f"- **{doc.get('title', doc.get('document_id', '-'))}**: "
                                            f"{doc.get('reason', '-')}"
                                        )
                                else:
                                    st.caption("상세 정보 없음")
                            else:
                                st.caption("차단 문서 정보를 가져올 수 없습니다.")
        else:
            st.info("게이트 통계 데이터가 없습니다.")


# =============================================================================
# 탭 4: 스케줄
# =============================================================================
with tab_schedule:
    st.subheader("인제스천 스케줄 (Temporal Workflows)")

    schedules_result = api_client.list_ingestion_schedules()

    if api_failed(schedules_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_schedules"):
            st.cache_data.clear()
            st.rerun()
    else:
        schedules = schedules_result.get("items", schedules_result.get("schedules", []))
        if schedules:
            rows = []
            for sched in schedules:
                rows.append({
                    "스케줄 ID": sched.get("schedule_id", sched.get("id", "-")),
                    "이름": sched.get("name", "-"),
                    "Cron": sched.get("cron_expression", sched.get("cron", "-")),
                    "대상 KB": sched.get("kb_name", sched.get("kb_id", "-")),
                    "소스 타입": sched.get("source_type", "-"),
                    "상태": sched.get("status", "ACTIVE"),
                    "마지막 실행": sched.get("last_run_at", "-"),
                    "다음 실행": sched.get("next_run_at", "-"),
                })
            df_sched = pd.DataFrame(rows)

            status_map = {"ACTIVE": "🟢 활성", "PAUSED": "🟡 일시중지", "DISABLED": "⚪ 비활성"}
            df_sched["상태"] = df_sched["상태"].apply(lambda s: status_map.get(s, s))

            st.dataframe(df_sched, use_container_width=True, hide_index=True)
        else:
            st.info("등록된 스케줄이 없습니다.")
