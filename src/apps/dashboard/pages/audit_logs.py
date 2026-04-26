"""Audit log 조회 페이지 — C3.

``knowledge_audit_logs`` 의 최근 row 를 필터 (event_type / knowledge_id /
시간 범위) 와 함께 표시. ``audit_log:read`` 권한이 있는 admin 전용.

P2-6 와 연동: ``actor`` 가 ``_system`` 인 row 는 ``unauth.*`` event_type
prefix 를 가지므로 우선 색상 강조.
"""

import streamlit as st

st.set_page_config(
    page_title="Audit Logs", page_icon="📜", layout="wide",
)

from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📜 Audit Logs")
st.caption(
    "PR-12 (J) — 인증된 mutating endpoint 의 audit trail. ``unauth.``"
    "prefix event 는 인증 미들웨어 우회 의심 (P2-5 audit_unauthenticated 알람)."
)


# =============================================================================
# Filters
# =============================================================================
filter_cols = st.columns([2, 2, 1, 1, 1])
with filter_cols[0]:
    event_type_filter = st.text_input(
        "event_type filter", value="", placeholder="예: kb.update, unauth.",
    )
with filter_cols[1]:
    kb_filter = st.text_input(
        "knowledge_id filter", value="",
    )
with filter_cols[2]:
    limit = st.number_input(
        "limit", min_value=10, max_value=500, value=100, step=10,
    )
with filter_cols[3]:
    show_unauth_only = st.checkbox("unauth. only")
with filter_cols[4]:
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.rerun()


@st.cache_data(ttl=10)
def _fetch_audit_logs(
    event_type: str, kb_id: str, limit: int, unauth_only: bool,
) -> list[dict]:
    params = {"limit": limit}
    if event_type:
        params["event_type"] = event_type
    if kb_id:
        params["knowledge_id"] = kb_id
    if unauth_only:
        params["event_type"] = "unauth."
    try:
        return api_client.get(
            "/api/v1/admin/audit-logs",
            params=params,
            cache_key=f"audit_logs:{event_type}:{kb_id}:{limit}:{unauth_only}",
        ) or []
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to fetch audit logs: {e}")
        return []


rows = _fetch_audit_logs(event_type_filter, kb_filter, int(limit), show_unauth_only)

# =============================================================================
# Summary
# =============================================================================
total = len(rows)
unauth_count = sum(
    1 for r in rows
    if str(r.get("event_type", "")).startswith("unauth.")
)
m1, m2 = st.columns(2)
m1.metric("Rows", f"{total}")
m2.metric("⚠️ Unauthenticated", f"{unauth_count}",
          delta=("이상" if unauth_count > 0 else None))

if not rows:
    st.info("No audit log rows match the filter.")
    st.stop()

# =============================================================================
# Table — 강조 색상 처리
# =============================================================================
display_rows = []
for r in rows:
    event_type = r.get("event_type", "")
    is_unauth = event_type.startswith("unauth.")
    icon = "⚠️" if is_unauth else "✅"
    display_rows.append({
        "": icon,
        "created_at": r.get("created_at", ""),
        "event_type": event_type,
        "knowledge_id": r.get("knowledge_id", ""),
        "actor": r.get("actor", ""),
        "details_preview": str(r.get("details", ""))[:80],
        "id": (r.get("id") or "")[:8] + "…",
    })

st.subheader(f"최근 {total} rows")
st.dataframe(display_rows, use_container_width=True, hide_index=True)

# =============================================================================
# Detail expander
# =============================================================================
st.subheader("개별 row 상세 보기")
ids = [r.get("id") for r in rows if r.get("id")]
if ids:
    selected_id = st.selectbox("audit row id 선택", ids[:50])
    selected = next(
        (r for r in rows if r.get("id") == selected_id), None,
    )
    if selected:
        st.json(selected)
