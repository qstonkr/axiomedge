"""담당자 관리 (병합)

구 5_owner_mgmt + admin/7_experts 병합.
3 탭: 담당자 목록, 전문가 프로필, 가용성

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="담당자 관리", page_icon="👥", layout="wide")


import pandas as pd

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("👥 담당자 관리")

tab_owners, tab_experts, tab_availability = st.tabs(["담당자 목록", "전문가 프로필", "가용성"])


# =============================================================================
# 탭 1: 담당자 목록
# =============================================================================
with tab_owners:
    st.subheader("문서/토픽/KB 담당자")
    st.caption("3-Tier 소유권: 문서(Document) → 토픽(Topic) → KB")

    # KB 선택
    kbs_result = api_client.list_kbs()
    if api_failed(kbs_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_owners"):
            st.cache_data.clear()
            st.rerun()
    else:
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        if kb_items:
            kb_options = {
                kb.get("name", kb.get("kb_id", "")): kb.get("kb_id", kb.get("id", ""))
                for kb in kb_items
            }
            selected_kb_name = st.selectbox("KB 선택", options=list(kb_options.keys()), key="owner_kb_select")
            selected_kb_id = kb_options[selected_kb_name]

            # ── 문서 담당자 ──
            st.markdown("#### 문서 담당자 (DocumentOwner)")
            owners_result = api_client.list_document_owners(selected_kb_id)

            if api_failed(owners_result):
                st.error("API 연결 실패. 재시도 해주세요.")
                if st.button("🔄 재시도", key="retry_doc_owners"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                owners = owners_result.get("items", owners_result.get("owners", []))
                if owners:
                    rows = []
                    for owner in owners:
                        rows.append({
                            "문서 ID": owner.get("document_id", "-"),
                            "담당자": owner.get("owner_user_id", owner.get("owner_name", "-")),
                            "백업 담당자": owner.get("backup_owner_user_id") or "-",
                            "할당 유형": owner.get("ownership_type", "-"),
                            "검증 상태": owner.get("verification_status", owner.get("status", "-")),
                            "할당일": (owner.get("created_at") or "-")[:10] if owner.get("created_at") else "-",
                            "검증일": (owner.get("last_verified") or "-")[:10] if owner.get("last_verified") else "-",
                        })
                    df_owners = pd.DataFrame(rows)
                    st.dataframe(df_owners, use_container_width=True, hide_index=True)

                    # 액션 버튼
                    st.markdown("---")
                    action_col1, action_col2, action_col3 = st.columns(3)

                    with action_col1:
                        with st.expander("➕ 담당자 할당"):
                            with st.form("assign_owner_form"):
                                doc_id = st.text_input("문서 ID")
                                user_id = st.text_input("담당자 User ID")
                                owner_name = st.text_input("담당자 이름")
                                submitted = st.form_submit_button("할당")
                                if submitted and doc_id and user_id:
                                    result = api_client.assign_document_owner({
                                        "document_id": doc_id,
                                        "kb_id": selected_kb_id,
                                        "user_id": user_id,
                                        "owner_name": owner_name,
                                    })
                                    if api_failed(result):
                                        st.error("할당 실패")
                                    else:
                                        st.success("담당자가 할당되었습니다.")
                                        st.cache_data.clear()
                                        st.rerun()

                    with action_col2:
                        with st.expander("🔄 소유권 이전"):
                            with st.form("transfer_owner_form"):
                                t_doc_id = st.text_input("문서 ID", key="transfer_doc")
                                new_user_id = st.text_input("새 담당자 User ID")
                                reason = st.text_input("이전 사유")
                                submitted = st.form_submit_button("이전")
                                if submitted and t_doc_id and new_user_id:
                                    result = api_client.transfer_ownership(t_doc_id, {
                                        "new_user_id": new_user_id,
                                        "reason": reason,
                                        "kb_id": selected_kb_id,
                                    })
                                    if api_failed(result):
                                        st.error("이전 실패")
                                    else:
                                        st.success("소유권이 이전되었습니다.")
                                        st.cache_data.clear()
                                        st.rerun()

                    with action_col3:
                        with st.expander("✅ 담당자 검증"):
                            with st.form("verify_owner_form"):
                                v_doc_id = st.text_input("문서 ID", key="verify_doc")
                                verifier = st.text_input("검증자 User ID")
                                submitted = st.form_submit_button("검증")
                                if submitted and v_doc_id and verifier:
                                    result = api_client.verify_document_owner(v_doc_id, {
                                        "verified_by": verifier,
                                        "kb_id": selected_kb_id,
                                    })
                                    if api_failed(result):
                                        st.error("검증 실패")
                                    else:
                                        st.success("담당자가 검증되었습니다.")
                                        st.cache_data.clear()
                                        st.rerun()
                else:
                    st.info("등록된 문서 담당자가 없습니다.")

            # ── 토픽 담당자 ──
            st.markdown("---")
            st.markdown("#### 토픽 담당자 (TopicOwner)")
            topics_result = api_client.list_topic_owners(selected_kb_id)

            if not api_failed(topics_result):
                topics = topics_result.get("items", topics_result.get("owners", topics_result.get("topics", [])))
                if topics:
                    rows = []
                    for t in topics:
                        rows.append({
                            "토픽": t.get("topic", t.get("topic_name", "-")),
                            "담당자": t.get("owner_name", t.get("user_id", "-")),
                            "문서 수": t.get("document_count", 0),
                            "할당일": t.get("assigned_at", "-"),
                        })
                    df_topics = pd.DataFrame(rows)
                    st.dataframe(df_topics, use_container_width=True, hide_index=True)
                else:
                    st.info("등록된 토픽 담당자가 없습니다.")
            else:
                st.warning("토픽 담당자 정보를 가져올 수 없습니다.")
        else:
            st.info("등록된 KB가 없습니다.")


# =============================================================================
# 탭 2: 전문가 프로필
# =============================================================================
with tab_experts:
    st.subheader("전문가 프로필")
    st.caption("ContributorReputation: NOVICE → CONTRIBUTOR → TRUSTED → EXPERT (5개 배지)")

    contributors_result = api_client.list_contributors()

    if api_failed(contributors_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_experts"):
            st.cache_data.clear()
            st.rerun()
    else:
        contributors = contributors_result.get("items", contributors_result.get("contributors", []))
        if contributors:
            rank_icons = {
                "NOVICE": "🌱",
                "CONTRIBUTOR": "🌿",
                "TRUSTED": "🌳",
                "EXPERT": "🏆",
            }

            for contrib in contributors:
                name = contrib.get("name", contrib.get("user_id", "-"))
                rank = contrib.get("rank", contrib.get("reputation", "NOVICE"))
                expertise = contrib.get("expertise_areas", [])
                contribution_count = contrib.get("contribution_count", contrib.get("total_contributions", 0))
                badges = contrib.get("badges", [])
                score = contrib.get("reputation_score", 0)

                rank_icon = rank_icons.get(rank, "🌱")

                with st.container(border=True):
                    col_profile, col_stats, col_badges = st.columns([2, 1, 2])

                    with col_profile:
                        st.markdown(f"**{rank_icon} {name}**")
                        st.caption(f"등급: {rank} | 평판 점수: {score}")
                        if expertise:
                            st.write("전문 분야: " + ", ".join(expertise[:5]))

                    with col_stats:
                        st.metric("기여 수", f"{contribution_count}건")

                    with col_badges:
                        if badges:
                            badge_text = " ".join(
                                [f"🏅 {b}" if isinstance(b, str) else f"🏅 {b.get('name', '')}" for b in badges[:5]]
                            )
                            st.write(badge_text)
                        else:
                            st.caption("배지 없음")
        else:
            st.info("등록된 전문가 프로필이 없습니다.")


# =============================================================================
# 탭 3: 가용성
# =============================================================================
with tab_availability:
    st.subheader("담당자 가용성 관리")
    st.caption("OwnerAvailability: 부재/위임 관리")

    owner_user_id = st.text_input("담당자 User ID 조회", placeholder="예: mslee", key="avail_user_id")

    if owner_user_id:
        avail_result = api_client.get_owner_availability(owner_user_id)

        if api_failed(avail_result):
            st.error("API 연결 실패. 재시도 해주세요.")
            if st.button("🔄 재시도", key="retry_avail"):
                st.cache_data.clear()
                st.rerun()
        else:
            avail = avail_result
            current_status = avail.get("status", avail.get("availability_status", "AVAILABLE"))
            delegate = avail.get("delegate_user_id", avail.get("delegate", ""))
            start_date = avail.get("unavailable_from", avail.get("start_date", ""))
            end_date = avail.get("unavailable_until", avail.get("end_date", ""))

            status_badge = {
                "AVAILABLE": "🟢 가용",
                "UNAVAILABLE": "🔴 부재",
                "PARTIAL": "🟡 부분 가용",
            }.get(current_status, f"⚪ {current_status}")

            st.info(f"현재 상태: {status_badge}")
            if delegate:
                st.write(f"위임자: **{delegate}**")
            if start_date:
                st.write(f"부재 기간: {start_date} ~ {end_date}")

            st.markdown("---")

            # ── 가용성 업데이트 폼 ──
            st.markdown("#### 가용성 수정")
            with st.form("update_availability_form"):
                new_status = st.selectbox(
                    "상태",
                    options=["AVAILABLE", "UNAVAILABLE", "PARTIAL"],
                    index=["AVAILABLE", "UNAVAILABLE", "PARTIAL"].index(current_status)
                    if current_status in ["AVAILABLE", "UNAVAILABLE", "PARTIAL"]
                    else 0,
                )
                new_delegate = st.text_input("위임자 User ID", value=delegate or "")
                col_from, col_to = st.columns(2)
                with col_from:
                    new_start = st.date_input("부재 시작일")
                with col_to:
                    new_end = st.date_input("부재 종료일")
                note = st.text_area("메모", placeholder="부재 사유 (선택)")

                submitted = st.form_submit_button("저장", type="primary")
                if submitted:
                    body = {
                        "status": new_status,
                        "delegate_user_id": new_delegate or None,
                        "unavailable_from": str(new_start),
                        "unavailable_until": str(new_end),
                    }
                    if note:
                        body["note"] = note

                    result = api_client.update_owner_availability(owner_user_id, body)
                    if api_failed(result):
                        st.error("가용성 업데이트 실패")
                    else:
                        st.success("가용성이 업데이트되었습니다.")
                        st.cache_data.clear()
                        st.rerun()
    else:
        st.info("담당자 User ID를 입력하여 가용성을 조회하세요.")
