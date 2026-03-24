"""피드백 관리 -- 피드백 워크플로우, KTS 반영, 기여자, 학습 루프 통합 페이지

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="피드백 관리", page_icon="💬", layout="wide")


import plotly.graph_objects as go

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("피드백 관리")
st.caption("피드백 워크플로우 관리, KTS 반영, 기여자 현황, 학습 루프를 운영합니다.")

tab_workflow, tab_kts, tab_contributors, tab_learning = st.tabs(
    ["피드백 워크플로우", "KTS 반영", "기여자", "학습 루프"]
)

# ============================================================================
# 1) 피드백 워크플로우
# ============================================================================
with tab_workflow:
    wf_data = api_client.get_feedback_workflow_stats()
    if api_failed(wf_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_wf"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("워크플로우 현황")

        # 8-state workflow
        WORKFLOW_STATES = [
            "SUBMITTED", "PENDING_OWNER_REVIEW", "OWNER_RESPONDED",
            "ESCALATED", "IN_PROGRESS", "COMPLETED", "REJECTED", "EXPIRED",
        ]
        STATE_LABELS = {
            "SUBMITTED": "제출됨",
            "PENDING_OWNER_REVIEW": "담당자 검토 대기",
            "OWNER_RESPONDED": "담당자 응답",
            "ESCALATED": "에스컬레이션",
            "IN_PROGRESS": "처리 중",
            "COMPLETED": "완료",
            "REJECTED": "반려",
            "EXPIRED": "만료",
        }
        STATE_COLORS = {
            "SUBMITTED": "#3498DB",
            "PENDING_OWNER_REVIEW": "#F39C12",
            "OWNER_RESPONDED": "#9B59B6",
            "ESCALATED": "#E74C3C",
            "IN_PROGRESS": "#F39C12",
            "COMPLETED": "#2ECC71",
            "REJECTED": "#E74C3C",
            "EXPIRED": "#95A5A6",
        }

        state_dist = wf_data.get("state_distribution", {})

        # Backend may return flat metrics instead of state_distribution
        # Reconstruct distribution from flat fields if needed
        if not state_dist:
            total_fb = wf_data.get("total_feedback", wf_data.get("total", 0))
            pending = wf_data.get("pending_review", 0)
            resolved = wf_data.get("resolved", wf_data.get("completed", 0))
            rejected = wf_data.get("rejected", 0)
            in_progress = wf_data.get("in_progress", 0)
            if total_fb > 0:
                submitted = max(0, total_fb - pending - resolved - rejected - in_progress)
                state_dist = {
                    "SUBMITTED": submitted,
                    "PENDING_OWNER_REVIEW": pending,
                    "IN_PROGRESS": in_progress,
                    "COMPLETED": resolved,
                    "REJECTED": rejected,
                }

        if state_dist:
            labels = [STATE_LABELS.get(s, s) for s in WORKFLOW_STATES]
            values = [state_dist.get(s, 0) for s in WORKFLOW_STATES]
            colors = [STATE_COLORS.get(s, "#999") for s in WORKFLOW_STATES]

            fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors))
            fig.update_layout(
                title="워크플로우 상태 분포",
                xaxis_title="상태", yaxis_title="건수",
                height=350, margin=dict(l=20, r=20, t=40, b=80),
                xaxis_tickangle=-45,
            )
            st.plotly_chart(fig, use_container_width=True)

        # Workflow diagram
        st.markdown("#### 워크플로우 상태 전이")
        st.markdown("""```mermaid
stateDiagram-v2
    [*] --> SUBMITTED
    SUBMITTED --> PENDING_OWNER_REVIEW : 자동 배정
    PENDING_OWNER_REVIEW --> OWNER_RESPONDED : 담당자 응답
    PENDING_OWNER_REVIEW --> ESCALATED : 기한 초과
    OWNER_RESPONDED --> IN_PROGRESS : 수정 시작
    OWNER_RESPONDED --> REJECTED : 반려
    ESCALATED --> IN_PROGRESS : 관리자 배정
    IN_PROGRESS --> COMPLETED : 수정 완료
    IN_PROGRESS --> REJECTED : 반려
    PENDING_OWNER_REVIEW --> EXPIRED : 장기 미응답
    COMPLETED --> [*]
    REJECTED --> [*]
    EXPIRED --> [*]
```""")

        # Escalation level distribution
        st.markdown("---")
        st.subheader("에스컬레이션 레벨 분포")
        ESCALATION_LEVELS = {
            "L1": {"label": "L1 (담당자)", "color": "#2ECC71"},
            "L2": {"label": "L2 (팀 리더)", "color": "#F39C12"},
            "L3": {"label": "L3 (관리자)", "color": "#E74C3C"},
        }

        esc_dist = wf_data.get("escalation_distribution", {})
        if esc_dist:
            esc_cols = st.columns(3)
            for i, (level, info) in enumerate(ESCALATION_LEVELS.items()):
                with esc_cols[i]:
                    count = esc_dist.get(level, 0)
                    st.metric(info["label"], f"{count}건")
        else:
            st.info("에스컬레이션 데이터가 없습니다.")

        # Feedback list with status filter
        st.markdown("---")
        st.subheader("피드백 목록")
        status_filter = st.selectbox(
            "상태 필터",
            ["전체"] + WORKFLOW_STATES,
            format_func=lambda x: "전체" if x == "전체" else STATE_LABELS.get(x, x),
            key="wf_status",
        )

        status_param = None if status_filter == "전체" else status_filter

        FB_PAGE_SIZE = 20
        fb_page = st.number_input("페이지", min_value=1, value=1, key="fb_page")

        fb_data = api_client.list_feedback(status=status_param, page=fb_page, page_size=FB_PAGE_SIZE)
        if api_failed(fb_data):
            st.error("API 연결 실패")
            if st.button("재시도", key="retry_wf_list"):
                st.cache_data.clear()
                st.rerun()
        else:
            fb_items = fb_data.get("items", [])
            fb_total = fb_data.get("total", len(fb_items))
            fb_total_pages = max(1, (fb_total + FB_PAGE_SIZE - 1) // FB_PAGE_SIZE)

            st.caption(f"총 {fb_total:,}건 (페이지 {fb_page}/{fb_total_pages})")

            if fb_items:
                for fb in fb_items:
                    fb_status = fb.get("status", "SUBMITTED")
                    fb_type = fb.get("feedback_type", "")
                    fb_content = fb.get("content", "")[:100]
                    fb_created = fb.get("created_at", "")[:16]
                    s_color = STATE_COLORS.get(fb_status, "#999")
                    st.markdown(
                        f"- **[{STATE_LABELS.get(fb_status, fb_status)}]** "
                        f"`{fb_type}` - {fb_content}... ({fb_created})"
                    )
            else:
                st.info("해당 상태의 피드백이 없습니다.")

        # Summary metrics
        st.markdown("---")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            total_fb = wf_data.get("total", wf_data.get("total_feedback", 0))
            st.metric("전체 피드백", f"{total_fb:,}건")
        with m2:
            avg_hours = wf_data.get("avg_resolution_hours", 0)
            st.metric("평균 처리 시간", f"{avg_hours:.1f}시간" if avg_hours else "-")
        with m3:
            comp_rate = wf_data.get("completion_rate", wf_data.get("positive_rate", 0))
            st.metric("처리율", f"{comp_rate:.1%}" if comp_rate else "-")
        with m4:
            esc_rate = wf_data.get("escalation_rate", 0)
            st.metric("에스컬레이션율", f"{esc_rate:.1%}" if esc_rate else "-")

# ============================================================================
# 2) KTS 반영
# ============================================================================
with tab_kts:
    st.subheader("Vote → KTS 자동 반영")

    st.markdown("""
    피드백 투표(Upvote/Downvote)는 KTS(Knowledge Trust Score)에 자동 반영됩니다.

    #### 반영 규칙
    | 피드백 유형 | KTS 영향 | 가중치 |
    |------------|---------|--------|
    | Upvote | `usage_feedback` 점수 상승 | +0.01/vote |
    | Downvote | `usage_feedback` 점수 하락 | -0.02/vote |
    | 수정 제안 (반영됨) | `accuracy` 점수 상승 | +0.05 |
    | 오류 신고 (확인됨) | `accuracy` 점수 하락 후 수정 시 회복 | -0.10 → +0.05 |
    | 전문가 검증 | `expert_validation` 점수 상승 | +0.10 |
    """)

    # Vote → KTS stats
    fb_stats = api_client.get_feedback_stats()
    if api_failed(fb_stats):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_kts_fb"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.markdown("---")
        st.markdown("#### 투표 현황")
        v1, v2, v3 = st.columns(3)
        with v1:
            upvotes = fb_stats.get("total_upvotes", fb_stats.get("positive_count", 0))
            st.metric("총 Upvote", f"{upvotes:,}건")
        with v2:
            downvotes = fb_stats.get("total_downvotes", fb_stats.get("negative_count", 0))
            st.metric("총 Downvote", f"{downvotes:,}건")
        with v3:
            positive_rate = fb_stats.get("positive_rate")
            if positive_rate is not None:
                st.metric("긍정 비율", f"{positive_rate:.1%}")
            else:
                total_votes = upvotes + downvotes
                if total_votes > 0:
                    st.metric("긍정 비율", f"{upvotes / total_votes:.1%}")
                else:
                    st.metric("긍정 비율", "-")

        st.markdown("---")
        st.markdown("#### KTS 반영 현황")
        kts_applied = fb_stats.get("kts_applied_count", 0)
        kts_pending = fb_stats.get("kts_pending_count", 0)
        total_count = fb_stats.get("total_count", 0)
        k1, k2 = st.columns(2)
        with k1:
            st.metric("KTS 반영 완료", f"{kts_applied:,}건" if kts_applied else f"{total_count:,}건 (전체)")
        with k2:
            st.metric("KTS 반영 대기", f"{kts_pending:,}건")

# ============================================================================
# 3) 기여자
# ============================================================================
with tab_contributors:
    contrib_data = api_client.list_contributors()
    if api_failed(contrib_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_contrib"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("기여자 현황")

        # ContributorReputation: 4 ranks, 5 badges
        REPUTATION_RANKS = {
            "NOVICE": {"label": "초보", "icon": "🌱", "color": "#95A5A6"},
            "CONTRIBUTOR": {"label": "기여자", "icon": "🌿", "color": "#3498DB"},
            "REVIEWER": {"label": "리뷰어", "icon": "🌳", "color": "#F39C12"},
            "EXPERT": {"label": "전문가", "icon": "🏆", "color": "#2ECC71"},
        }

        BADGES = ["첫 피드백", "10회 기여", "정확한 수정", "지식 수호자", "최다 기여자"]

        contributors = contrib_data.get("items", [])
        if contributors:
            # Rank distribution
            rank_counts = {}
            for c in contributors:
                rank = c.get("rank", "NOVICE")
                rank_counts[rank] = rank_counts.get(rank, 0) + 1

            rank_cols = st.columns(4)
            for i, (rank, info) in enumerate(REPUTATION_RANKS.items()):
                with rank_cols[i]:
                    st.metric(
                        f"{info['icon']} {info['label']}",
                        f"{rank_counts.get(rank, 0)}명",
                    )

            # Contributor table
            st.markdown("---")
            st.markdown("#### 기여자 목록")
            import pandas as pd

            rows = []
            for c in contributors:
                rank = c.get("rank", "NOVICE")
                rank_info = REPUTATION_RANKS.get(rank, {"icon": "?", "label": rank})
                rows.append({
                    "이름": c.get("name", c.get("user_id", "")),
                    "등급": f"{rank_info['icon']} {rank_info['label']}",
                    "기여 수": c.get("contribution_count", c.get("total_contributions", 0)),
                    "점수": c.get("reputation_score", 0),
                    "뱃지": ", ".join(c.get("badges", [])),
                    "투표 가능": "O" if c.get("can_vote", False) else "X",
                    "리뷰 가능": "O" if c.get("can_review", False) else "X",
                    "KTS 재정의": "O" if c.get("can_override_kts", False) else "X",
                })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Permission legend
            st.markdown("---")
            st.markdown("#### 권한 안내")
            st.markdown("""
            | 권한 | 설명 | 필요 등급 |
            |-----|------|----------|
            | `can_vote` | 피드백 투표 | NOVICE+ |
            | `can_review` | 피드백 리뷰/승인 | REVIEWER+ |
            | `can_override_kts` | KTS 점수 수동 조정 | EXPERT |
            """)
        else:
            st.info("기여자 데이터가 없습니다.")

# ============================================================================
# 4) 학습 루프
# ============================================================================
with tab_learning:
    learn_data = api_client.get_learning_artifacts()
    if api_failed(learn_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_learn"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.subheader("학습 산출물")
        st.markdown("피드백에서 추출된 학습 패턴과 KB 반영 현황입니다.")

        artifacts = learn_data.get("items", [])
        if artifacts:
            # Summary
            total_artifacts = learn_data.get("total", len(artifacts))
            reflected = sum(1 for a in artifacts if a.get("reflected", False))

            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("학습 산출물", f"{total_artifacts}건")
            with m2:
                st.metric("KB 반영 완료", f"{reflected}건")
            with m3:
                rate = reflected / max(total_artifacts, 1)
                st.metric("반영률", f"{rate:.1%}")

            # Artifact list
            st.markdown("---")
            for artifact in artifacts:
                art_id = artifact.get("id", "")
                art_type = artifact.get("type", "")
                art_title = artifact.get("title", "학습 패턴")
                art_desc = artifact.get("description", "")
                art_reflected = artifact.get("reflected", False)
                art_kb_id = artifact.get("target_kb_id", "")
                art_created = artifact.get("created_at", "")

                status_icon = "✅" if art_reflected else "⏳"
                status_text = "KB 반영 완료" if art_reflected else "반영 대기"

                with st.expander(f"{status_icon} {art_title} ({art_created[:10]})"):
                    st.markdown(f"**유형**: {art_type}")
                    st.markdown(f"**설명**: {art_desc}")
                    st.markdown(f"**상태**: {status_text}")
                    if art_kb_id:
                        st.markdown(f"**대상 KB**: `{art_kb_id}`")

                    source_feedbacks = artifact.get("source_feedback_ids", [])
                    if source_feedbacks:
                        st.markdown(f"**출처 피드백**: {len(source_feedbacks)}건")
        else:
            st.info("학습 산출물이 없습니다.")

        # Learning loop diagram
        st.markdown("---")
        st.subheader("학습 루프 프로세스")
        st.markdown("""
        ```
        [피드백 수집] → [패턴 분석] → [학습 산출물 생성] → [KB 반영] → [품질 개선]
              ↑                                                           |
              └───────────────── 지속적 개선 ──────────────────────────────┘
        ```

        1. **피드백 수집**: 사용자 투표, 수정 제안, 오류 신고
        2. **패턴 분석**: 반복적 피드백에서 학습 패턴 추출
        3. **산출물 생성**: 개선 항목, 새 문서 초안, 수정 제안
        4. **KB 반영**: 검증 후 Knowledge Base에 자동/수동 반영
        5. **품질 개선**: KTS 점수 변화 모니터링
        """)
