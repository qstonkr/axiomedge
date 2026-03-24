"""검색 이력 -- 사용자 검색 히스토리 조회

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="검색 이력", page_icon="🕐", layout="wide")


from datetime import datetime, timedelta

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("검색 이력")
st.caption("검색 기록을 조회하고, 날짜/키워드로 필터링합니다.")

# ---------------------------------------------------------------------------
# 필터
# ---------------------------------------------------------------------------
filter_col1, filter_col2, filter_col3 = st.columns([2, 1, 1])

with filter_col1:
    query_filter = st.text_input("검색어 필터", placeholder="검색어로 필터링...", key="sh_query")

with filter_col2:
    date_start = st.date_input(
        "시작 날짜",
        value=datetime.now() - timedelta(days=30),
        key="sh_start",
    )

with filter_col3:
    date_end = st.date_input("종료 날짜", value=datetime.now(), key="sh_end")

# Pagination
page_size = st.selectbox("페이지 크기", [20, 50, 100], index=1, key="sh_page_size")
page = st.number_input("페이지", min_value=1, value=1, key="sh_page")

# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------
data = api_client.get_search_history(page=page, page_size=page_size)
if api_failed(data):
    st.error("API 연결 실패")
    if st.button("재시도", key="retry_sh"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

items = data.get("items", [])
total = data.get("total", 0)

st.markdown(f"**총 {total:,}건** (페이지 {page})")

# ---------------------------------------------------------------------------
# 필터링 (클라이언트 사이드)
# ---------------------------------------------------------------------------
filtered = items
if query_filter:
    query_lower = query_filter.lower()
    filtered = [
        item for item in filtered
        if query_lower in item.get("query", "").lower()
    ]

if date_start and date_end:
    start_str = str(date_start)
    end_str = str(date_end)
    filtered = [
        item for item in filtered
        if start_str <= item.get("timestamp", "")[:10] <= end_str
    ]

# ---------------------------------------------------------------------------
# 테이블
# ---------------------------------------------------------------------------
if filtered:
    import pandas as pd

    df = pd.DataFrame(filtered)

    # 표시할 컬럼 선택
    display_mapping = {
        "timestamp": "시간",
        "query": "검색어",
        "result_count": "결과 수",
        "kb_ids": "검색 KB",
        "user_id": "사용자",
        "response_time_ms": "응답시간(ms)",
        "source": "소스",
    }

    available_cols = [c for c in display_mapping if c in df.columns]
    if available_cols:
        df_display = df[available_cols].rename(
            columns={c: display_mapping[c] for c in available_cols}
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------------------
    # 이벤트 타임라인 (LineageEventType 기반)
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("검색 이벤트 타임라인")

    EVENT_TYPES = [
        "CREATED", "UPDATED", "MERGED", "SPLIT", "ARCHIVED",
        "RESTORED", "LINKED", "UNLINKED", "MIGRATED",
    ]

    events_with_type = [item for item in filtered if item.get("event_type")]
    if events_with_type:
        for evt in events_with_type[:50]:
            etype = evt.get("event_type", "UNKNOWN")
            ts = evt.get("timestamp", "")
            query_text = evt.get("query", "")
            icon_map = {
                "CREATED": "🆕", "UPDATED": "📝", "MERGED": "🔀",
                "SPLIT": "✂️", "ARCHIVED": "📦", "RESTORED": "♻️",
                "LINKED": "🔗", "UNLINKED": "🔓", "MIGRATED": "🚚",
            }
            icon = icon_map.get(etype, "📌")
            st.markdown(f"{icon} **{ts}** - `{etype}` : {query_text}")
    else:
        # Fallback: show recent queries as timeline
        st.markdown("최근 검색 타임라인:")
        for item in filtered[:20]:
            ts = item.get("timestamp", "")
            q = item.get("query", "")
            count = item.get("result_count", 0)
            st.markdown(f"🔍 **{ts}** - \"{q}\" ({count}건)")

    # ---------------------------------------------------------------------------
    # 통계 요약
    # ---------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("기간 요약 통계")
    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("조회 기간 검색 수", f"{len(filtered):,}건")
    with s2:
        if "result_count" in df.columns:
            avg_results = df["result_count"].mean()
            st.metric("평균 결과 수", f"{avg_results:.1f}건")
        else:
            st.metric("평균 결과 수", "-")
    with s3:
        if "response_time_ms" in df.columns:
            avg_rt = df["response_time_ms"].mean()
            st.metric("평균 응답시간", f"{avg_rt:.0f}ms")
        else:
            st.metric("평균 응답시간", "-")
else:
    st.info("조건에 맞는 검색 이력이 없습니다.")
