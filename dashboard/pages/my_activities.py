"""나의 활동

활동 요약 카드, 활동 타임라인, 필터링.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="나의 활동", page_icon="📋", layout="wide")

from datetime import date, timedelta

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar()

st.title("📋 나의 활동")

# =============================================================================
# Activity Summary Cards
# =============================================================================
st.subheader("활동 요약")

summary_result = api_client._request("GET", "/api/v1/auth/my-activities/summary")

if not api_failed(summary_result):
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "총 활동",
            f"{summary_result.get('total', 0):,}건",
        )
    with col2:
        st.metric(
            "기간 (일)",
            f"{summary_result.get('period_days', 0)}일",
        )
    with col3:
        st.metric(
            "검색",
            f"{summary_result.get('by_type', {}).get('search', 0):,}건",
        )
    with col4:
        st.metric(
            "피드백",
            f"{summary_result.get('by_type', {}).get('feedback', 0):,}건",
        )
else:
    st.warning("활동 요약을 불러올 수 없습니다.")

st.markdown("---")

# =============================================================================
# Filters
# =============================================================================
st.subheader("활동 타임라인")

filter_col1, filter_col2, filter_col3 = st.columns(3)

with filter_col1:
    activity_type = st.selectbox(
        "활동 유형",
        options=["all", "search", "feedback", "document", "login", "ingestion"],
        format_func=lambda x: {
            "all": "전체",
            "search": "검색",
            "feedback": "피드백",
            "document": "문서",
            "login": "로그인",
            "ingestion": "인제스천",
        }.get(x, x),
        key="activity_type_filter",
    )

with filter_col2:
    date_from = st.date_input(
        "시작일",
        value=date.today() - timedelta(days=30),
        key="activity_date_from",
    )

with filter_col3:
    date_to = st.date_input(
        "종료일",
        value=date.today(),
        key="activity_date_to",
    )

# =============================================================================
# Activity List
# =============================================================================
params: dict = {"limit": 50}
if activity_type != "all":
    params["activity_type"] = activity_type
if date_from:
    params["date_from"] = str(date_from)
if date_to:
    params["date_to"] = str(date_to)

activities_result = api_client._request(
    "GET", "/api/v1/auth/my-activities", params=params
)

if api_failed(activities_result):
    st.error("활동 목록을 불러올 수 없습니다.")
else:
    activities = activities_result.get("items", activities_result.get("activities", []))
    total = activities_result.get("total", len(activities))
    st.caption(f"총 {total:,}건")

    if activities:
        type_icons = {
            "search": "🔍",
            "feedback": "📝",
            "document": "📄",
            "login": "🔑",
            "ingestion": "📥",
        }

        for act in activities:
            act_type = act.get("activity_type", "unknown")
            icon = type_icons.get(act_type, "📌")
            title = act.get("title", act.get("description", "-"))
            timestamp = act.get("created_at", act.get("timestamp", "-"))
            detail = act.get("detail", act.get("metadata", ""))

            with st.container(border=True):
                col_icon, col_content, col_time = st.columns([0.5, 4, 1.5])
                with col_icon:
                    st.markdown(f"### {icon}")
                with col_content:
                    st.markdown(f"**{title}**")
                    if detail:
                        detail_str = str(detail) if not isinstance(detail, str) else detail
                        st.caption(detail_str[:200])
                with col_time:
                    st.caption(str(timestamp))
    else:
        st.info("해당 기간에 활동 내역이 없습니다.")
