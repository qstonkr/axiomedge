"""그래프 무결성 모니터링

6개 무결성 서브시스템 상태 대시보드:
- PersonIdentityResolver
- CrossDocumentConflictDetector
- CardinalityValidator
- DocumentGraphSync
- OrphanNodeCleaner
- TemporalValidityManager

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="그래프 무결성", page_icon="🛡️", layout="wide")


import plotly.graph_objects as go

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("🛡️ 그래프 무결성")

# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------
integrity_result = api_client.get_graph_integrity()

if api_failed(integrity_result):
    st.error("API 연결 실패")
    if st.button("🔄 재시도", key="retry_integrity"):
        st.cache_data.clear()
        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# 전체 무결성 점수 게이지
# ---------------------------------------------------------------------------
overall_score = integrity_result.get("overall_score", 0)
total_issues = integrity_result.get("total_issues", 0)
last_checked = integrity_result.get("last_checked", "-")

col_gauge, col_summary = st.columns([1, 2])

with col_gauge:
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=overall_score * 100 if isinstance(overall_score, float) and overall_score <= 1 else overall_score,
        title={"text": "무결성 점수"},
        delta={"reference": 95, "increasing": {"color": "green"}, "decreasing": {"color": "red"}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#4CAF50" if overall_score >= 0.9 else "#FF9800" if overall_score >= 0.7 else "#F44336"},
            "steps": [
                {"range": [0, 70], "color": "#FFCDD2"},
                {"range": [70, 90], "color": "#FFF9C4"},
                {"range": [90, 100], "color": "#C8E6C9"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 4},
                "thickness": 0.75,
                "value": 90,
            },
        },
    ))
    fig.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=300)
    st.plotly_chart(fig, use_container_width=True)

with col_summary:
    st.subheader("요약")
    mcol1, mcol2, mcol3 = st.columns(3)
    with mcol1:
        st.metric("총 이슈", f"{total_issues}건")
    with mcol2:
        st.metric("마지막 점검", str(last_checked)[:16] if last_checked != "-" else "-")
    with mcol3:
        status_label = "정상" if overall_score >= 0.9 else "주의" if overall_score >= 0.7 else "경고"
        status_color = "🟢" if overall_score >= 0.9 else "🟡" if overall_score >= 0.7 else "🔴"
        st.metric("상태", f"{status_color} {status_label}")

    # 점검 실행 버튼
    if st.button("🔍 무결성 점검 실행", type="primary", key="run_integrity"):
        with st.spinner("무결성 점검 실행 중..."):
            run_result = api_client.run_graph_integrity_check()
            if api_failed(run_result):
                st.error("무결성 점검 실행 실패. 재시도해 주세요.")
            else:
                st.success("무결성 점검이 시작되었습니다.")
                st.cache_data.clear()
                st.rerun()

st.markdown("---")


# ---------------------------------------------------------------------------
# 6개 서브시스템 상태
# ---------------------------------------------------------------------------
SUBSYSTEMS = [
    {
        "key": "person_identity_resolver",
        "name": "PersonIdentityResolver",
        "description": "동일 인물의 다양한 표기를 통합 (예: 김철수, chulsoo.kim, C. Kim)",
        "icon": "👤",
    },
    {
        "key": "cross_document_conflict_detector",
        "name": "CrossDocumentConflictDetector",
        "description": "서로 다른 문서 간 모순되는 정보 탐지",
        "icon": "⚔️",
    },
    {
        "key": "cardinality_validator",
        "name": "CardinalityValidator",
        "description": "관계의 다중성 제약 조건 검증 (1:1, 1:N, N:M)",
        "icon": "🔢",
    },
    {
        "key": "document_graph_sync",
        "name": "DocumentGraphSync",
        "description": "원본 문서와 그래프 노드 간 동기화 상태 확인",
        "icon": "🔄",
    },
    {
        "key": "orphan_node_cleaner",
        "name": "OrphanNodeCleaner",
        "description": "관계가 없는 고립 노드 탐지 및 정리",
        "icon": "🧹",
    },
    {
        "key": "temporal_validity_manager",
        "name": "TemporalValidityManager",
        "description": "시간 기반 유효성 검증 (만료된 정책, 퇴사자 등)",
        "icon": "⏰",
    },
]

st.subheader("서브시스템 상태")

subsystems_data = integrity_result.get("subsystems", {})

# 2열로 서브시스템 카드 배치
for row_start in range(0, len(SUBSYSTEMS), 2):
    cols = st.columns(2)
    for col_idx, sub_idx in enumerate(range(row_start, min(row_start + 2, len(SUBSYSTEMS)))):
        sub_def = SUBSYSTEMS[sub_idx]
        sub_data = subsystems_data.get(sub_def["key"], {})

        status = sub_data.get("status", "UNKNOWN")
        issue_count = sub_data.get("issue_count", 0)
        last_run = sub_data.get("last_run", sub_data.get("last_run_at", "-"))

        # 상태 배지
        status_badges = {
            "OK": ("🟢", "정상"),
            "WARNING": ("🟡", "주의"),
            "ERROR": ("🔴", "오류"),
            "UNKNOWN": ("⚪", "미확인"),
            "RUNNING": ("🔵", "실행 중"),
        }
        badge_icon, badge_text = status_badges.get(status.upper(), ("⚪", status))

        with cols[col_idx]:
            with st.container(border=True):
                st.markdown(f"### {sub_def['icon']} {sub_def['name']}")
                st.caption(sub_def["description"])

                m1, m2, m3 = st.columns(3)
                with m1:
                    st.markdown(f"**상태:** {badge_icon} {badge_text}")
                with m2:
                    st.markdown(f"**이슈:** {issue_count}건")
                with m3:
                    last_run_display = str(last_run)[:16] if last_run != "-" else "-"
                    st.markdown(f"**마지막 실행:** {last_run_display}")

                # 이슈 목록
                issues = sub_data.get("issues", [])
                if issues:
                    with st.expander(f"이슈 상세 ({len(issues)}건)"):
                        for issue in issues[:10]:
                            severity = issue.get("severity", "INFO")
                            sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}.get(severity, "⚪")
                            desc = issue.get("description", issue.get("message", "-"))
                            node_id = issue.get("node_id", "")
                            st.markdown(f"- {sev_icon} **{severity}**: {desc}")
                            if node_id:
                                st.caption(f"  노드: `{node_id}`")

                            # 해결 액션
                            action = issue.get("resolution_action", "")
                            if action:
                                issue_id = issue.get("id", issue.get("issue_id", ""))
                                if st.button(
                                    f"해결: {action}",
                                    key=f"resolve_{sub_def['key']}_{issue_id}_{desc[:20]}",
                                    type="secondary",
                                ):
                                    st.info(f"해결 액션 실행: {action}")


st.markdown("---")
st.caption("📌 그래프 무결성 = 6개 서브시스템 점수의 가중 평균 | SSOT: oreo-agents domain/knowledge/graph_integrity/")
