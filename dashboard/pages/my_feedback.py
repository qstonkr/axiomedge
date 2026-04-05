"""내 피드백/오류 신고 -- 피드백 제출 및 오류 리포트 (탭 통합)

Created: 2026-02-20
Updated: 2026-04-04  — 오류 신고 탭 통합
"""

import streamlit as st

st.set_page_config(page_title="피드백/오류 신고", page_icon="📝", layout="wide")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed
from services.validators import sanitize_input

render_sidebar()

st.title("피드백/오류 신고")

# ===========================================================================
# Tab structure
# ===========================================================================
tab_feedback, tab_error = st.tabs(["📝 피드백", "🚨 오류 신고"])

# ===========================================================================
# Tab 1: 피드백
# ===========================================================================
with tab_feedback:
    st.caption("내가 제출한 피드백 이력과 처리 현황을 확인합니다.")

    # -----------------------------------------------------------------------
    # FeedbackType 5종
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # 피드백 제출 폼
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # 내 피드백 이력
    # -----------------------------------------------------------------------
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
        items = data.get("items", data.get("feedback", []))
        total = data.get("total", 0)

        st.markdown(f"**총 {total:,}건**")

        if items:
            for fb in items:
                fb_id = fb.get("id", fb.get("feedback_id", ""))  # noqa: F841
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

            # ---------------------------------------------------------------
            # 요약 통계
            # ---------------------------------------------------------------
            st.markdown("---")
            st.subheader("제출 통계")
            type_counts: dict[str, int] = {}
            status_counts: dict[str, int] = {}
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

# ===========================================================================
# Tab 2: 오류 신고
# ===========================================================================
with tab_error:
    st.caption("문서 오류를 신고하고 처리 현황을 확인합니다.")

    # -----------------------------------------------------------------------
    # ErrorType 7종, ErrorPriority 4종, ErrorStatus 5종
    # -----------------------------------------------------------------------
    ERROR_TYPES = [
        "INACCURATE", "OUTDATED", "INCOMPLETE", "DUPLICATE",
        "BROKEN_LINK", "FORMATTING", "OTHER",
    ]
    ERROR_TYPE_LABELS = {
        "INACCURATE": "부정확한 내용",
        "OUTDATED": "오래된 정보",
        "INCOMPLETE": "불완전한 내용",
        "DUPLICATE": "중복 문서",
        "BROKEN_LINK": "깨진 링크",
        "FORMATTING": "서식 오류",
        "OTHER": "기타",
    }

    ERROR_PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    PRIORITY_LABELS = {
        "CRITICAL": "긴급",
        "HIGH": "높음",
        "MEDIUM": "보통",
        "LOW": "낮음",
    }

    ERROR_STATUSES = ["OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED", "CLOSED"]
    STATUS_COLORS = {
        "OPEN": "red",
        "IN_PROGRESS": "orange",
        "RESOLVED": "green",
        "REJECTED": "gray",
        "CLOSED": "blue",
    }

    # -----------------------------------------------------------------------
    # 오류 신고 폼
    # -----------------------------------------------------------------------
    st.subheader("새 오류 신고")

    with st.form("error_report_form"):
        col1, col2 = st.columns(2)

        with col1:
            error_type = st.selectbox(
                "오류 유형",
                ERROR_TYPES,
                format_func=lambda x: ERROR_TYPE_LABELS.get(x, x),
            )
            priority = st.selectbox(
                "우선순위",
                ERROR_PRIORITIES,
                index=2,
                format_func=lambda x: PRIORITY_LABELS.get(x, x),
            )

        with col2:
            title = st.text_input("제목", placeholder="오류 요약을 입력하세요")
            er_document_id = st.text_input(
                "관련 문서 ID", placeholder="문서 ID (선택)", key="er_doc_id"
            )

        description = st.text_area(
            "오류 상세 설명",
            placeholder="발견한 오류를 상세히 설명해주세요.\n예: 3번 섹션의 배포 절차가 현재 프로세스와 다릅니다.",
            height=150,
        )

        er_submitted = st.form_submit_button("오류 신고 제출", type="primary")

        if er_submitted:
            title = sanitize_input(title, max_length=200)
            description = sanitize_input(description, max_length=5000)
            er_document_id = sanitize_input(er_document_id, max_length=200)
            if not title:
                st.warning("제목을 입력하세요.")
            elif not description:
                st.warning("오류 설명을 입력하세요.")
            else:
                body = {
                    "error_type": error_type,
                    "priority": priority,
                    "title": title,
                    "description": description,
                }
                if er_document_id:
                    body["document_id"] = er_document_id

                result = api_client.create_error_report(body)
                if api_failed(result):
                    st.error("API 연결 실패")
                    if st.button("재시도", key="retry_create_er"):
                        st.rerun()
                else:
                    st.success("오류 신고가 접수되었습니다.")
                    report_id = result.get("report_id", result.get("id", ""))
                    if report_id:
                        st.info(f"신고 번호: `{report_id}`")
                    st.cache_data.clear()
                    st.rerun()

    # -----------------------------------------------------------------------
    # 최근 오류 신고 목록
    # -----------------------------------------------------------------------
    st.markdown("---")
    st.subheader("오류 신고 목록")

    # 필터
    er_filter_col1, er_filter_col2 = st.columns(2)
    with er_filter_col1:
        er_status_filter = st.selectbox(
            "상태 필터",
            ["전체"] + ERROR_STATUSES,
            key="er_status_filter",
        )
    with er_filter_col2:
        er_page = st.number_input("페이지", min_value=1, value=1, key="er_page")

    er_status_param = None if er_status_filter == "전체" else er_status_filter
    reports_data = api_client.list_error_reports(status=er_status_param, page=er_page)

    if api_failed(reports_data):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_er_list"):
            st.cache_data.clear()
            st.rerun()
    else:
        reports = reports_data.get("items", reports_data.get("reports", []))
        total = reports_data.get("total", 0)
        er_page_size = 20
        total_pages = max(1, (total + er_page_size - 1) // er_page_size)

        st.markdown(f"**총 {total:,}건** (페이지 {er_page}/{total_pages})")

        if reports:
            for report in reports:
                report_id = report.get("id", report.get("report_id", ""))  # noqa: F841
                r_title = report.get("title", "제목 없음")
                r_type = report.get("error_type", "OTHER")
                r_status = report.get("status", "OPEN")
                r_priority = report.get("priority", "MEDIUM")
                r_created = report.get("created_at", "")
                r_doc_id = report.get("document_id", "")

                s_color = STATUS_COLORS.get(r_status, "gray")
                p_icon = {
                    "CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"
                }.get(r_priority, "⚪")

                with st.expander(f"{p_icon} [{r_status}] {r_title}"):
                    c1, c2, c3, c4 = st.columns(4)
                    with c1:
                        st.markdown(f"**유형**: {ERROR_TYPE_LABELS.get(r_type, r_type)}")
                    with c2:
                        st.markdown(f"**상태**: :{s_color}[{r_status}]")
                    with c3:
                        st.markdown(
                            f"**우선순위**: {PRIORITY_LABELS.get(r_priority, r_priority)}"
                        )
                    with c4:
                        st.markdown(f"**생성일**: {r_created[:16]}")

                    if r_doc_id:
                        st.markdown(f"**관련 문서**: `{r_doc_id}`")

                    desc = report.get("description", "")
                    if desc:
                        st.markdown(f"**설명**: {desc}")

                    resolution = report.get("resolution", "")
                    if resolution:
                        st.markdown(f"**해결 내용**: {resolution}")
        else:
            st.info("오류 신고 내역이 없습니다.")
