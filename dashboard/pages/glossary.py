"""용어집 (병합)

구 4_glossary + admin/4_glossary 병합.
4 탭: 용어 목록, 승인 대기, 통계, 쿼리 확장

Created: 2026-02-20
Refactored: 2026-04-04 - Extract helpers to glossary_components.py
"""

import streamlit as st

st.set_page_config(page_title="용어집", page_icon="📖", layout="wide")


import pandas as pd  # noqa: E402

from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402
from services.api_client import api_failed  # noqa: E402
from pages.glossary_components import (  # noqa: E402
    render_dict_subtab,
    render_similarity_check,
    render_distribution_analysis,
    render_discovered_synonyms,
    render_pending_list,
    render_stats_tab,
    render_expansion_tab,
)

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📖 용어집")

tab_list, tab_pending, tab_stats, tab_expansion = st.tabs(
    ["용어 관리", "승인 대기", "통계", "쿼리 확장"]
)


# =============================================================================
# 탭 1: 용어 관리 (CSV 임포트 통합 + 단어/용어 분리 조회)
# =============================================================================
with tab_list:
    st.subheader("용어 관리")

    # ── CSV 임포트 (통합 - 자동 판별) ──
    with st.expander("📥 CSV 가져오기 (단어/용어 자동 판별)", expanded=False):
        st.caption("CSV 파일을 업로드하면 **구성정보** 컬럼 기준으로 단어/용어를 자동 판별합니다.")
        st.caption("구성정보 1단어 → 단어(word) | 구성정보 2단어+ → 용어(term)")
        st.caption("필수 컬럼: 물리명(term) 또는 논리명(term_ko)")

        csv_files = st.file_uploader(
            "CSV 파일 선택 (여러 개 가능)",
            type=["csv"],
            accept_multiple_files=True,
            key="unified_csv_upload",
        )
        csv_enc = st.selectbox("인코딩", options=["utf-8", "euc-kr", "cp949"], key="unified_csv_enc")

        if csv_files:
            try:
                preview_df = pd.read_csv(csv_files[0], nrows=5, encoding=csv_enc)
                csv_files[0].seek(0)
                st.markdown(f"**미리보기: {csv_files[0].name} (첫 5행)**")
                st.dataframe(preview_df, use_container_width=True, hide_index=True)
                if len(csv_files) > 1:
                    st.info(f"총 {len(csv_files)}개 파일 선택됨")

                required = {"물리명", "논리명"}
                alt_required = {"term", "term_ko"}
                cols = set(preview_df.columns)
                if not (required & cols) and not (alt_required & cols):
                    st.error(f"필수 컬럼이 없습니다. 필요: {required} 또는 {alt_required}")
                else:
                    if st.button(
                        f"📥 가져오기 ({len(csv_files)}개 파일)",
                        key="unified_import_btn",
                        type="primary",
                    ):
                        total_imported = 0
                        total_skipped = 0
                        total_words = 0
                        total_terms = 0
                        all_errors = []

                        for csv_file in csv_files:
                            file_bytes = csv_file.read()
                            result = api_client.import_glossary_csv(
                                file_bytes=file_bytes,
                                filename=csv_file.name,
                                encoding=csv_enc,
                                term_type="term",
                            )
                            if api_failed(result):
                                all_errors.append(
                                    f"{csv_file.name}: {result.get('error', 'Unknown')}"
                                )
                            else:
                                total_imported += result.get("imported", 0)
                                total_skipped += result.get("skipped", 0)
                                total_words += result.get("auto_detected_words", 0)
                                total_terms += result.get("auto_detected_terms", 0)

                        if all_errors:
                            st.error(f"일부 실패: {'; '.join(all_errors[:5])}")
                        st.success(
                            f"임포트 완료: {len(csv_files)}개 파일, "
                            f"등록 {total_imported:,}건 "
                            f"(단어 {total_words:,} + 용어 {total_terms:,}), "
                            f"건너뜀 {total_skipped:,}건"
                        )
                        st.cache_data.clear()
                        st.rerun()
            except Exception as e:
                st.error(f"파일 읽기 실패: {e}")

    st.markdown("---")

    # ── 조회/관리는 탭 분리 유지 ──
    sub_word, sub_term = st.tabs(["📖 단어사전", "📚 용어사전"])

    with sub_word:
        st.caption("원자 단어 (영문 약어 ↔ 한국어 매핑, L1 exact match)")
        render_dict_subtab("word", "단어사전", "word")

    with sub_term:
        st.caption("복합 용어 (유의어/약어 포함, L2 유사도 매칭)")
        render_dict_subtab("term", "용어사전", "term")

    st.markdown("---")

    # ── 용어 추가 폼 ──
    with st.expander("➕ 새 용어 추가"):
        with st.form("create_term_form"):
            new_term = st.text_input("용어")
            new_definition = st.text_area("정의")
            c1, c2, c3 = st.columns(3)
            with c1:
                new_category = st.text_input("카테고리", placeholder="예: 인프라, 비즈니스")
            with c2:
                new_kb_id = st.text_input("KB ID", value="global-standard")
            with c3:
                new_synonyms = st.text_input("동의어 (쉼표 구분)", placeholder="예: K8s, 쿠버네티스")

            submitted = st.form_submit_button("추가", type="primary")
            if submitted:
                if not new_term or not new_definition:
                    st.error("용어와 정의는 필수입니다.")
                else:
                    body = {
                        "term": new_term,
                        "definition": new_definition,
                        "source": "MANUAL",
                        "category": new_category,
                        "kb_id": new_kb_id,
                    }
                    if new_synonyms:
                        body["synonyms"] = [
                            s.strip() for s in new_synonyms.split(",") if s.strip()
                        ]

                    result = api_client.create_glossary_term(body)
                    if api_failed(result):
                        st.error("용어 추가 실패")
                    else:
                        st.success(f"'{new_term}' 용어가 추가되었습니다.")
                        st.cache_data.clear()
                        st.rerun()

    # ── Global 승격 ──
    with st.expander("🌐 Global로 승격"):
        st.caption("KB 전용 용어를 전사 공통(Global) 용어로 승격합니다.")
        promote_term_id = st.text_input("승격할 용어 ID", key="promote_term_id")
        if promote_term_id:
            if st.button("승격 실행", key="promote_btn", type="primary"):
                result = api_client.promote_glossary_term_to_global(promote_term_id)
                if api_failed(result):
                    st.error("승격 실패")
                else:
                    st.success("용어가 Global로 승격되었습니다.")
                    st.cache_data.clear()
                    st.rerun()

    # ── 용어 수정/삭제 ──
    with st.expander("✏️ 용어 수정 / 삭제"):
        edit_term_id = st.text_input("수정할 용어 ID", key="edit_term_id")
        if edit_term_id:
            term_detail = api_client.get_glossary_term(edit_term_id)
            if not api_failed(term_detail):
                with st.form("edit_term_form"):
                    ed_term = st.text_input("용어", value=term_detail.get("term", ""))
                    ed_definition = st.text_area(
                        "정의", value=term_detail.get("definition", "")
                    )
                    ed_category = st.text_input(
                        "카테고리", value=term_detail.get("category", "")
                    )

                    ec1, ec2 = st.columns(2)
                    with ec1:
                        if st.form_submit_button("저장", type="primary"):
                            result = api_client.update_glossary_term(edit_term_id, {
                                "term": ed_term,
                                "definition": ed_definition,
                                "category": ed_category,
                            })
                            if api_failed(result):
                                st.error("수정 실패")
                            else:
                                st.success("수정 완료")
                                st.cache_data.clear()
                                st.rerun()

                # ── 동의어 관리 (Synonym Editor) ──
                st.markdown("**동의어 관리**")
                current_synonyms = term_detail.get("synonyms", [])
                if current_synonyms:
                    syn_cols = st.columns(min(len(current_synonyms), 5))
                    for syn_idx, syn in enumerate(current_synonyms):
                        col_idx = syn_idx % min(len(current_synonyms), 5)
                        with syn_cols[col_idx]:
                            sc1, sc2 = st.columns([3, 1])
                            with sc1:
                                st.code(syn, language=None)
                            with sc2:
                                if st.button(
                                    "X",
                                    key=f"rm_syn_{edit_term_id}_{syn_idx}",
                                    help=f"'{syn}' 동의어 삭제",
                                ):
                                    rm_result = api_client.remove_synonym(
                                        edit_term_id, syn
                                    )
                                    if not api_failed(rm_result):
                                        st.success(f"'{syn}' 삭제됨")
                                        st.cache_data.clear()
                                        st.rerun()
                                    else:
                                        st.error(
                                            f"삭제 실패: {rm_result.get('error', '')}"
                                        )
                else:
                    st.caption("등록된 동의어가 없습니다.")

                # Add new synonym
                add_syn_col1, add_syn_col2 = st.columns([3, 1])
                with add_syn_col1:
                    new_syn = st.text_input(
                        "새 동의어 추가",
                        placeholder="동의어 입력",
                        key=f"new_syn_{edit_term_id}",
                        label_visibility="collapsed",
                    )
                with add_syn_col2:
                    if st.button(
                        "추가", key=f"add_syn_btn_{edit_term_id}", type="primary"
                    ):
                        if new_syn and new_syn.strip():
                            add_result = api_client.add_synonym_to_standard(
                                standard_term_id=edit_term_id,
                                synonym=new_syn.strip(),
                            )
                            if not api_failed(add_result):
                                st.success(f"'{new_syn.strip()}' 동의어 추가됨")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(
                                    f"추가 실패: {add_result.get('error', '')}"
                                )
                        else:
                            st.warning("동의어를 입력해 주세요.")
            else:
                st.warning("해당 ID의 용어를 찾을 수 없습니다.")

        del_term_id = st.text_input("삭제할 용어 ID", key="del_term_id")
        if del_term_id and st.button("🗑️ 삭제 실행", key="delete_term_btn"):
            result = api_client.delete_glossary_term(del_term_id)
            if api_failed(result):
                st.error("삭제 실패")
            else:
                st.success("삭제 완료")
                st.cache_data.clear()
                st.rerun()


# =============================================================================
# 탭 2: 승인 대기
# =============================================================================
with tab_pending:
    st.subheader("승인 대기 용어")

    # ── 유사도 기반 자동 정리 (분포 분석 실행 후 노출) ──
    if "dist_result" not in st.session_state:
        st.info(
            "💡 아래 **📊 유사도 점수 분포 분석**을 먼저 실행하면, "
            "유사도 체크 & 자동 정리 기능이 활성화됩니다."
        )

    if "dist_result" in st.session_state:
        render_similarity_check()

    render_distribution_analysis()

    st.markdown("---")

    render_discovered_synonyms()

    st.markdown("---")

    render_pending_list()


# =============================================================================
# 탭 3: 통계
# =============================================================================
with tab_stats:
    render_stats_tab()


# =============================================================================
# 탭 4: 쿼리 확장
# =============================================================================
with tab_expansion:
    render_expansion_tab()
