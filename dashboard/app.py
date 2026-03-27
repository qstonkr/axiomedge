"""Knowledge Management - Local Dashboard

Standalone knowledge dashboard for knowledge-local project.
All data fetched via local FastAPI server at localhost:8000.

Run:
    cd dashboard
    streamlit run app.py
"""

import logging

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

# =============================================================================
# Page config (must be first)
# =============================================================================
st.set_page_config(
    page_title="지식 검색",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from services.logging_config import init_logging, get_trace_id

init_logging()

if "trace_id" not in st.session_state:
    st.session_state.trace_id = get_trace_id()

from components.constants import TIER_ICONS
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

# Hide default nav immediately
hide_default_nav()

_app_logger = logging.getLogger(__name__)

# No auth gate needed for local - render sidebar directly
render_sidebar(user_role="admin")


# =============================================================================
# Main: Search UI (renders immediately without API dependency)
# =============================================================================

st.markdown("""
<div style="text-align: center; padding: 2rem 0;">
    <h1 style="font-size: 2.5rem; margin-bottom: 0.5rem;">🔍 지식 검색</h1>
    <p style="color: #666; font-size: 1.1rem;">궁금한 것을 검색하세요. 담당자도 찾을 수 있어요.</p>
</div>
""", unsafe_allow_html=True)

col1, col2, col3 = st.columns([1, 3, 1])

with col2:
    query = st.text_input(
        "검색어를 입력하세요",
        placeholder="예: K8s 배포 담당자, 데이터마트 문서, AWS 가이드...",
        label_visibility="collapsed",
        key="main_search",
    )

    # Search group selection
    groups_result = api_client._request("GET", "/api/v1/search-groups")
    groups = groups_result.get("groups", []) if not api_failed(groups_result) else []

    if groups:
        group_options = {g.get("name", ""): g for g in groups}
        # Find default group
        default_idx = 0
        for i, g in enumerate(groups):
            if g.get("is_default"):
                default_idx = i
                break

        gcol1, gcol2 = st.columns([3, 1])
        with gcol1:
            selected_group_name = st.selectbox(
                "검색 그룹",
                options=list(group_options.keys()),
                index=default_idx,
                key="main_search_group",
                label_visibility="collapsed",
            )
        with gcol2:
            selected_group = group_options.get(selected_group_name, {})
            kb_count = len(selected_group.get("kb_ids", []))
            st.caption(f"KB {kb_count}개")

        if selected_group.get("description"):
            st.caption(f"ℹ️ {selected_group['description']}")

        # Store selected group for chat page
        st.session_state.search_group_name = selected_group_name
        st.session_state.search_kb_ids = selected_group.get("kb_ids", [])

    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        if st.button("🔍 검색", type="primary", use_container_width=True):
            if query:
                st.session_state.pending_query = query
                st.switch_page("pages/chat.py")
            else:
                st.warning("검색어를 입력해주세요.")

    with btn_col2:
        if st.button("👤 담당자 찾기", use_container_width=True):
            if query:
                st.session_state.owner_query = query
            st.switch_page("pages/find_owner.py")

    with btn_col3:
        if st.button("📝 오류 신고", use_container_width=True):
            st.switch_page("pages/error_report.py")

st.markdown("---")

# =============================================================================
# KB List (API dependent sections below)
# =============================================================================

kbs_result = api_client.list_kbs()
api_ok = not api_failed(kbs_result)

st.markdown("### 📚 지식 베이스")
st.caption("등록된 지식 베이스 목록입니다.")

if api_ok:
    kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
    if kb_items:
        cols = st.columns(3)
        for i, kb in enumerate(kb_items[:6]):
            with cols[i % 3]:
                tier = kb.get("tier", "-")
                icon = TIER_ICONS.get(tier, "📁")

                with st.container(border=True):
                    name = kb.get("name", kb.get("id", "-"))
                    st.markdown(f"**{icon} {name}**")
                    doc_count = kb.get("document_count", 0)
                    st.caption(f"{tier} | {doc_count:,}개 문서")

                    kb_id = kb.get("kb_id", kb.get("id", ""))
                    if st.button("🔍 검색", key=f"kb_search_{i}", use_container_width=True):
                        st.session_state.search_kb_ids = [kb_id]
                        st.session_state.search_group_name = None
                        st.switch_page("pages/chat.py")
    else:
        st.info("등록된 KB가 없습니다.")
else:
    from services import config as _cfg

    st.error(
        "API 서버에 연결할 수 없습니다.\n\n"
        f"**API URL:** `{_cfg.DASHBOARD_API_URL}`\n\n"
        "FastAPI 서버가 실행 중인지 확인해주세요: `make api`"
    )
    if st.button("🔄 재시도", key="retry_home"):
        st.cache_data.clear()
        st.rerun()


st.markdown("---")

# =============================================================================
# Summary metrics
# =============================================================================
st.markdown("### 📊 지식 현황")

if api_ok:
    agg_result = api_client.get_kb_aggregation()

    if not api_failed(agg_result):
        agg = agg_result
    else:
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        total_doc_count = sum(kb.get("document_count", 0) for kb in kb_items)
        agg = {
            "total_kbs": kbs_result.get("total", len(kb_items)),
            "total_documents": total_doc_count,
            "avg_quality_score": 0,
            "active_kbs": len([kb for kb in kb_items if kb.get("status", "active") == "active"]),
        }

    total_docs = agg.get("total_documents", 0)

    if total_docs == 0 and agg.get("total_kbs", 0) > 0:
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        fallback_total = sum(kb.get("document_count", 0) for kb in kb_items)
        if fallback_total > 0:
            total_docs = fallback_total
    total_kbs = agg.get("total_kbs", 0)
    avg_quality = agg.get("avg_quality_score", 0)
    active_kbs = agg.get("active_kbs", total_kbs)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("전체 문서", f"{total_docs:,}개", help="총 문서 수")
    with col2:
        quality_display = f"{avg_quality:.0%}" if isinstance(avg_quality, float) and avg_quality <= 1 else str(avg_quality)
        st.metric("평균 품질", quality_display, help="전체 문서의 평균 품질 점수")
    with col3:
        st.metric("KB 수", f"{total_kbs}개", help="등록된 Knowledge Base 수")
    with col4:
        st.metric("활성 KB", f"{active_kbs}개", help="현재 활성 상태인 KB 수")

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📊 현황")
        st.metric("KB", f"{total_kbs:,}개")
        st.metric("문서", f"{total_docs:,}개")
else:
    st.warning("API에 연결할 수 없어 현황을 표시할 수 없습니다.")


# =============================================================================
# Health check in sidebar
# =============================================================================

@st.cache_data(ttl=60)
def _cached_health() -> dict:
    from services.health import check_health
    return check_health()

with st.sidebar:
    st.markdown("---")
    _health = _cached_health()
    _status = _health.get("status", "unknown")
    _api_ok = _health.get("checks", {}).get("api", False)
    _neo4j_ok = _health.get("checks", {}).get("neo4j", False)

    _status_icon = {"healthy": "OK", "degraded": "DEGRADED", "unhealthy": "UNREACHABLE"}.get(_status, "UNKNOWN")
    _api_label = "OK" if _api_ok else "FAIL"
    _neo4j_label = "OK" if _neo4j_ok else "-"

    st.caption(f"API: {_api_label} | Neo4j: {_neo4j_label} | {_status_icon}")

st.markdown("---")
st.caption("Knowledge Management System | Local Dashboard | Hub Search API")
