"""내 담당 문서 (병합)

3 탭: 내 담당 문서, 대기 작업, 알림

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="내 담당 문서", page_icon="📄", layout="wide")


import pandas as pd

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("📄 내 담당 문서")

# ---------------------------------------------------------------------------
# 현재 사용자 (세션에서 가져오기)
# ---------------------------------------------------------------------------
current_user = st.session_state.get("current_user_id", "")
if not current_user:
    with st.sidebar:
        st.markdown("---")
        current_user = st.text_input(
            "사용자 ID",
            placeholder="예: mslee",
            key="my_docs_user_id",
        )
        if current_user:
            st.session_state.current_user_id = current_user

tab_docs, tab_pending, tab_notifications = st.tabs(["내 담당 문서", "대기 작업", "알림"])


# =============================================================================
# 탭 1: 내 담당 문서
# =============================================================================
with tab_docs:
    if not current_user:
        st.info("사이드바에서 사용자 ID를 입력해주세요.")
    else:
        # KB 선택
        kbs_result = api_client.list_kbs()
        if api_failed(kbs_result):
            st.error("API 연결 실패")
            if st.button("🔄 재시도", key="retry_my_docs"):
                st.cache_data.clear()
                st.rerun()
        else:
            kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
            if kb_items:
                kb_options = {
                    kb.get("name", kb.get("kb_id", "")): kb.get("kb_id", kb.get("id", ""))
                    for kb in kb_items
                }
                all_kb_names = list(kb_options.keys())

                selected_kb_name = st.selectbox(
                    "KB 선택", options=all_kb_names, key="my_docs_kb_select"
                )
                selected_kb_id = kb_options[selected_kb_name]

                # 담당 문서 조회
                owners_result = api_client.list_document_owners(selected_kb_id)

                if api_failed(owners_result):
                    st.error("API 연결 실패")
                    if st.button("🔄 재시도", key="retry_my_docs_list"):
                        st.cache_data.clear()
                        st.rerun()
                else:
                    all_owners = owners_result.get("items", owners_result.get("owners", []))

                    # 현재 사용자 필터
                    my_docs = [
                        o for o in all_owners
                        if o.get("owner_user_id", o.get("user_id", "")) == current_user
                        or o.get("owner_name", o.get("name", "")) == current_user
                    ]

                    if my_docs:
                        st.success(f"{len(my_docs)}건의 담당 문서가 있습니다.")

                        rows = []
                        for doc in my_docs:
                            ownership_type = doc.get("ownership_type", doc.get("type", "-"))
                            status = doc.get("status", "-")
                            status_icons = {
                                "ACTIVE": "🟢",
                                "PENDING": "🟡",
                                "STALE": "🟠",
                                "EXPIRED": "🔴",
                            }
                            status_display = f"{status_icons.get(status, '⚪')} {status}"

                            rows.append({
                                "문서 제목": doc.get("document_title", doc.get("title", "-")),
                                "문서 ID": doc.get("document_id", doc.get("doc_id", "-"))[:12],
                                "소유 유형": ownership_type,
                                "상태": status_display,
                                "할당일": str(doc.get("assigned_at", doc.get("created_at", "-")))[:10],
                                "마지막 검증": str(doc.get("last_verified", "-"))[:10] if doc.get("last_verified") else "-",
                            })

                        df = pd.DataFrame(rows)
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    else:
                        st.info(
                            f"'{selected_kb_name}'에 '{current_user}' 사용자의 담당 문서가 없습니다."
                        )
            else:
                st.info("등록된 KB가 없습니다.")


# =============================================================================
# 탭 2: 대기 작업
# =============================================================================
with tab_pending:
    if not current_user:
        st.info("사이드바에서 사용자 ID를 입력해주세요.")
    else:
        st.subheader("대기 중인 작업")
        st.caption("검증, 피드백, 오류 신고 등 내 문서에 대한 대기 작업")

        # 검증 대기
        st.markdown("#### 검증 대기")
        verification_result = api_client.get_verification_pending()
        if api_failed(verification_result):
            st.warning("검증 대기 목록을 불러올 수 없습니다.")
        else:
            pending_items = verification_result.get("items", verification_result.get("pending", []))
            my_pending = [
                p for p in pending_items
                if p.get("owner_user_id", p.get("user_id", "")) == current_user
                or p.get("owner_name", p.get("name", "")) == current_user
            ]
            if my_pending:
                for item in my_pending[:10]:
                    with st.container(border=True):
                        pcol1, pcol2, pcol3 = st.columns([3, 1, 1])
                        with pcol1:
                            st.markdown(f"**{item.get('document_title', item.get('title', '-'))}**")
                            st.caption(f"요청일: {str(item.get('requested_at', '-'))[:10]}")
                        with pcol2:
                            st.markdown(f"유형: {item.get('verification_type', '-')}")
                        with pcol3:
                            doc_id = item.get("document_id", item.get("doc_id", ""))
                            if st.button("검증", key=f"verify_{doc_id}"):
                                st.info(f"검증 페이지로 이동: {doc_id}")
            else:
                st.info("검증 대기 작업이 없습니다.")

        st.markdown("---")

        # 피드백 대기
        st.markdown("#### 피드백 대기")
        feedback_result = api_client.list_feedback(status="PENDING")
        if api_failed(feedback_result):
            st.warning("피드백 목록을 불러올 수 없습니다.")
        else:
            fb_items = feedback_result.get("items", feedback_result.get("feedback", []))
            my_feedback = fb_items[:5]  # 최근 5건
            if my_feedback:
                for fb in my_feedback:
                    with st.container(border=True):
                        st.markdown(f"**{fb.get('title', fb.get('subject', '-'))}**")
                        st.caption(
                            f"유형: {fb.get('feedback_type', '-')} | "
                            f"작성일: {str(fb.get('created_at', '-'))[:10]}"
                        )
            else:
                st.info("대기 중인 피드백이 없습니다.")

        st.markdown("---")

        # 오류 신고 대기
        st.markdown("#### 오류 신고 대기")
        error_result = api_client.list_error_reports(status="OPEN")
        if api_failed(error_result):
            st.warning("오류 신고 목록을 불러올 수 없습니다.")
        else:
            err_items = error_result.get("items", error_result.get("reports", []))
            my_errors = err_items[:5]  # 최근 5건
            if my_errors:
                for err in my_errors:
                    with st.container(border=True):
                        st.markdown(f"**{err.get('title', err.get('description', '-')[:40])}**")
                        st.caption(
                            f"상태: {err.get('status', '-')} | "
                            f"신고일: {str(err.get('created_at', '-'))[:10]}"
                        )
            else:
                st.info("대기 중인 오류 신고가 없습니다.")


# =============================================================================
# 탭 3: 알림
# =============================================================================
with tab_notifications:
    if not current_user:
        st.info("사이드바에서 사용자 ID를 입력해주세요.")
    else:
        st.subheader("알림")
        st.caption("조직 변경(OrgChangeEvent) 및 문서 할당 알림")

        # OrgChangeEvent 기반 알림 (API가 제공하는 경우)
        st.markdown("#### 조직 변경 알림")
        st.info(
            "조직 변경 이벤트(OrgChangeEvent)가 발생하면 자동으로 알림됩니다.\n"
            "- 팀 이동 시 담당 문서 재할당\n"
            "- 퇴사 시 담당 문서 인수인계\n"
            "- 신규 입사 시 담당 영역 할당"
        )

        st.markdown("---")

        # 문서 할당 알림
        st.markdown("#### 문서 할당 알림")

        # stale 문서 체크
        kbs_for_stale = api_client.list_kbs()
        if not api_failed(kbs_for_stale):
            kb_items_for_stale = kbs_for_stale.get("items", kbs_for_stale.get("kbs", []))
            stale_total = 0
            for kb in kb_items_for_stale:
                kb_id = kb.get("kb_id", kb.get("id", ""))
                stale_result = api_client.get_stale_owners(kb_id)
                if not api_failed(stale_result):
                    stale_items = stale_result.get("items", stale_result.get("stale_owners", []))
                    my_stale = [
                        s for s in stale_items
                        if s.get("owner_user_id", s.get("user_id", "")) == current_user
                    ]
                    stale_total += len(my_stale)
                    if my_stale:
                        st.warning(
                            f"**{kb.get('name', kb_id)}**: {len(my_stale)}건의 문서가 "
                            f"90일 이상 미검증 상태입니다."
                        )
                        for s in my_stale[:5]:
                            st.caption(
                                f"- {s.get('document_title', s.get('title', '-'))} "
                                f"(마지막 검증: {str(s.get('last_verified', '-'))[:10]})"
                            )

            if stale_total == 0:
                st.success("미검증 문서가 없습니다. 모든 담당 문서가 최신 상태입니다.")
        else:
            st.warning("KB 목록을 불러올 수 없어 Stale 문서를 확인할 수 없습니다.")


st.markdown("---")
st.caption("📌 문서 담당자 관리 | OrgChangeEvent 기반 자동 알림 | SSOT: oreo-agents domain/knowledge/ownership/")
