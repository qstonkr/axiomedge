"""내 피드백 -- 사용자 본인의 피드백 조회 및 제출

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="내 피드백", page_icon="📝", layout="wide")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("내 피드백")
st.caption("내가 제출한 피드백 이력과 처리 현황을 확인합니다.")

# ---------------------------------------------------------------------------
# FeedbackType 5종
# ---------------------------------------------------------------------------
FEEDBACK_TYPES = {
    "UPVOTE": {"label": "좋아요", "icon": "👍"},
    "DOWNVOTE": {"label": "싫어요", "icon": "👎"},
    "CORRECTION": {"label": "수정 제안", "icon": "✏️"},
    "ERROR_REPORT": {"label": "오류 신고", "icon": "🚨"},
    "SUGGESTION": {"label": "개선 제안", "icon": "💡"},
}

FEEDBACK_STATUSES = {
    "SUBMITTED": {"label": "제출됨", "color": "blue"},
    "PENDING_REVIEW": {"label": "검토 대기", "color": "orange"},
    "IN_PROGRESS": {"label": "처리 중", "color": "orange"},
    "COMPLETED": {"label": "처리 완료", "color": "green"},
    "REJECTED": {"label": "반려", "color": "red"},
}

# ---------------------------------------------------------------------------
# 피드백 제출 폼
# ---------------------------------------------------------------------------
st.subheader("새 피드백 제출")

with st.form("feedback_form"):
    col1, col2 = st.columns(2)
    with col1:
        feedback_type = st.selectbox(
            "피드백 유형",
            list(FEEDBACK_TYPES.keys()),
            format_func=lambda x: f"{FEEDBACK_TYPES[x]['icon']} {FEEDBACK_TYPES[x]['label']}",
        )
    with col2:
        document_id = st.text_input("관련 문서 ID", placeholder="문서 ID (선택)")

    content = st.text_area(
        "피드백 내용",
        placeholder="피드백을 작성해주세요.\n예: 이 문서의 3번 섹션에서 명령어가 잘못되어 있습니다.",
        height=120,
    )

    submitted = st.form_submit_button("피드백 제출", type="primary")

    if submitted:
        if not content and feedback_type not in ("UPVOTE", "DOWNVOTE"):
            st.warning("피드백 내용을 입력하세요.")
        else:
            body = {
                "feedback_type": feedback_type,
                "content": content,
            }
            if document_id:
                body["document_id"] = document_id

            result = api_client.create_feedback(body)
            if api_failed(result):
                st.error("API 연결 실패")
                if st.button("재시도", key="retry_create_fb"):
                    st.rerun()
            else:
                st.success("피드백이 제출되었습니다.")
                st.cache_data.clear()
                st.rerun()

# ---------------------------------------------------------------------------
# 내 피드백 이력
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("피드백 이력")

# 필터
filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    type_filter = st.selectbox(
        "유형 필터",
        ["전체"] + list(FEEDBACK_TYPES.keys()),
        format_func=lambda x: "전체" if x == "전체" else f"{FEEDBACK_TYPES[x]['icon']} {FEEDBACK_TYPES[x]['label']}",
        key="fb_type_filter",
    )
with filter_col2:
    status_filter = st.selectbox(
        "상태 필터",
        ["전체"] + list(FEEDBACK_STATUSES.keys()),
        format_func=lambda x: "전체" if x == "전체" else FEEDBACK_STATUSES[x]["label"],
        key="fb_status_filter",
    )
with filter_col3:
    page = st.number_input("페이지", min_value=1, value=1, key="fb_page")

type_param = None if type_filter == "전체" else type_filter
status_param = None if status_filter == "전체" else status_filter

data = api_client.list_feedback(
    feedback_type=type_param,
    status=status_param,
    page=page,
)

if api_failed(data):
    st.error("API 연결 실패")
    if st.button("재시도", key="retry_fb_list"):
        st.cache_data.clear()
        st.rerun()
else:
    items = data.get("items", [])
    total = data.get("total", 0)

    st.markdown(f"**총 {total:,}건**")

    if items:
        for fb in items:
            fb_id = fb.get("id", fb.get("feedback_id", ""))
            fb_type = fb.get("feedback_type", "SUGGESTION")
            fb_status = fb.get("status", "SUBMITTED")
            fb_content = fb.get("content", "")
            fb_created = fb.get("created_at", "")
            fb_doc_id = fb.get("document_id", "")
            fb_response = fb.get("response", "")

            type_info = FEEDBACK_TYPES.get(fb_type, {"label": fb_type, "icon": "📌"})
            status_info = FEEDBACK_STATUSES.get(fb_status, {"label": fb_status, "color": "gray"})

            with st.expander(
                f"{type_info['icon']} {type_info['label']} - "
                f":{status_info['color']}[{status_info['label']}] "
                f"({fb_created[:10]})"
            ):
                st.markdown(f"**내용**: {fb_content}")
                if fb_doc_id:
                    st.markdown(f"**관련 문서**: `{fb_doc_id}`")
                st.markdown(f"**상태**: :{status_info['color']}[{status_info['label']}]")
                st.markdown(f"**제출일**: {fb_created[:16]}")

                if fb_response:
                    st.markdown("---")
                    st.markdown(f"**관리자 답변**: {fb_response}")

                updated = fb.get("updated_at", "")
                if updated:
                    st.caption(f"최종 수정: {updated[:16]}")

        # ---------------------------------------------------------------------------
        # 요약 통계
        # ---------------------------------------------------------------------------
        st.markdown("---")
        st.subheader("제출 통계")
        type_counts = {}
        status_counts = {}
        for fb in items:
            ft = fb.get("feedback_type", "OTHER")
            fs = fb.get("status", "SUBMITTED")
            type_counts[ft] = type_counts.get(ft, 0) + 1
            status_counts[fs] = status_counts.get(fs, 0) + 1

        tcols = st.columns(len(FEEDBACK_TYPES))
        for i, (ftype, info) in enumerate(FEEDBACK_TYPES.items()):
            with tcols[i]:
                st.metric(f"{info['icon']} {info['label']}", f"{type_counts.get(ftype, 0)}건")

        scols = st.columns(len(FEEDBACK_STATUSES))
        for i, (fstatus, info) in enumerate(FEEDBACK_STATUSES.items()):
            with scols[i]:
                st.metric(info["label"], f"{status_counts.get(fstatus, 0)}건")
    else:
        st.info("제출한 피드백이 없습니다.")
