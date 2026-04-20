"""Auth / RBAC 관리

사용자 관리, KB 권한, ABAC 정책 관리 페이지.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="Auth / RBAC", page_icon="🔐", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/users", "사용자/권한")

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar()

st.title("🔐 Auth / RBAC")

tab_users, tab_kb_perms, tab_abac = st.tabs(["사용자 관리", "KB 권한", "ABAC 정책"])

# =============================================================================
# Tab 1: 사용자 관리
# =============================================================================
with tab_users:
    st.subheader("사용자 목록")

    users_result = api_client.list_auth_users()

    if api_failed(users_result):
        st.error("사용자 목록을 불러올 수 없습니다.")
    else:
        users = users_result.get("items", users_result.get("users", []))
        if users:
            import pandas as pd

            rows = []
            for u in users:
                rows.append({
                    "ID": u.get("id", "-"),
                    "이름": u.get("display_name", u.get("name", "-")),
                    "이메일": u.get("email", "-"),
                    "부서": u.get("department", "-"),
                    "Provider": u.get("provider", "-"),
                    "상태": "활성" if u.get("is_active", True) else "비활성",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("등록된 사용자가 없습니다.")

    st.markdown("---")

    col_reg, col_role = st.columns(2)

    with col_reg:
        st.subheader("사용자 등록")
        with st.form("user_registration_form"):
            reg_email = st.text_input("이메일 *")
            reg_name = st.text_input("이름 *")
            reg_dept = st.text_input("부서")
            reg_org = st.text_input("조직 ID")
            reg_role = st.selectbox("초기 역할", ["viewer", "contributor", "editor", "kb_manager", "admin"])
            reg_submitted = st.form_submit_button("사용자 등록", type="primary")

            if reg_submitted:
                if not reg_email or not reg_name:
                    st.error("이메일과 이름은 필수입니다.")
                else:
                    result = api_client.create_auth_user({
                            "email": reg_email,
                            "display_name": reg_name,
                            "department": reg_dept or None,
                            "organization_id": reg_org or None,
                            "role": reg_role,
                    })
                    if api_failed(result):
                        st.error(f"등록 실패: {result.get('detail', '알 수 없는 오류')}")
                    else:
                        st.success(f"사용자 '{reg_name}' ({reg_email}) 등록 완료!")
                        st.rerun()

    with col_role:
        st.subheader("역할 할당")
        with st.form("role_assignment_form"):
            user_options = [f"{u.get('display_name', u.get('name', '?'))} ({u.get('email', '?')})" for u in users] if users else []
            user_ids = [u.get("id", "") for u in users] if users else []

            if user_options:
                selected_idx = st.selectbox("사용자 선택", range(len(user_options)), format_func=lambda i: user_options[i])
                user_id_input = user_ids[selected_idx]
            else:
                st.info("등록된 사용자가 없습니다.")
                user_id_input = ""

            role_select = st.selectbox("역할", ["viewer", "contributor", "editor", "kb_manager", "admin"])
            submitted = st.form_submit_button("역할 할당", type="primary")

            if submitted and user_id_input:
                result = api_client.assign_user_role(
                    user_id_input, {"role": role_select}
                )
                if api_failed(result):
                    st.error("역할 할당에 실패했습니다.")
                else:
                    st.success(f"역할 '{role_select}' 할당 완료!")
                    st.rerun()

# =============================================================================
# Tab 2: KB 권한
# =============================================================================
with tab_kb_perms:
    st.subheader("KB 권한 관리")

    # KB 선택
    kbs_result = api_client.list_kbs()
    kb_options = []
    if not api_failed(kbs_result):
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        kb_options = [
            (kb.get("kb_id", kb.get("id", "")), kb.get("name", kb.get("kb_id", "")))
            for kb in kb_items
        ]

    if kb_options:
        selected_kb = st.selectbox(
            "KB 선택",
            options=[kid for kid, _ in kb_options],
            format_func=lambda x: next((name for kid, name in kb_options if kid == x), x),
            key="kb_perm_select",
        )

        if selected_kb:
            perms_result = api_client.get_kb_permissions(selected_kb)

            if api_failed(perms_result):
                st.warning("권한 정보를 불러올 수 없습니다.")
            else:
                perms = perms_result.get("items", perms_result.get("permissions", []))
                if perms:
                    import pandas as pd

                    perm_rows = []
                    for p in perms:
                        perm_rows.append({
                            "사용자": p.get("user_id", p.get("subject", "-")),
                            "권한": p.get("permission", p.get("access_level", "-")),
                            "부여일": p.get("granted_at", "-"),
                        })
                    st.dataframe(pd.DataFrame(perm_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("설정된 권한이 없습니다.")

            # 권한 추가/삭제 폼
            st.markdown("---")
            col_add, col_remove = st.columns(2)

            with col_add:
                st.markdown("**권한 추가**")
                with st.form("add_perm_form"):
                    add_user = st.text_input("사용자 ID", key="add_perm_user")
                    add_level = st.selectbox("권한", ["reader", "contributor", "manager", "owner"], key="add_perm_level")
                    if st.form_submit_button("추가", type="primary") and add_user:
                        res = api_client.add_kb_permission(
                            selected_kb,
                            {"user_id": add_user, "permission_level": add_level},
                        )
                        if api_failed(res):
                            st.error("권한 추가 실패.")
                        else:
                            st.success("권한이 추가되었습니다.")
                            st.rerun()

            with col_remove:
                st.markdown("**권한 삭제**")
                with st.form("remove_perm_form"):
                    rm_user = st.text_input("사용자 ID", key="rm_perm_user")
                    if st.form_submit_button("삭제") and rm_user:
                        res = api_client.remove_kb_permission(
                            selected_kb, rm_user
                        )
                        if api_failed(res):
                            st.error("권한 삭제 실패.")
                        else:
                            st.success("권한이 삭제되었습니다.")
                            st.rerun()
    else:
        st.warning("KB 목록을 불러올 수 없습니다.")

# =============================================================================
# Tab 3: ABAC 정책
# =============================================================================
with tab_abac:
    st.subheader("ABAC 정책 관리")

    policies_result = api_client.list_abac_policies()

    if api_failed(policies_result):
        st.error("정책 목록을 불러올 수 없습니다.")
    else:
        policies = policies_result.get("items", policies_result.get("policies", []))
        if policies:
            import pandas as pd

            policy_rows = []
            for p in policies:
                policy_rows.append({
                    "정책 ID": p.get("policy_id", p.get("id", "-")),
                    "이름": p.get("name", "-"),
                    "설명": p.get("description", "-"),
                    "효과": p.get("effect", "-"),
                    "상태": p.get("status", "active"),
                })
            st.dataframe(pd.DataFrame(policy_rows), use_container_width=True, hide_index=True)
        else:
            st.info("등록된 정책이 없습니다.")

    st.markdown("---")
    st.subheader("정책 생성")

    with st.form("create_policy_form"):
        policy_name = st.text_input("정책 이름")
        policy_desc = st.text_area("설명", max_chars=500)
        policy_effect = st.selectbox("효과", ["allow", "deny"])
        policy_resource = st.text_input("리소스 패턴", placeholder="kb:*")
        policy_action = st.selectbox("액션", ["read", "write", "delete", "admin"])

        if st.form_submit_button("정책 생성", type="primary"):
            if policy_name:
                res = api_client.create_abac_policy({
                        "name": policy_name,
                        "description": policy_desc,
                        "effect": policy_effect,
                        "resource": policy_resource,
                        "action": policy_action,
                })
                if api_failed(res):
                    st.error("정책 생성 실패.")
                else:
                    st.success(f"정책 '{policy_name}'이 생성되었습니다.")
                    st.rerun()
            else:
                st.warning("정책 이름을 입력해주세요.")

    # 정책 삭제
    st.markdown("---")
    st.subheader("정책 삭제")

    with st.form("delete_policy_form"):
        del_policy_id = st.text_input("삭제할 정책 ID")
        if st.form_submit_button("삭제") and del_policy_id:
            res = api_client.delete_abac_policy(del_policy_id)
            if api_failed(res):
                st.error("정책 삭제 실패.")
            else:
                st.success("정책이 삭제되었습니다.")
                st.rerun()
