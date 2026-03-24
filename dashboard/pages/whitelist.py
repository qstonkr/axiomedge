"""Knowledge Dashboard Access Whitelist Management

Admin page for managing email-based access whitelist:
- View active/all whitelist entries
- Add new email with TTL
- Remove email (soft delete)
- Extend TTL
- Trigger ConfigMap sync

Created: 2026-03-16 (Knowledge Dashboard Phase 2)
"""

import streamlit as st

st.set_page_config(page_title="Access Whitelist", page_icon="🔑", layout="wide")


from datetime import datetime, timedelta, timezone

import pandas as pd

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("Access Whitelist")

tab_current, tab_add, tab_history = st.tabs(["Current", "Add", "History"])


# =============================================================================
# Tab 1: Current active whitelist
# =============================================================================
with tab_current:
    col_refresh, col_sync = st.columns([3, 1])
    with col_sync:
        if st.button("Sync to ConfigMap", type="primary"):
            result = api_client.sync_whitelist_to_configmap()
            if api_failed(result):
                st.error("ConfigMap sync failed.")
            else:
                st.success(result.get("message", "Sync completed."))

    page = st.number_input("Page", min_value=1, value=1, key="wl_page")
    result = api_client.list_whitelist(page=page, page_size=50, active_only=True)

    if api_failed(result):
        st.error("Failed to load whitelist.")
    else:
        items = result.get("items", [])
        total = result.get("total", 0)
        st.caption(f"Total: {total}")

        if items:
            df = pd.DataFrame(items)
            display_cols = ["email", "expires_at", "granted_by", "reason", "created_at"]
            available_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available_cols], use_container_width=True, hide_index=True)

            # Actions per entry
            st.subheader("Actions")
            selected_email = st.selectbox(
                "Select entry",
                options=[item["email"] for item in items],
                key="wl_select",
            )
            selected_item = next((i for i in items if i["email"] == selected_email), None)

            if selected_item:
                col_extend, col_remove = st.columns(2)

                with col_extend:
                    days_to_extend = st.number_input(
                        "Extend by (days)", min_value=1, max_value=365, value=30, key="wl_extend_days"
                    )
                    if st.button("Extend TTL"):
                        new_expiry = datetime.now(timezone.utc) + timedelta(days=days_to_extend)
                        ext_result = api_client.extend_whitelist_ttl(
                            selected_item["id"],
                            {"new_expires_at": new_expiry.isoformat()},
                        )
                        if api_failed(ext_result):
                            st.error("Failed to extend TTL.")
                        else:
                            st.success(f"Extended to {new_expiry.strftime('%Y-%m-%d %H:%M UTC')}")
                            st.rerun()

                with col_remove:
                    st.write("")  # spacer
                    if st.button("Remove", type="secondary"):
                        del_result = api_client.remove_whitelist_entry(selected_item["id"])
                        if api_failed(del_result):
                            st.error("Failed to remove entry.")
                        else:
                            st.success(f"Removed {selected_email}")
                            st.rerun()
        else:
            st.info("No active whitelist entries.")


# =============================================================================
# Tab 2: Add new entry
# =============================================================================
with tab_add:
    with st.form("add_whitelist_form"):
        email = st.text_input("Email", placeholder="user@partner.com")
        ttl_days = st.number_input("Access duration (days)", min_value=1, max_value=365, value=30)
        reason = st.text_input("Reason", placeholder="Partner project access")

        submitted = st.form_submit_button("Add to Whitelist")
        if submitted:
            if not email:
                st.error("Email is required.")
            else:
                expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
                add_result = api_client.add_whitelist_entry({
                    "email": email.strip(),
                    "expires_at": expires_at.isoformat(),
                    "reason": reason.strip() if reason else None,
                })
                if api_failed(add_result):
                    detail = add_result.get("detail", "Unknown error")
                    st.error(f"Failed to add: {detail}")
                else:
                    st.success(f"Added {email} (expires {expires_at.strftime('%Y-%m-%d')})")


# =============================================================================
# Tab 3: History (all entries including inactive)
# =============================================================================
with tab_history:
    hist_page = st.number_input("Page", min_value=1, value=1, key="wl_hist_page")
    hist_result = api_client.list_whitelist(page=hist_page, page_size=50, active_only=False)

    if api_failed(hist_result):
        st.error("Failed to load history.")
    else:
        items = hist_result.get("items", [])
        total = hist_result.get("total", 0)
        st.caption(f"Total: {total}")

        if items:
            df = pd.DataFrame(items)
            display_cols = ["email", "is_active", "expires_at", "granted_by", "reason", "created_at", "updated_at"]
            available_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available_cols], use_container_width=True, hide_index=True)
        else:
            st.info("No whitelist entries found.")
