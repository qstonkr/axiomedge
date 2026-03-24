"""RAG 평가

RAGAS/DeepEval 메트릭, SageMaker 실행 이력, Quality Gate.

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="RAG 평가", page_icon="🧪", layout="wide")


import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from components.sidebar import render_sidebar
from components.metric_cards import render_quality_metrics
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("🧪 RAG 평가")


# ---------------------------------------------------------------------------
# 평가 이력 조회
# ---------------------------------------------------------------------------
history_result = api_client.list_evaluation_history()

if api_failed(history_result):
    st.error("API 연결 실패")
    if st.button("🔄 재시도", key="retry_eval_history"):
        st.cache_data.clear()
        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# 새 평가 트리거
# ---------------------------------------------------------------------------
st.subheader("평가 실행")

with st.form("trigger_eval_form"):
    domain = st.selectbox(
        "평가 도메인",
        ["general", "policy", "code_error", "faq", "infrastructure"],
        index=0,
        key="eval_domain",
    )
    eval_engine = st.selectbox(
        "평가 엔진",
        ["ragas", "deepeval"],
        index=0,
        key="eval_engine",
        help="RAGAS: Faithfulness/Relevancy/Precision\nDeepEval: 50+ 메트릭",
    )
    submitted = st.form_submit_button("평가 시작", type="primary")

    if submitted:
        trigger_result = api_client.trigger_evaluation({
            "domain": domain,
            "engine": eval_engine,
        })
        if api_failed(trigger_result):
            st.error("평가 트리거 실패. 재시도해 주세요.")
        else:
            eval_id = trigger_result.get("job_name", trigger_result.get("evaluation_id", "-"))
            eval_status = trigger_result.get("status", "")
            if eval_status == "DRY_RUN":
                msg = trigger_result.get("message", "SageMaker 미설정")
                st.warning(f"DRY_RUN 모드: {msg}")
            else:
                st.success(f"평가가 시작되었습니다. (ID: {eval_id})")
            st.cache_data.clear()
            st.rerun()

st.markdown("---")


# ---------------------------------------------------------------------------
# Quality Gate (Faithfulness >= 0.65)
# ---------------------------------------------------------------------------
st.subheader("Quality Gate")
st.caption("Faithfulness >= 0.65 통과 기준")

evals = history_result.get("jobs", history_result.get("items", history_result.get("evaluations", [])))

if evals:
    # 최신 평가의 Quality Gate
    latest = evals[0] if evals else {}
    metrics = latest.get("metrics", {})

    faithfulness = metrics.get("faithfulness", 0)
    relevancy = metrics.get("answer_relevancy", metrics.get("relevancy", 0))
    precision = metrics.get("context_precision", metrics.get("precision", 0))
    overall = metrics.get("overall_score", None)

    gate_passed = faithfulness >= 0.65
    gate_color = "🟢" if gate_passed else "🔴"
    gate_text = "PASSED" if gate_passed else "FAILED"

    st.markdown(f"### {gate_color} Quality Gate: **{gate_text}**")

    render_quality_metrics(faithfulness, relevancy, precision, overall)

    st.markdown("---")


    # ---------------------------------------------------------------------------
    # 메트릭 추세 차트
    # ---------------------------------------------------------------------------
    st.subheader("메트릭 추세")

    trend_data = []
    for ev in reversed(evals[:20]):  # 최근 20건
        ev_metrics = ev.get("metrics", {})
        trend_data.append({
            "날짜": ev.get("completed_at", ev.get("created_at", "-"))[:10],
            "Faithfulness": ev_metrics.get("faithfulness", 0),
            "Relevancy": ev_metrics.get("answer_relevancy", ev_metrics.get("relevancy", 0)),
            "Overall": ev_metrics.get("overall_score", 0),
        })

    if trend_data:
        df_trend = pd.DataFrame(trend_data)
        fig_trend = px.line(
            df_trend,
            x="날짜",
            y=["Faithfulness", "Relevancy", "Overall"],
            title="평가 메트릭 추세",
            markers=True,
        )
        fig_trend.add_hline(
            y=0.65,
            line_dash="dash",
            line_color="red",
            annotation_text="Quality Gate (0.65)",
        )
        fig_trend.update_layout(margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_trend, use_container_width=True)

    st.markdown("---")


    # ---------------------------------------------------------------------------
    # 평가 이력 테이블
    # ---------------------------------------------------------------------------
    st.subheader("평가 실행 이력")

    rows = []
    for ev in evals:
        ev_metrics = ev.get("metrics", {})
        status = ev.get("status", "-")
        status_icons = {
            "Completed": "🟢", "COMPLETED": "🟢",
            "Running": "🔵", "RUNNING": "🔵",
            "Failed": "🔴", "FAILED": "🔴",
            "Pending": "🟡", "PENDING": "🟡",
        }
        status_display = f"{status_icons.get(status, '⚪')} {status}"

        faithfulness_val = ev_metrics.get("faithfulness", 0)
        gate = "PASS" if faithfulness_val >= 0.65 else "FAIL"
        gate_icon = "✅" if gate == "PASS" else "❌"

        rows.append({
            "Job": ev.get("job_name", ev.get("evaluation_id", "-")),
            "도메인": ev.get("domain", "-"),
            "상태": status_display,
            "Faithfulness": f"{faithfulness_val:.3f}",
            "Relevancy": f"{ev_metrics.get('answer_relevancy', ev_metrics.get('relevancy', 0)):.3f}",
            "Overall": f"{ev_metrics.get('overall_score', 0):.3f}",
            "Gate": f"{gate_icon} {gate}",
            "생성일": ev.get("created_at", "-")[:16],
        })

    df_history = pd.DataFrame(rows)
    st.dataframe(df_history, use_container_width=True, hide_index=True)


    # ---------------------------------------------------------------------------
    # SageMaker 실행 정보
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("SageMaker 실행 정보")
    st.caption("SageMaker Processing Job 기반 평가 (linux/amd64)")

    sm_info = [
        {"항목": "인스턴스 타입", "값": "ml.m5.xlarge"},
        {"항목": "실행 주기", "값": "매주 월요일 06:00 (KST)"},
        {"항목": "Spot 가격", "값": "~$0.067/hr"},
        {"항목": "월간 비용", "값": "~$0.07"},
        {"항목": "Temporal Schedule", "값": "sagemaker-eval-weekly"},
        {"항목": "Task Queue", "값": "ml-pipeline-queue"},
    ]
    st.dataframe(pd.DataFrame(sm_info), use_container_width=True, hide_index=True)

else:
    st.info("평가 이력이 없습니다. 위에서 새 평가를 시작하세요.")

st.markdown("---")
st.caption("📌 RAGAS (Faithfulness/Relevancy/Precision) + DeepEval (50+ metrics) | SSOT: oreo-agents infrastructure/evaluation/")
