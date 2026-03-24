"""용어집 (병합)

구 4_glossary + admin/4_glossary 병합.
4 탭: 용어 목록, 승인 대기, 통계, 쿼리 확장

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="용어집", page_icon="📖", layout="wide")


import pandas as pd
import plotly.express as px

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📖 용어집")

tab_list, tab_pending, tab_stats, tab_expansion = st.tabs(
    ["용어 관리", "승인 대기", "통계", "쿼리 확장"]
)


# =============================================================================
# Helper: 사전 유형별 목록 + CSV 임포트 + 삭제 서브탭 렌더링
# =============================================================================
def _render_dict_subtab(term_type_value: str, term_type_label: str, key_prefix: str):
    """단어사전/용어사전 서브탭 공통 렌더링."""

    # ── 필터 ──
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        search_q = st.text_input("검색", placeholder="검색어 입력", key=f"{key_prefix}_search")
    with fc2:
        PAGE_SIZE = 100
        page = st.number_input("페이지", min_value=1, value=1, key=f"{key_prefix}_page")
    with fc3:
        st.write("")  # spacer

    terms_result = api_client.list_glossary_terms(
        status="approved", term_type=term_type_value, page=page, page_size=PAGE_SIZE
    )

    if api_failed(terms_result):
        st.error("API 연결 실패.")
        return

    terms = terms_result.get("items", terms_result.get("terms", []))
    total = terms_result.get("total", len(terms))
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    st.caption(f"총 {total:,}개 (페이지 {page}/{total_pages})")

    if search_q:
        terms = [
            t for t in terms
            if search_q.lower() in t.get("term", "").lower()
            or search_q.lower() in (t.get("term_ko") or "").lower()
            or search_q.lower() in t.get("definition", "").lower()
        ]

    if terms:
        rows = []
        for t in terms:
            rows.append({
                "물리명": t.get("term", "-"),
                "논리명": t.get("term_ko") or "-",
                "정의": (t.get("definition", "-") or "-")[:80],
                "카테고리": t.get("category", "-"),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info(f"등록된 {term_type_label}이 없습니다.")

    st.markdown("---")

    # ── CSV 임포트 ──
    with st.expander(f"📥 {term_type_label} CSV 가져오기"):
        st.caption(f"{term_type_label} CSV 파일을 가져옵니다. 필수 컬럼: 물리명(term), 논리명(term_ko)")
        csv_file = st.file_uploader("CSV 파일 선택", type=["csv"], key=f"{key_prefix}_csv_upload")
        csv_enc = st.selectbox("인코딩", options=["utf-8", "euc-kr", "cp949"], key=f"{key_prefix}_csv_enc")

        if csv_file is not None:
            try:
                preview_df = pd.read_csv(csv_file, nrows=5, encoding=csv_enc)
                csv_file.seek(0)
                st.markdown("**미리보기 (첫 5행)**")
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

                required = {"물리명", "논리명"}
                alt_required = {"term", "term_ko"}
                cols = set(preview_df.columns)
                if not (required & cols) and not (alt_required & cols):
                    st.error(f"필수 컬럼이 없습니다. 필요: {required} 또는 {alt_required}")
                else:
                    if st.button(f"📥 {term_type_label} 가져오기", key=f"{key_prefix}_import_btn", type="primary"):
                        file_bytes = csv_file.read()
                        result = api_client.import_glossary_csv(
                            file_bytes=file_bytes,
                            filename=csv_file.name,
                            encoding=csv_enc,
                            term_type=term_type_value,
                        )
                        if api_failed(result):
                            st.error(f"임포트 실패: {result.get('error', 'Unknown error')}")
                        else:
                            st.success(
                                f"임포트 완료: 전체 {result.get('total_rows', 0)}행, "
                                f"등록 {result.get('imported_count', 0)}건, "
                                f"건너뜀 {result.get('skipped_count', 0)}건"
                            )
                            if result.get("errors"):
                                for err in result["errors"][:5]:
                                    st.warning(err)
                            st.cache_data.clear()
                            st.rerun()
            except Exception as e:
                st.error(f"파일 읽기 실패: {e}")

    # ── 전체 삭제 + 재임포트 ──
    with st.expander(f"🗑️ {term_type_label} 전체 삭제"):
        st.caption(f"현재 approved 상태인 {term_type_label} 전체를 삭제합니다. 재임포트 전에 사용하세요.")
        st.warning(f"이 작업은 {term_type_label}의 모든 approved 데이터를 삭제합니다.")
        if st.button(f"🗑️ {term_type_label} 전체 삭제 실행", key=f"{key_prefix}_delete_all_btn"):
            result = api_client.delete_glossary_by_type(term_type=term_type_value)
            if api_failed(result):
                st.error(f"삭제 실패: {result.get('error', '')}")
            else:
                st.success(f"{result.get('deleted_count', 0)}건 삭제 완료")
                st.cache_data.clear()
                st.rerun()


# =============================================================================
# 탭 1: 용어 관리 (단어사전 / 용어사전 서브탭)
# =============================================================================
with tab_list:
    st.subheader("용어 관리")

    sub_word, sub_term = st.tabs(["단어사전", "용어사전"])

    with sub_word:
        st.caption("원자 단어 (L1 exact match + L1.5 형태소 분해용)")
        _render_dict_subtab("word", "단어사전", "word")

    with sub_term:
        st.caption("복합 용어 (L2 유사도 매칭용)")
        _render_dict_subtab("term", "용어사전", "term")

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
                        body["synonyms"] = [s.strip() for s in new_synonyms.split(",") if s.strip()]

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
                    st.success(f"용어가 Global로 승격되었습니다.")
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
                    ed_definition = st.text_area("정의", value=term_detail.get("definition", ""))
                    ed_category = st.text_input("카테고리", value=term_detail.get("category", ""))

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
            else:
                st.warning("해당 ID의 용어를 찾을 수 없습니다.")

        del_term_id = st.text_input("삭제할 용어 ID", key="del_term_id")
        if del_term_id:
            if st.button("🗑️ 삭제 실행", key="delete_term_btn"):
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
        st.info("💡 아래 **📊 유사도 점수 분포 분석**을 먼저 실행하면, 유사도 체크 & 자동 정리 기능이 활성화됩니다.")

    if "dist_result" in st.session_state:
      with st.expander("🔍 유사도 체크 & 자동 정리", expanded=False):
        st.caption("PENDING 용어를 페이지 단위(50건)로 표준 용어사전과 3-Layer 매칭합니다.")

        # 분포 분석 결과 기반 추천 임계값 표시
        dr = st.session_state["dist_result"]
        rf_stats = dr.get("score_stats", {}).get("rapidfuzz", {})
        jac_stats = dr.get("score_stats", {}).get("jaccard", {})
        rf_p90 = rf_stats.get("p90", 0)
        rf_p50 = rf_stats.get("p50", 0)
        jac_p90 = jac_stats.get("p90", 0)
        jac_p50 = jac_stats.get("p50", 0)

        if rf_p90 > 0:
            rec_auto = round(rf_p90, 2)
            rec_review = round(rf_p50, 2)
            st.info(
                f"**추천 임계값** (분포 기반 자동 산출)\n\n"
                f"| Zone | RapidFuzz | Jaccard |\n"
                f"|------|-----------|--------|\n"
                f"| AUTO_MATCH | ≥ {rec_auto} | ≥ {round(jac_p90, 2)} |\n"
                f"| REVIEW | {rec_review} ~ {rec_auto} | {round(jac_p50, 2)} ~ {round(jac_p90, 2)} |\n"
                f"| NEW_TERM | < {rec_review} | < {round(jac_p50, 2)} |"
            )
            default_threshold = rec_auto
        else:
            default_threshold = 0.7

        threshold = st.slider(
            "유사도 기준점", 0.5, 1.0, default_threshold, 0.05,
            help="이 값 이상이면 표준 용어와 동일/유사한 것으로 판단",
            key="sim_threshold",
        )

        # 페이지 컨트롤
        sim_page = st.session_state.get("sim_page", 1)

        sim_col1, sim_col2, sim_col3 = st.columns([2, 2, 2])
        with sim_col1:
            if st.button("🔍 유사도 체크", type="secondary", key="sim_check_btn"):
                st.session_state["sim_page"] = 1
                sim_page = 1
                with st.spinner(f"유사도 분석 중... (페이지 {sim_page}, 50건)"):
                    result = api_client.check_pending_similarity(threshold, page=sim_page, page_size=50)
                    if not api_failed(result):
                        st.session_state["sim_check_result"] = result
                    else:
                        st.error(f"유사도 체크 실패: {result.get('error', '')}")

        if "sim_check_result" in st.session_state:
            r = st.session_state["sim_check_result"]
            cur_page = r.get("page", 1)
            has_next = r.get("has_next", False)

            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            with mc1:
                st.metric("전체 PENDING", r.get("total_pending", 0))
            with mc2:
                st.metric("AUTO_MATCH", r.get("matched_count", 0))
            with mc3:
                st.metric("REVIEW", r.get("review_count", r.get("unmatched_count", 0)))
            with mc4:
                st.metric("NEW_TERM", r.get("new_term_count", 0))
            with mc5:
                st.metric("페이지", f"{cur_page}")

            matches = r.get("matches", [])
            if matches:
                display_rows = []
                for m in matches:
                    matched_std = m.get("matched_standard", m.get("standard_term", "-"))
                    matched_std_ko = m.get("matched_standard_ko") or ""

                    # 복합어 형태소 매칭: 모든 구성 요소 표시
                    morph_matches = m.get("morpheme_matches")
                    if morph_matches and len(morph_matches) > 1:
                        parts = []
                        for mm in morph_matches:
                            ko = mm.get("standard_term_ko") or ""
                            parts.append(f"{mm['standard_term']}({ko})" if ko else mm["standard_term"])
                        matched_display = " + ".join(parts)
                    elif matched_std_ko:
                        matched_display = f"{matched_std} ({matched_std_ko})"
                    else:
                        matched_display = matched_std

                    row = {
                        "용어": m.get("term", m.get("pending_term", "-")),
                        "매칭 표준어": matched_display,
                        "유사도": f"{m.get('similarity_score', m.get('similarity', m.get('score', 0))):.3f}",
                        "Zone": m.get("decision_zone", m.get("zone", "-")),
                    }
                    cs = m.get("channel_scores", {})
                    if cs:
                        row["S_edit"] = f"{cs.get('edit', 0):.3f}" if cs.get("edit") else "-"
                        row["S_sparse"] = f"{cs.get('sparse', 0):.3f}" if cs.get("sparse") else "-"
                        row["S_dense"] = f"{cs.get('dense', 0):.3f}" if cs.get("dense") else "-"
                    display_rows.append(row)

                df_matches = pd.DataFrame(display_rows)

                def _zone_color(val):
                    if val == "auto_match":
                        return "background-color: #d4edda"
                    elif val == "review":
                        return "background-color: #fff3cd"
                    elif val == "new_term":
                        return "background-color: #f8d7da"
                    return ""

                styled = df_matches.style.map(_zone_color, subset=["Zone"])
                st.dataframe(styled, use_container_width=True, hide_index=True)

            # 페이지 네비게이션 + 액션 버튼
            nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([2, 2, 2, 2])
            with nav_col1:
                if cur_page > 1:
                    if st.button("⬅️ 이전 페이지", key="sim_prev_page"):
                        new_page = cur_page - 1
                        st.session_state["sim_page"] = new_page
                        with st.spinner(f"페이지 {new_page} 분석 중..."):
                            result = api_client.check_pending_similarity(threshold, page=new_page, page_size=50)
                            if not api_failed(result):
                                st.session_state["sim_check_result"] = result
                                st.rerun()
            with nav_col2:
                if has_next:
                    if st.button("➡️ 다음 페이지", key="sim_next_page"):
                        new_page = cur_page + 1
                        st.session_state["sim_page"] = new_page
                        with st.spinner(f"페이지 {new_page} 분석 중..."):
                            result = api_client.check_pending_similarity(threshold, page=new_page, page_size=50)
                            if not api_failed(result):
                                st.session_state["sim_check_result"] = result
                                st.rerun()
            with nav_col3:
                auto_match_ids = [
                    m.get("term_id") for m in matches
                    if m.get("decision_zone") == "auto_match" and m.get("term_id")
                ]
                if auto_match_ids:
                    if st.button(f"🗑️ AUTO_MATCH {len(auto_match_ids)}건 삭제", type="primary", key="sim_cleanup_btn"):
                        with st.spinner("삭제 중..."):
                            cleanup = api_client.cleanup_pending_by_similarity(threshold, term_ids=auto_match_ids)
                            if not api_failed(cleanup):
                                st.success(
                                    f"{cleanup.get('deleted_count', 0)}개 삭제 완료 "
                                    f"(남은 PENDING: {cleanup.get('remaining_pending', 0)}개)"
                                )
                                if "sim_check_result" in st.session_state:
                                    del st.session_state["sim_check_result"]
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(f"삭제 실패: {cleanup.get('error', '')}")
            with nav_col4:
                # AUTO_MATCH 중 표준 용어 ID가 있는 항목을 동의어로 일괄 등록
                synonym_candidates = [
                    m for m in matches
                    if m.get("decision_zone") == "auto_match"
                    and m.get("matched_standard_id")
                    and m.get("term_id")
                ]
                if synonym_candidates:
                    if st.button(f"🔗 AUTO_MATCH {len(synonym_candidates)}건 동의어 등록", key="sim_synonym_bulk_btn"):
                        success_count = 0
                        for m in synonym_candidates:
                            pending_term = m.get("term", m.get("pending_term", ""))
                            res = api_client.add_synonym_to_standard(
                                standard_term_id=m["matched_standard_id"],
                                synonym=pending_term,
                                delete_pending_id=m["term_id"],
                            )
                            if not api_failed(res):
                                success_count += 1
                        st.success(f"{success_count}/{len(synonym_candidates)}건 동의어 등록 완료")
                        if "sim_check_result" in st.session_state:
                            del st.session_state["sim_check_result"]
                        st.cache_data.clear()
                        st.rerun()

            # ── 개별 동의어 등록 (REVIEW zone) ──
            review_matches = [
                m for m in matches
                if m.get("decision_zone") == "review"
                and m.get("matched_standard_id")
                and m.get("term_id")
            ]
            if review_matches:
                with st.expander(f"🔗 REVIEW {len(review_matches)}건 개별 동의어 등록", expanded=False):
                    st.caption("REVIEW zone 용어를 표준 용어의 동의어로 개별 등록할 수 있습니다.")
                    for idx, m in enumerate(review_matches):
                        pending_term = m.get("term", m.get("pending_term", "-"))
                        matched_std = m.get("matched_standard", "-")
                        matched_std_ko = m.get("matched_standard_ko") or ""
                        score = m.get("similarity_score", m.get("similarity", 0))
                        std_display = f"{matched_std} ({matched_std_ko})" if matched_std_ko else matched_std

                        rc1, rc2, rc3 = st.columns([3, 3, 2])
                        with rc1:
                            st.text(f"{pending_term}")
                        with rc2:
                            st.text(f"→ {std_display} ({score:.3f})")
                        with rc3:
                            if st.button("동의어 등록", key=f"syn_review_{idx}"):
                                res = api_client.add_synonym_to_standard(
                                    standard_term_id=m["matched_standard_id"],
                                    synonym=pending_term,
                                    delete_pending_id=m["term_id"],
                                )
                                if not api_failed(res):
                                    st.success(f"'{pending_term}' → '{matched_std}' 동의어 등록 완료")
                                    st.cache_data.clear()
                                    st.rerun()
                                else:
                                    st.error(f"등록 실패: {res.get('error', '')}")

    # ── 유사도 점수 분포 분석 (Phase 0) ──
    if "dist_result" in st.session_state:
        # 분석 완료 → 컴팩트 요약 + 재분석 버튼
        dr = st.session_state["dist_result"]
        rf_stats = dr.get("score_stats", {}).get("rapidfuzz", {})
        jac_stats = dr.get("score_stats", {}).get("jaccard", {})
        summary_cols = st.columns([6, 1])
        with summary_cols[0]:
            st.success(
                f"**분포 분석 완료** — "
                f"RapidFuzz p90={rf_stats.get('p90', 0):.2f}, p50={rf_stats.get('p50', 0):.2f} | "
                f"Jaccard p90={jac_stats.get('p90', 0):.2f}, p50={jac_stats.get('p50', 0):.2f}"
            )
        with summary_cols[1]:
            if st.button("🔄 재분석", key="dist_reanalyze_btn"):
                del st.session_state["dist_result"]
                if "sim_check_result" in st.session_state:
                    del st.session_state["sim_check_result"]
                st.rerun()

        # 상세 분포 히스토그램 (접힌 상태로 제공)
        with st.expander("📊 분포 상세 보기", expanded=False):
            st.caption(
                f"표준 용어: {dr.get('standard_count', 0):,}개 | "
                f"PENDING: {dr.get('pending_count', 0):,}개 | "
                f"샘플: {dr.get('sample_size', 0):,}개"
            )
            channel_map = {
                "RapidFuzz (S_edit)": ("rapidfuzz_scores", "rapidfuzz"),
                "N-gram Jaccard (S_sparse)": ("jaccard_scores", "jaccard"),
                "Dense Cosine (S_dense)": ("dense_cosine_scores", "dense_cosine"),
            }
            all_stats = dr.get("score_stats", {})
            for ch_label, (scores_key, stats_key) in channel_map.items():
                scores = dr.get(scores_key, [])
                stats = all_stats.get(stats_key, {})
                if not scores:
                    if "Dense" in ch_label:
                        st.markdown(f"**{ch_label}** — 분포 분석 미지원 (임베딩 비용으로 제외, 실제 매칭에서 사용)")
                    else:
                        st.markdown(f"**{ch_label}** — 데이터 없음")
                    continue
                st.markdown(f"**{ch_label}** — count={int(stats.get('count', 0))}, "
                            f"mean={stats.get('mean', 0):.3f}, "
                            f"p50={stats.get('p50', 0):.3f}, "
                            f"p90={stats.get('p90', 0):.3f}, "
                            f"p95={stats.get('p95', 0):.3f}")
                fig = px.histogram(
                    x=scores, nbins=20,
                    title=f"{ch_label} Score Distribution",
                    labels={"x": "Score", "count": "Count"},
                )
                fig.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=250)
                st.plotly_chart(fig, use_container_width=True)
    else:
        # 분석 미실행 → 분석 버튼 표시
        with st.expander("📊 유사도 점수 분포 분석", expanded=True):
            st.caption("PENDING 용어 샘플(최대 100개)에 대해 채널별 유사도 점수 분포를 분석합니다. "
                       "임계값 캘리브레이션에 활용합니다.")
            if st.button("📊 분포 분석 실행", type="primary", key="dist_btn"):
                with st.spinner("채널별 점수 분포 수집 중 (최대 30초)..."):
                    dist_result = api_client.get_similarity_distribution()
                    if not api_failed(dist_result):
                        st.session_state["dist_result"] = dist_result
                        st.rerun()
                    else:
                        st.error(f"분포 분석 실패: {dist_result.get('error', '')}")

    st.markdown("---")

    PENDING_PAGE_SIZE = 20
    pending_page = st.number_input("페이지", min_value=1, value=1, key="pending_page")

    pending_result = api_client.list_glossary_terms(
        status="pending", page=pending_page, page_size=PENDING_PAGE_SIZE
    )

    if api_failed(pending_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_pending"):
            st.cache_data.clear()
            st.rerun()
    else:
        pending_terms = pending_result.get("items", pending_result.get("terms", []))
        pending_total = pending_result.get("total", len(pending_terms))
        pending_total_pages = max(1, (pending_total + PENDING_PAGE_SIZE - 1) // PENDING_PAGE_SIZE)

        if pending_terms:
            st.info(f"승인 대기 중인 용어: {pending_total}개 (페이지 {pending_page}/{pending_total_pages})")

            for term in pending_terms:
                term_id = term.get("term_id", term.get("id", "-"))
                term_name = term.get("term", "-")
                definition = term.get("definition", "-")
                source = term.get("source", term.get("term_source", "-"))
                category = term.get("category", "-")

                source_icon = {"AUTO": "🤖", "MANUAL": "✋", "LLM": "🧠"}.get(source, "")

                with st.container(border=True):
                    st.markdown(f"**{term_name}** {source_icon}")
                    st.write(definition)
                    st.caption(f"소스: {source} | 카테고리: {category} | ID: {term_id}")

                    btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])

                    with btn_col1:
                        if st.button("✅ 승인", key=f"approve_{term_id}", type="primary"):
                            result = api_client.approve_glossary_term(term_id, approved_by="admin")
                            if api_failed(result):
                                st.error("승인 실패")
                            else:
                                st.success(f"'{term_name}' 승인 완료")
                                st.cache_data.clear()
                                st.rerun()

                    with btn_col2:
                        if st.button("❌ 거부", key=f"reject_{term_id}"):
                            st.session_state[f"show_reject_{term_id}"] = True

                    if st.session_state.get(f"show_reject_{term_id}", False):
                        reject_reason = st.text_input("거부 사유", key=f"reason_{term_id}")
                        if st.button("거부 확인", key=f"confirm_reject_{term_id}"):
                            result = api_client.reject_glossary_term(
                                term_id, rejected_by="admin", reason=reject_reason
                            )
                            if api_failed(result):
                                st.error("거부 실패")
                            else:
                                st.success(f"'{term_name}' 거부 완료")
                                st.session_state[f"show_reject_{term_id}"] = False
                                st.cache_data.clear()
                                st.rerun()
        else:
            st.success("승인 대기 중인 용어가 없습니다.")


# =============================================================================
# 탭 3: 통계
# =============================================================================
with tab_stats:
    st.subheader("용어집 통계")

    # API total 필드를 활용하여 정확한 카운트 조회 (page_size=1로 최소 데이터)
    word_approved = api_client.list_glossary_terms(status="approved", term_type="word", page_size=1)
    term_approved = api_client.list_glossary_terms(status="approved", term_type="term", page_size=1)
    all_pending = api_client.list_glossary_terms(status="pending", page_size=1)
    all_total = api_client.list_glossary_terms(page_size=1)

    if api_failed(all_total):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_glossary_stats"):
            st.cache_data.clear()
            st.rerun()
    else:
        word_count = word_approved.get("total", 0) if not api_failed(word_approved) else 0
        term_count = term_approved.get("total", 0) if not api_failed(term_approved) else 0
        pending_count = all_pending.get("total", 0) if not api_failed(all_pending) else 0
        total_count = all_total.get("total", 0)
        approved_count = word_count + term_count

        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("전체", f"{total_count:,}개")
        with m2:
            st.metric("승인됨", f"{approved_count:,}개")
        with m3:
            st.metric("단어사전 (word)", f"{word_count:,}개")
        with m4:
            st.metric("용어사전 (term)", f"{term_count:,}개")
        with m5:
            st.metric("대기 중", f"{pending_count:,}개")

        st.markdown("---")

        # ── 사전 유형별 분포 ──
        type_data = {"단어사전 (word)": word_count, "용어사전 (term)": term_count, "대기 중 (pending)": pending_count}
        type_data = {k: v for k, v in type_data.items() if v > 0}

        if type_data:
            fig_type = px.pie(
                names=list(type_data.keys()),
                values=list(type_data.values()),
                title="사전 유형별 분포",
                hole=0.3,
                color_discrete_sequence=["#2196F3", "#4CAF50", "#FFC107"],
            )
            fig_type.update_layout(margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_type, use_container_width=True)

        # ── 카테고리별 분포 (approved 500건 샘플) ──
        sample_result = api_client.list_glossary_terms(status="approved", page_size=500)
        if not api_failed(sample_result):
            sample_terms = sample_result.get("items", sample_result.get("terms", []))
            if sample_terms:
                categories = {}
                for t in sample_terms:
                    cat = t.get("category", "미분류") or "미분류"
                    categories[cat] = categories.get(cat, 0) + 1

                if categories:
                    fig_cat = px.pie(
                        names=list(categories.keys()),
                        values=list(categories.values()),
                        title=f"카테고리별 분포 (상위 500건 샘플)",
                        hole=0.3,
                    )
                    fig_cat.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig_cat, use_container_width=True)

        if total_count == 0:
            st.info("용어 데이터가 없습니다.")


# =============================================================================
# 탭 4: 쿼리 확장
# =============================================================================
with tab_expansion:
    st.subheader("쿼리 확장 (QueryExpander / GlossaryQueryExpander)")
    st.caption("용어와 동의어 매핑을 통한 검색 확장을 시각화합니다.")

    # 승인된 용어에서 동의어 매핑 추출
    expansion_result = api_client.list_glossary_terms(status="approved", page_size=500)

    if api_failed(expansion_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_expansion"):
            st.cache_data.clear()
            st.rerun()
    else:
        approved_terms = expansion_result.get("items", expansion_result.get("terms", []))

        if approved_terms:
            # ── 동의어 매핑 테이블 ──
            expansion_rows = []
            for t in approved_terms:
                term_name = t.get("term", "-")
                synonyms = t.get("synonyms", [])
                abbreviations = t.get("abbreviations", t.get("abbr", []))

                all_expansions = []
                if synonyms:
                    all_expansions.extend(synonyms if isinstance(synonyms, list) else [synonyms])
                if abbreviations:
                    all_expansions.extend(
                        abbreviations if isinstance(abbreviations, list) else [abbreviations]
                    )

                if all_expansions:
                    expansion_rows.append({
                        "원본 용어": term_name,
                        "확장 용어": ", ".join(all_expansions),
                        "확장 수": len(all_expansions),
                        "카테고리": t.get("category", "-"),
                    })

            if expansion_rows:
                st.markdown(f"**동의어/약어 매핑: {len(expansion_rows)}개 용어**")
                df_exp = pd.DataFrame(expansion_rows)
                df_exp = df_exp.sort_values("확장 수", ascending=False).reset_index(drop=True)
                st.dataframe(df_exp, use_container_width=True, hide_index=True)

                # 확장 수 분포 차트
                fig_exp = px.histogram(
                    df_exp,
                    x="확장 수",
                    title="용어당 확장 수 분포",
                    labels={"확장 수": "확장 수", "count": "용어 수"},
                    nbins=10,
                )
                fig_exp.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_exp, use_container_width=True)
            else:
                st.info("동의어/약어 매핑이 등록된 용어가 없습니다.")

            st.markdown("---")

            # ── 쿼리 확장 테스트 ──
            st.markdown("#### 쿼리 확장 테스트")
            test_query = st.text_input("테스트 쿼리 입력", placeholder="예: K8s 배포 방법", key="expansion_test")
            if test_query:
                matched = []
                for t in approved_terms:
                    term_name = t.get("term", "")
                    synonyms = t.get("synonyms", [])
                    all_terms_for_match = [term_name] + (synonyms if isinstance(synonyms, list) else [])

                    for candidate in all_terms_for_match:
                        if candidate and candidate.lower() in test_query.lower():
                            matched.append({
                                "매칭 용어": candidate,
                                "표준 용어": term_name,
                                "정의": (t.get("definition", "-") or "-")[:60],
                            })

                if matched:
                    st.success(f"매칭된 용어: {len(matched)}개")
                    st.dataframe(pd.DataFrame(matched), use_container_width=True, hide_index=True)
                else:
                    st.info("매칭되는 용어가 없습니다.")
        else:
            st.info("승인된 용어가 없어 쿼리 확장 데이터를 표시할 수 없습니다.")
