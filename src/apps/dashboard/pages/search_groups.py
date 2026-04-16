"""검색 그룹 관리

KB를 그룹으로 묶어 스코프 검색 지원.
관리자가 BU/팀 단위로 그룹 생성, 사용자가 검색 시 그룹 선택.
"""

import streamlit as st

st.set_page_config(page_title="검색 그룹", page_icon="📂", layout="wide")

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("📂 검색 그룹 관리")
st.caption("KB를 그룹으로 묶어 검색 범위를 설정합니다. 사용자는 검색 시 그룹을 선택할 수 있습니다.")

# =============================================================================
# 그룹 목록
# =============================================================================

groups_result = api_client.list_search_groups()
groups = []
if not api_failed(groups_result):
    groups = groups_result.get("groups", [])

# KB 목록 (그룹에 추가할 KB 선택용)
kbs_result = api_client.list_kbs()
all_kbs = []
if not api_failed(kbs_result):
    all_kbs = kbs_result.get("kbs", kbs_result.get("items", []))
kb_options = {kb.get("kb_id", kb.get("id", "")): kb.get("name", kb.get("kb_id", "")) for kb in all_kbs}

# =============================================================================
# 메트릭
# =============================================================================

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("총 그룹 수", f"{len(groups)}개")
with col2:
    default_groups = [g for g in groups if g.get("is_default")]
    st.metric("기본 그룹", default_groups[0]["name"] if default_groups else "없음")
with col3:
    total_kbs_in_groups = sum(len(g.get("kb_ids", [])) for g in groups)
    st.metric("그룹 내 KB (중복 포함)", f"{total_kbs_in_groups}개")

st.markdown("---")

# =============================================================================
# 새 그룹 생성
# =============================================================================

with st.expander("➕ 새 검색 그룹 만들기", expanded=not groups):
    with st.form("create_group_form"):
        new_name = st.text_input("그룹 이름", placeholder="예: CVS팀, IT운영, 홈쇼핑AX")
        new_desc = st.text_area("설명 (선택)", placeholder="이 그룹의 용도를 설명해주세요")

        if kb_options:
            selected_kbs = st.multiselect(
                "포함할 KB 선택",
                options=list(kb_options.keys()),
                format_func=lambda x: f"{kb_options.get(x, x)}",
            )
        else:
            st.warning("등록된 KB가 없습니다. 먼저 파일을 인제스천하세요.")
            selected_kbs = []

        is_default = st.checkbox("기본 그룹으로 설정", help="기본 그룹은 검색 시 아무 그룹도 선택하지 않았을 때 사용됩니다")

        submitted = st.form_submit_button("그룹 생성", type="primary")

        if submitted:
            if not new_name:
                st.error("그룹 이름을 입력해주세요.")
            elif not selected_kbs:
                st.error("최소 1개의 KB를 선택해주세요.")
            else:
                result = api_client.create_search_group({
                    "name": new_name,
                    "description": new_desc,
                    "kb_ids": selected_kbs,
                    "is_default": is_default,
                })
                if not api_failed(result):
                    st.success(f"그룹 '{new_name}' 생성 완료!")
                    st.rerun()
                else:
                    st.error(f"생성 실패: {result.get('error', result.get('detail', ''))}")

# =============================================================================
# 기존 그룹 목록 + 수정/삭제
# =============================================================================

if groups:
    st.subheader("📋 등록된 검색 그룹")

    for group in groups:
        group_id = group.get("id", "")
        group_name = group.get("name", "")
        group_desc = group.get("description", "")
        group_kb_ids = group.get("kb_ids", [])
        is_default = group.get("is_default", False)

        with st.container(border=True):
            header_col, action_col = st.columns([4, 1])

            with header_col:
                badge = " ⭐ 기본" if is_default else ""
                st.markdown(f"### {group_name}{badge}")
                if group_desc:
                    st.caption(group_desc)

                # KB 목록 표시
                kb_names = [kb_options.get(kid, kid) for kid in group_kb_ids]
                st.markdown(f"**KB ({len(group_kb_ids)}개):** {', '.join(kb_names)}")

            with action_col:
                if st.button("🗑️ 삭제", key=f"del_{group_id}", use_container_width=True):
                    result = api_client.delete_search_group(group_id)
                    if not api_failed(result):
                        st.success(f"'{group_name}' 삭제됨")
                        st.rerun()
                    else:
                        st.error("삭제 실패")

            # 수정 폼
            with st.expander(f"✏️ '{group_name}' 수정"):
                with st.form(f"edit_{group_id}"):
                    edit_name = st.text_input("이름", value=group_name, key=f"name_{group_id}")
                    edit_desc = st.text_area("설명", value=group_desc, key=f"desc_{group_id}")

                    if kb_options:
                        edit_kbs = st.multiselect(
                            "KB 선택",
                            options=list(kb_options.keys()),
                            default=[k for k in group_kb_ids if k in kb_options],
                            format_func=lambda x: kb_options.get(x, x),
                            key=f"kbs_{group_id}",
                        )
                    else:
                        edit_kbs = group_kb_ids

                    edit_default = st.checkbox(
                        "기본 그룹",
                        value=is_default,
                        key=f"default_{group_id}",
                    )

                    if st.form_submit_button("저장"):
                        result = api_client.update_search_group(group_id, {
                            "name": edit_name,
                            "description": edit_desc,
                            "kb_ids": edit_kbs,
                            "is_default": edit_default,
                        })
                        if not api_failed(result):
                            st.success("수정 완료!")
                            st.rerun()
                        else:
                            st.error("수정 실패")
else:
    st.info("등록된 검색 그룹이 없습니다. 위에서 새 그룹을 만들어보세요.")

st.markdown("---")
st.caption("검색 그룹은 '지식 검색' 페이지에서 검색 범위로 사용됩니다.")
