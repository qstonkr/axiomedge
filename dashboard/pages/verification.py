"""검증 관리 -- 문서 검증 큐, 투표, 리뷰어 배정

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="검증 관리", page_icon="✅", layout="wide")


import plotly.graph_objects as go

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("검증 관리")
st.caption("문서 검증 대기 큐를 관리하고, 검증 투표를 수행합니다.")

# ---------------------------------------------------------------------------
# VerificationStatus 5종
# ---------------------------------------------------------------------------
VERIFICATION_STATUSES = {
    "UNVERIFIED": {"label": "미검증", "color": "#95A5A6", "icon": "⚪"},
    "PENDING": {"label": "검증 대기", "color": "#F39C12", "icon": "🟡"},
    "IN_REVIEW": {"label": "검토 중", "color": "#3498DB", "icon": "🔵"},
    "VERIFIED": {"label": "검증 완료", "color": "#2ECC71", "icon": "🟢"},
    "REJECTED": {"label": "검증 실패", "color": "#E74C3C", "icon": "🔴"},
}

# ---------------------------------------------------------------------------
# 검증 큐 메트릭
# ---------------------------------------------------------------------------
pending_data = api_client.get_verification_pending(page=1, page_size=50)
if api_failed(pending_data):
    st.error("API 연결 실패")
    if st.button("재시도", key="retry_vf"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

pending_items = pending_data.get("items", [])
total_pending = pending_data.get("total", len(pending_items))

# Summary metrics
st.subheader("검증 큐 현황")
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("대기 문서", f"{total_pending}건")
with m2:
    # Count by status
    status_counts = {}
    for item in pending_items:
        s = item.get("verification_status", item.get("status", "UNVERIFIED"))
        status_counts[s] = status_counts.get(s, 0) + 1
    in_review = status_counts.get("IN_REVIEW", 0)
    st.metric("검토 중", f"{in_review}건")
with m3:
    # Compute avg wait from items' created_at if aggregate not provided
    avg_wait = pending_data.get("avg_wait_hours")
    if avg_wait is not None:
        st.metric("평균 대기 시간", f"{avg_wait:.1f}시간")
    else:
        st.metric("대기 항목", f"{len(pending_items)}건")
with m4:
    reviewers = pending_data.get("active_reviewers")
    if reviewers is not None:
        st.metric("활성 리뷰어", f"{reviewers}명")
    else:
        # Count unique reviewers from items
        reviewer_set = {item.get("assigned_reviewer") for item in pending_items if item.get("assigned_reviewer")}
        st.metric("배정 리뷰어", f"{len(reviewer_set)}명")

# ---------------------------------------------------------------------------
# Workflow diagram
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("검증 워크플로우")
st.markdown("""```mermaid
stateDiagram-v2
    [*] --> UNVERIFIED
    UNVERIFIED --> PENDING : 검증 요청
    PENDING --> IN_REVIEW : 리뷰어 배정
    IN_REVIEW --> VERIFIED : 투표 통과
    IN_REVIEW --> REJECTED : 투표 실패
    REJECTED --> PENDING : 재검증 요청
    VERIFIED --> [*]
```""")

# Status distribution chart
if status_counts:
    st.markdown("---")
    st.subheader("검증 상태 분포")
    labels = []
    values = []
    colors = []
    for status_key, info in VERIFICATION_STATUSES.items():
        count = status_counts.get(status_key, 0)
        if count > 0:
            labels.append(info["label"])
            values.append(count)
            colors.append(info["color"])

    if labels:
        fig = go.Figure(
            go.Pie(
                labels=labels, values=values,
                marker=dict(colors=colors),
                textinfo="label+percent+value",
                hole=0.3,
            )
        )
        fig.update_layout(title="상태 분포", height=350)
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# 검증 대기 문서 목록
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("검증 대기 문서")

if pending_items:
    for item in pending_items:
        doc_id = item.get("document_id", item.get("id", ""))
        doc_title = item.get("title", item.get("query", doc_id))
        v_status = item.get("verification_status", item.get("status", "UNVERIFIED"))
        v_info = VERIFICATION_STATUSES.get(v_status, {"label": v_status, "icon": "?", "color": "gray"})
        created = item.get("created_at", "")
        kb_id = item.get("kb_id", "")
        votes_for = item.get("votes_for", 0)
        votes_against = item.get("votes_against", 0)
        required_votes = item.get("required_votes", 3)
        assigned_reviewer = item.get("assigned_reviewer", "")

        with st.expander(f"{v_info['icon']} {doc_title} - {v_info['label']}"):
            # Document info
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(f"**문서 ID**: `{doc_id}`")
                if kb_id:
                    st.markdown(f"**KB**: `{kb_id}`")
            with c2:
                st.markdown(f"**상태**: :{v_info['color']}[{v_info['label']}]")
                st.markdown(f"**등록일**: {created[:16]}")
            with c3:
                st.markdown(f"**찬성 투표**: {votes_for}/{required_votes}")
                st.markdown(f"**반대 투표**: {votes_against}")
                if assigned_reviewer:
                    st.markdown(f"**배정 리뷰어**: {assigned_reviewer}")

            # Vote progress bar
            total_votes = votes_for + votes_against
            if required_votes > 0:
                progress = min(votes_for / required_votes, 1.0)
                st.progress(progress, text=f"투표 진행률: {votes_for}/{required_votes}")

            # Vote submission form
            st.markdown("---")
            st.markdown("**투표하기**")
            vote_col1, vote_col2, vote_col3 = st.columns([2, 1, 1])

            with vote_col1:
                vote_comment = st.text_input(
                    "코멘트 (선택)",
                    placeholder="검증 의견을 입력하세요",
                    key=f"comment_{doc_id}",
                )
            with vote_col2:
                if st.button("찬성", key=f"approve_{doc_id}", type="primary"):
                    result = api_client.submit_verification_vote(
                        doc_id,
                        {"vote": "APPROVE", "comment": vote_comment},
                    )
                    if api_failed(result):
                        st.error("투표 실패: API 연결 실패")
                    else:
                        st.success("찬성 투표가 등록되었습니다.")
                        st.cache_data.clear()
                        st.rerun()
            with vote_col3:
                if st.button("반대", key=f"reject_{doc_id}"):
                    if not vote_comment:
                        st.warning("반대 시 사유를 입력해주세요.")
                    else:
                        result = api_client.submit_verification_vote(
                            doc_id,
                            {"vote": "REJECT", "comment": vote_comment},
                        )
                        if api_failed(result):
                            st.error("투표 실패: API 연결 실패")
                        else:
                            st.success("반대 투표가 등록되었습니다.")
                            st.cache_data.clear()
                            st.rerun()

            # Vote history
            vote_history = item.get("vote_history", [])
            if vote_history:
                st.markdown("**투표 이력:**")
                for vote in vote_history:
                    v_type = vote.get("vote", "")
                    v_user = vote.get("user_id", "")
                    v_time = vote.get("timestamp", "")
                    v_comment = vote.get("comment", "")
                    v_icon = "👍" if v_type == "APPROVE" else "👎"
                    st.markdown(f"{v_icon} **{v_user}** ({v_time[:16]}) - {v_comment}")
else:
    st.info("검증 대기 중인 문서가 없습니다.")

# ---------------------------------------------------------------------------
# 리뷰어 배정 현황
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("리뷰어 배정 현황")

reviewer_stats = pending_data.get("reviewer_stats", [])
if reviewer_stats:
    import pandas as pd

    rows = []
    for r in reviewer_stats:
        rows.append({
            "리뷰어": r.get("name", r.get("user_id", "")),
            "배정 건수": r.get("assigned_count", 0),
            "완료 건수": r.get("completed_count", 0),
            "평균 소요시간": f"{r.get('avg_review_hours', 0):.1f}시간",
            "상태": "활성" if r.get("is_active", False) else "비활성",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    # Compute reviewer summary from items
    reviewer_map: dict[str, int] = {}
    for item in pending_items:
        reviewer = item.get("assigned_reviewer")
        if reviewer:
            reviewer_map[reviewer] = reviewer_map.get(reviewer, 0) + 1
    if reviewer_map:
        import pandas as pd

        rows = [{"리뷰어": name, "배정 건수": count} for name, count in reviewer_map.items()]
        df = pd.DataFrame(rows).sort_values("배정 건수", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("리뷰어 배정 데이터가 없습니다.")
