"""오류 신고 -- 오류 리포트 생성 및 조회

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="오류 신고", page_icon="🚨", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/errors", "오류 신고")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed
from services.validators import sanitize_input

render_sidebar()

st.title("오류 신고")
st.caption("문서 오류를 신고하고 처리 현황을 확인합니다.")

# ---------------------------------------------------------------------------
# ErrorType 7종, ErrorPriority 4종, ErrorStatus 5종
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 오류 신고 폼
# ---------------------------------------------------------------------------
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
        document_id = st.text_input("관련 문서 ID", placeholder="문서 ID (선택)")

    description = st.text_area(
        "오류 상세 설명",
        placeholder="발견한 오류를 상세히 설명해주세요.\n예: 3번 섹션의 배포 절차가 현재 프로세스와 다릅니다.",
        height=150,
    )

    submitted = st.form_submit_button("오류 신고 제출", type="primary")

    if submitted:
        title = sanitize_input(title, max_length=200)
        description = sanitize_input(description, max_length=5000)
        document_id = sanitize_input(document_id, max_length=200)
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
            if document_id:
                body["document_id"] = document_id

            result = api_client.create_error_report(body)
            if api_failed(result):
                st.error("API 연결 실패")
                if st.button("재시도", key="retry_create"):
                    st.rerun()
            else:
                st.success("오류 신고가 접수되었습니다.")
                report_id = result.get("report_id", result.get("id", ""))
                if report_id:
                    st.info(f"신고 번호: `{report_id}`")
                st.cache_data.clear()
                st.rerun()

# ---------------------------------------------------------------------------
# 최근 오류 신고 목록
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("오류 신고 목록")

# 필터
filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    status_filter = st.selectbox(
        "상태 필터",
        ["전체"] + ERROR_STATUSES,
        key="er_status_filter",
    )
with filter_col2:
    page = st.number_input("페이지", min_value=1, value=1, key="er_page")

status_param = None if status_filter == "전체" else status_filter
reports_data = api_client.list_error_reports(status=status_param, page=page)

if api_failed(reports_data):
    st.error("API 연결 실패")
    if st.button("재시도", key="retry_list"):
        st.cache_data.clear()
        st.rerun()
else:
    reports = reports_data.get("items", reports_data.get("reports", []))
    total = reports_data.get("total", 0)
    er_page_size = 20
    total_pages = max(1, (total + er_page_size - 1) // er_page_size)

    st.markdown(f"**총 {total:,}건** (페이지 {page}/{total_pages})")

    if reports:
        for report in reports:
            report_id = report.get("id", report.get("report_id", ""))
            r_title = report.get("title", "제목 없음")
            r_type = report.get("error_type", "OTHER")
            r_status = report.get("status", "OPEN")
            r_priority = report.get("priority", "MEDIUM")
            r_created = report.get("created_at", "")
            r_doc_id = report.get("document_id", "")

            s_color = STATUS_COLORS.get(r_status, "gray")
            p_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(r_priority, "⚪")

            with st.expander(f"{p_icon} [{r_status}] {r_title}"):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown(f"**유형**: {ERROR_TYPE_LABELS.get(r_type, r_type)}")
                with c2:
                    st.markdown(f"**상태**: :{s_color}[{r_status}]")
                with c3:
                    st.markdown(f"**우선순위**: {PRIORITY_LABELS.get(r_priority, r_priority)}")
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
