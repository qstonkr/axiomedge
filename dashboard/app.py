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

_PAGE_CHAT = "pages/chat.py"

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

from components.constants import TIER_ICONS  # noqa: F401
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

# Hide default nav immediately
hide_default_nav()

_app_logger = logging.getLogger(__name__)

# No auth gate needed for local - render sidebar directly
render_sidebar(_user_role="admin")


# =============================================================================
# Main: Home Page
# =============================================================================

st.markdown(
    """
<div style="text-align: center; padding: 2rem 0 1rem 0;">
    <h1 style="font-size: 2.5rem; margin-bottom: 0.5rem;">📚 GS리테일 지식 검색</h1>
    <p style="color: #666; font-size: 1.1rem;">궁금한 것을 검색하세요. 담당자도 찾을 수 있어요.</p>
</div>
""",
    unsafe_allow_html=True,
)

# -- Large centered search input --
col_l, col_c, col_r = st.columns([1, 3, 1])

with col_c:
    query = st.text_input(
        "검색어를 입력하세요",
        placeholder="예: 점포 운영 절차, 정산 프로세스, 담당자 찾기...",
        label_visibility="collapsed",
        key="main_search",
    )

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("🔍 검색", type="primary", use_container_width=True):
            if query:
                st.session_state.pending_query = query
                st.switch_page(_PAGE_CHAT)
            else:
                st.warning("검색어를 입력해주세요.")
    with btn_col2:
        if st.button("👤 담당자 찾기", use_container_width=True):
            if query:
                st.session_state.owner_query = query
            st.switch_page("pages/find_owner.py")

# -- Suggested query buttons --
st.markdown("")
suggested_queries = ["점포 운영 절차", "정산 프로세스", "분쟁 조정 방법", "주간보고 내용", "상품 등록 방법"]
sq_cols = st.columns(len(suggested_queries))
for i, sq in enumerate(suggested_queries):
    with sq_cols[i]:
        if st.button(sq, key=f"sq_{i}", use_container_width=True):
            st.session_state.pending_query = sq
            st.switch_page(_PAGE_CHAT)

st.markdown("---")

# =============================================================================
# Search group cards (side by side)
# =============================================================================

groups_result = api_client.list_search_groups()
groups = groups_result.get("groups", []) if not api_failed(groups_result) else []

# Define the two featured search engines
_hbu_group = {
    "name": "HBU검색엔진",
    "desc": "IT운영, 파트너스톡",
    "kb_count": 3,
    "group_data": None,
}
_pbu_group = {
    "name": "PBU검색엔진",
    "desc": "편의점 운영, 분쟁조정, G-ESPA",
    "kb_count": 3,
    "group_data": None,
}

# Try to match with actual groups from API
for g in groups:
    gname = g.get("name", "")
    if "HBU" in gname.upper() or "hbu" in gname.lower():
        _hbu_group["group_data"] = g
        _hbu_group["kb_count"] = len(g.get("kb_ids", []))
    elif "PBU" in gname.upper() or "pbu" in gname.lower():
        _pbu_group["group_data"] = g
        _pbu_group["kb_count"] = len(g.get("kb_ids", []))

card_col1, card_col2 = st.columns(2)

for col, grp in [(card_col1, _hbu_group), (card_col2, _pbu_group)]:
    with col:
        with st.container(border=True):
            st.markdown(f"**🔎 {grp['name']}**")
            st.caption(f"{grp['desc']} | {grp['kb_count']}개 KB")
            if st.button(
                "검색하기",
                key=f"grp_search_{grp['name']}",
                use_container_width=True,
            ):
                gd = grp["group_data"]
                if gd:
                    st.session_state.search_group_name = gd.get("name")
                    st.session_state.search_kb_ids = gd.get("kb_ids", [])
                    st.session_state["_active_group_name"] = gd.get("name")
                st.switch_page(_PAGE_CHAT)

st.markdown("---")

# =============================================================================
# Recent activity summary
# =============================================================================

kbs_result = api_client.list_kbs()
api_ok = not api_failed(kbs_result)

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
            "active_kbs": len(
                [kb for kb in kb_items if kb.get("status", "active") == "active"]
            ),
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

    st.markdown("### 📊 지식 현황")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("전체 문서", f"{total_docs:,}개", help="총 문서 수")
    with col2:
        quality_display = (
            f"{avg_quality:.0%}"
            if isinstance(avg_quality, float) and avg_quality <= 1
            else str(avg_quality)
        )
        st.metric("평균 품질", quality_display, help="전체 문서의 평균 품질 점수")
    with col3:
        st.metric("KB 수", f"{total_kbs}개", help="등록된 Knowledge Base 수")
    with col4:
        st.metric("활성 KB", f"{active_kbs}개", help="현재 활성 상태인 KB 수")
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

    _status_icon = {"healthy": "OK", "degraded": "DEGRADED", "unhealthy": "UNREACHABLE"}.get(
        _status, "UNKNOWN"
    )
    _api_label = "OK" if _api_ok else "FAIL"
    _neo4j_label = "OK" if _neo4j_ok else "-"

    st.caption(f"API: {_api_label} | Neo4j: {_neo4j_label} | {_status_icon}")

st.markdown("---")
st.caption("Knowledge Management System | Local Dashboard | Hub Search API")
