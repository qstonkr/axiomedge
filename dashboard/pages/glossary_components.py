"""Glossary page helper components.

Extracted from glossary.py to keep the main page file focused on layout/flow.

Created: 2026-04-04
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from services import api_client
from services.api_client import api_failed

_MSG_API_FAIL = "API 연결 실패. 재시도 해주세요."
_BTN_RETRY = "🔄 재시도"


# =============================================================================
# Helper: 사전 유형별 목록 + CSV 임포트 + 삭제 서브탭 렌더링
# =============================================================================
def _render_dict_csv_import(
    term_type_value: str, term_type_label: str, key_prefix: str,
) -> None:
    """Render CSV import section for a dict subtab."""
    with st.expander(f"📥 {term_type_label} CSV 가져오기"):
        st.caption(f"{term_type_label} CSV 파일을 가져옵니다. 여러 파일을 동시에 선택할 수 있습니다.")
        st.caption("필수 컬럼: 물리명(term), 논리명(term_ko)")
        csv_files = st.file_uploader(
            "CSV 파일 선택 (여러 개 가능)", type=["csv"],
            accept_multiple_files=True, key=f"{key_prefix}_csv_upload",
        )
        csv_enc = st.selectbox(
            "인코딩", options=["utf-8", "euc-kr", "cp949"], key=f"{key_prefix}_csv_enc",
        )
        if not csv_files:
            return

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
                return

            if st.button(
                f"📥 {term_type_label} 가져오기 ({len(csv_files)}개 파일)",
                key=f"{key_prefix}_import_btn", type="primary",
            ):
                _execute_csv_import(csv_files, csv_enc, term_type_value, term_type_label)
        except Exception as e:
            st.error(f"파일 읽기 실패: {e}")


def _execute_csv_import(csv_files, csv_enc: str, term_type_value: str, _term_type_label: str) -> None:
    """Execute the CSV import for multiple files."""
    total_imported = 0
    total_skipped = 0
    all_errors = []

    for csv_file in csv_files:
        file_bytes = csv_file.read()
        result = api_client.import_glossary_csv(
            file_bytes=file_bytes, filename=csv_file.name,
            encoding=csv_enc, term_type=term_type_value,
        )
        if api_failed(result):
            all_errors.append(f"{csv_file.name}: {result.get('error', 'Unknown')}")
        else:
            total_imported += result.get("imported", result.get("imported_count", 0))
            total_skipped += result.get("skipped", result.get("skipped_count", 0))

    if all_errors:
        st.error(f"일부 실패: {'; '.join(all_errors)}")
    st.success(
        f"임포트 완료: {len(csv_files)}개 파일, 등록 {total_imported}건, 건너뜀 {total_skipped}건"
    )
    st.cache_data.clear()
    st.rerun()


def _render_dict_delete_all(
    term_type_value: str, term_type_label: str, key_prefix: str,
) -> None:
    """Render delete-all section for a dict subtab."""
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


def render_dict_subtab(term_type_value: str, term_type_label: str, key_prefix: str):
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
        sq = search_q.lower()
        terms = [
            t for t in terms
            if sq in t.get("term", "").lower()
            or sq in (t.get("term_ko") or "").lower()
            or sq in t.get("definition", "").lower()
        ]

    if terms:
        rows = [{
            "물리명": t.get("term", "-"),
            "논리명": t.get("term_ko") or "-",
            "정의": (t.get("definition", "-") or "-")[:80],
            "카테고리": t.get("category", "-"),
        } for t in terms]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"등록된 {term_type_label}이 없습니다.")

    st.markdown("---")
    _render_dict_csv_import(term_type_value, term_type_label, key_prefix)
    _render_dict_delete_all(term_type_value, term_type_label, key_prefix)


# =============================================================================
# 유사도 체크 & 자동 정리
# =============================================================================
def render_similarity_check():
    """Render similarity check & cleanup UI (requires dist_result in session_state)."""
    with st.expander("🔍 유사도 체크 & 자동 정리", expanded=False):
        st.caption("PENDING 용어를 페이지 단위(50건)로 표준 용어사전과 3-Layer 매칭합니다.")

        # 분포 분석 결과 기반 추천 임계값 (Dense 임베딩 기준)
        dr = st.session_state["dist_result"]
        dense_stats = dr.get("score_stats", {}).get("dense_cosine", {})
        rf_stats = dr.get("score_stats", {}).get("rapidfuzz", {})
        def_stats = dr.get("score_stats", {}).get("definition", {})

        dense_p90 = dense_stats.get("p90", 0)
        dense_p50 = dense_stats.get("p50", 0)

        if dense_p90 > 0:
            rec_auto = round(dense_p90, 2)
            rec_review = round(dense_p50, 2)
            st.info(
                f"**추천 임계값** (Dense 임베딩 분포 기준 — 의미 기반 유사도)\n\n"
                f"| Zone | Dense Cosine | RapidFuzz | Definition |\n"
                f"|------|:---:|:---:|:---:|\n"
                f"| AUTO_MATCH | **≥ {rec_auto}** "
                f"| ≥ {round(rf_stats.get('p90', 0), 2)} "
                f"| ≥ {round(def_stats.get('p90', 0), 2)} |\n"
                f"| REVIEW | **{rec_review} ~ {rec_auto}** "
                f"| {round(rf_stats.get('p50', 0), 2)} ~ "
                f"{round(rf_stats.get('p90', 0), 2)} "
                f"| {round(def_stats.get('p50', 0), 2)} ~ "
                f"{round(def_stats.get('p90', 0), 2)} |\n"
                f"| NEW_TERM | **< {rec_review}** "
                f"| < {round(rf_stats.get('p50', 0), 2)} "
                f"| < {round(def_stats.get('p50', 0), 2)} |"
            )
            default_threshold = rec_auto
        else:
            rf_p90 = rf_stats.get("p90", 0)
            default_threshold = round(rf_p90, 2) if rf_p90 > 0 else 0.7

        threshold = st.slider(
            "유사도 기준점 (Dense Cosine 기준)", 0.3, 1.0, default_threshold, 0.05,
            help="이 값 이상이면 표준 용어와 동일/유사한 것으로 판단",
            key="sim_threshold",
        )

        # 페이지 컨트롤
        sim_col1, _, _ = st.columns([2, 2, 2])
        with sim_col1:
            if st.button("🔍 유사도 체크", type="secondary", key="sim_check_btn"):
                st.session_state["sim_page"] = 1
                sim_page = 1
                with st.spinner(f"유사도 분석 중... (페이지 {sim_page}, 50건)"):
                    result = api_client.check_pending_similarity(
                        threshold, page=sim_page, page_size=50
                    )
                    if not api_failed(result):
                        st.session_state["sim_check_result"] = result
                    else:
                        st.error(f"유사도 체크 실패: {result.get('error', '')}")

        if "sim_check_result" in st.session_state:
            _render_similarity_results(threshold)


def _render_sim_metrics(r: dict) -> None:
    """Render similarity check summary metrics."""
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
        st.metric("페이지", f"{r.get('page', 1)}")


def _build_matched_display(m: dict) -> str:
    """Build display string for a matched standard term."""
    matched_std = m.get("matched_standard", m.get("standard_term", "-"))
    matched_std_ko = m.get("matched_standard_ko") or ""
    morph_matches = m.get("morpheme_matches")

    if morph_matches and len(morph_matches) > 1:
        parts = []
        for mm in morph_matches:
            ko = mm.get("standard_term_ko") or ""
            parts.append(f"{mm['standard_term']}({ko})" if ko else mm["standard_term"])
        return " + ".join(parts)
    if matched_std_ko:
        return f"{matched_std} ({matched_std_ko})"
    return matched_std


def _render_sim_results_table(matches: list[dict]) -> None:
    """Render the similarity results table with zone coloring."""
    if not matches:
        return
    display_rows = []
    for m in matches:
        row = {
            "용어": m.get("term", m.get("pending_term", "-")),
            "매칭 표준어": _build_matched_display(m),
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
        colors = {"auto_match": "#d4edda", "review": "#fff3cd", "new_term": "#f8d7da"}
        bg = colors.get(val, "")
        return f"background-color: {bg}" if bg else ""

    styled = df_matches.style.map(_zone_color, subset=["Zone"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _navigate_sim_page(threshold: float, new_page: int) -> None:
    """Navigate to a similarity check page and update session state."""
    st.session_state["sim_page"] = new_page
    with st.spinner(f"페이지 {new_page} 분석 중..."):
        result = api_client.check_pending_similarity(threshold, page=new_page, page_size=50)
        if not api_failed(result):
            st.session_state["sim_check_result"] = result
            st.rerun()



def _handle_auto_match_delete(auto_match_ids: list[str], threshold: float) -> None:
    """Execute AUTO_MATCH deletion."""
    with st.spinner("삭제 중..."):
        cleanup = api_client.cleanup_pending_by_similarity(
            threshold, term_ids=auto_match_ids
        )
        if not api_failed(cleanup):
            st.success(
                f"{cleanup.get('deleted_count', 0)}개 삭제 완료 "
                f"(남은 PENDING: {cleanup.get('remaining_pending', 0)}개)"
            )
            st.session_state.pop("sim_check_result", None)
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(f"삭제 실패: {cleanup.get('error', '')}")


def _handle_synonym_bulk_register(synonym_candidates: list[dict]) -> None:
    """Execute bulk synonym registration for AUTO_MATCH candidates."""
    success_count = 0
    for m in synonym_candidates:
        pending_term = m.get("term", m.get("pending_term", ""))
        res = api_client.add_synonym_to_standard(
            standard_term_id=m["matched_standard_id"],
            synonym=pending_term, delete_pending_id=m["term_id"],
        )
        if not api_failed(res):
            success_count += 1
    st.success(f"{success_count}/{len(synonym_candidates)}건 동의어 등록 완료")
    st.session_state.pop("sim_check_result", None)
    st.cache_data.clear()
    st.rerun()


def _render_sim_review_section(matches: list[dict]) -> None:
    """Render individual synonym registration for REVIEW zone matches."""
    review_matches = [
        m for m in matches
        if m.get("decision_zone") == "review"
        and m.get("matched_standard_id") and m.get("term_id")
    ]
    if not review_matches:
        return
    with st.expander(
        f"🔗 REVIEW {len(review_matches)}건 개별 동의어 등록", expanded=False
    ):
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
                        synonym=pending_term, delete_pending_id=m["term_id"],
                    )
                    if not api_failed(res):
                        st.success(f"'{pending_term}' → '{matched_std}' 동의어 등록 완료")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"등록 실패: {res.get('error', '')}")


def _render_similarity_results(threshold: float):
    """Render similarity check results table and action buttons."""
    r = st.session_state["sim_check_result"]
    cur_page = r.get("page", 1)
    has_next = r.get("has_next", False)
    matches = r.get("matches", [])

    _render_sim_metrics(r)
    _render_sim_results_table(matches)

    auto_match_ids = [
        m.get("term_id") for m in matches
        if m.get("decision_zone") == "auto_match" and m.get("term_id")
    ]
    synonym_candidates = [
        m for m in matches
        if m.get("decision_zone") == "auto_match"
        and m.get("matched_standard_id") and m.get("term_id")
    ]

    nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([2, 2, 2, 2])
    with nav_col1:
        if cur_page > 1 and st.button("⬅️ 이전 페이지", key="sim_prev_page"):
            _navigate_sim_page(threshold, cur_page - 1)
    with nav_col2:
        if has_next and st.button("➡️ 다음 페이지", key="sim_next_page"):
            _navigate_sim_page(threshold, cur_page + 1)
    with nav_col3:
        if auto_match_ids and st.button(
            f"🗑️ AUTO_MATCH {len(auto_match_ids)}건 삭제",
            type="primary", key="sim_cleanup_btn",
        ):
            _handle_auto_match_delete(auto_match_ids, threshold)
    with nav_col4:
        if synonym_candidates and st.button(
            f"🔗 AUTO_MATCH {len(synonym_candidates)}건 동의어 등록",
            key="sim_synonym_bulk_btn",
        ):
            _handle_synonym_bulk_register(synonym_candidates)

    _render_sim_review_section(matches)


# =============================================================================
# 유사도 점수 분포 분석
# =============================================================================
def _render_distribution_prompt():
    """Render the initial distribution analysis prompt (no results yet)."""
    with st.expander("📊 유사도 점수 분포 분석", expanded=True):
        st.caption(
            "용어 샘플(최대 500개)에 대해 RapidFuzz/Jaccard 유사도 점수 분포를 분석합니다. "
            "PENDING이 없으면 APPROVED 샘플로 분석합니다. 임계값 캘리브레이션에 활용합니다."
        )
        if st.button("📊 분포 분석 실행", type="primary", key="dist_btn"):
            with st.spinner("채널별 점수 분포 수집 중 (최대 30초)..."):
                dist_result = api_client.get_similarity_distribution()
                if not api_failed(dist_result):
                    st.session_state["dist_result"] = dist_result
                    st.rerun()
                else:
                    st.error(f"분포 분석 실패: {dist_result.get('error', '')}")


def render_distribution_analysis():
    """Render distribution analysis section (Phase 0)."""
    if "dist_result" not in st.session_state:
        _render_distribution_prompt()
        return

    dr = st.session_state["dist_result"]
    rf_stats = dr.get("score_stats", {}).get("rapidfuzz", {})
    jac_stats = dr.get("score_stats", {}).get("jaccard", {})
    summary_cols = st.columns([6, 1])
    with summary_cols[0]:
        st.success(
            f"**분포 분석 완료** — "
            f"RapidFuzz p90={rf_stats.get('p90', 0):.2f}, "
            f"p50={rf_stats.get('p50', 0):.2f} | "
            f"Jaccard p90={jac_stats.get('p90', 0):.2f}, "
            f"p50={jac_stats.get('p50', 0):.2f}"
        )
    with summary_cols[1]:
        if st.button("🔄 재분석", key="dist_reanalyze_btn"):
            del st.session_state["dist_result"]
            if "sim_check_result" in st.session_state:
                del st.session_state["sim_check_result"]
            st.rerun()

    with st.expander("📊 분포 상세 보기", expanded=False):
        st.caption(
            f"표준 용어: {dr.get('standard_count', 0):,}개 | "
            f"PENDING: {dr.get('pending_count', 0):,}개 | "
            f"샘플: {dr.get('sample_size', 0):,}개"
        )
        channel_map = {
            "RapidFuzz (용어명 유사도)": ("rapidfuzz_scores", "rapidfuzz"),
            "N-gram Jaccard (구조 유사도)": ("jaccard_scores", "jaccard"),
            "Definition (정의 유사도)": ("definition_scores", "definition"),
            "Dense Cosine (임베딩 유사도)": ("dense_cosine_scores", "dense_cosine"),
        }
        all_stats = dr.get("score_stats", {})
        for ch_label, (scores_key, stats_key) in channel_map.items():
            scores = dr.get(scores_key, [])
            stats = all_stats.get(stats_key, {})
            if not scores:
                st.markdown(f"**{ch_label}** — 데이터 없음")
                continue
            st.markdown(
                f"**{ch_label}** — count={int(stats.get('count', 0))}, "
                f"mean={stats.get('mean', 0):.3f}, "
                f"p50={stats.get('p50', 0):.3f}, "
                f"p90={stats.get('p90', 0):.3f}, "
                f"p95={stats.get('p95', 0):.3f}"
            )
            fig = px.histogram(
                x=scores, nbins=20,
                title=f"{ch_label} Score Distribution",
                labels={"x": "Score", "count": "Count"},
            )
            fig.update_layout(margin={"l": 20, "r": 20, "t": 40, "b": 20}, height=250)
            st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# 자동 발견 동의어 후보
# =============================================================================
def _render_disc_bulk_actions(all_disc_ids: list[str]) -> None:
    """Render bulk approve/reject buttons for discovered synonyms."""
    bulk_col1, bulk_col2 = st.columns(2)
    with bulk_col1:
        if st.button(
            f"일괄 승인 ({len(all_disc_ids)}건)", key="disc_bulk_approve", type="primary",
        ):
            bulk_res = api_client.approve_discovered_synonyms(all_disc_ids)
            if not api_failed(bulk_res):
                st.success(f"{bulk_res.get('approved', 0)}건 승인 완료")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"일괄 승인 실패: {bulk_res.get('error', '')}")
    with bulk_col2:
        if st.button(f"일괄 거부 ({len(all_disc_ids)}건)", key="disc_bulk_reject"):
            bulk_res = api_client.reject_discovered_synonyms(all_disc_ids)
            if not api_failed(bulk_res):
                st.success(f"{bulk_res.get('rejected', 0)}건 거부 완료")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"일괄 거부 실패: {bulk_res.get('error', '')}")


def _render_disc_item(d_idx: int, disc: dict) -> None:
    """Render a single discovered synonym candidate card."""
    disc_id = disc.get("id", "-")
    disc_term = disc.get("term", "-")
    disc_cat = disc.get("category", "-")
    disc_synonyms_list = disc.get("synonyms", [])
    base_terms = ", ".join(disc_synonyms_list) if disc_synonyms_list else "-"

    with st.container(border=True):
        dc1, dc2, dc3 = st.columns([3, 3, 2])
        with dc1:
            st.markdown(f"**{disc_term}**")
            st.caption(f"패턴: {disc_cat}")
        with dc2:
            st.markdown(f"기준 용어: {base_terms}")
            st.caption(f"ID: {disc_id}")
        with dc3:
            dac1, dac2 = st.columns(2)
            with dac1:
                if st.button("승인", key=f"disc_approve_{d_idx}", type="primary"):
                    res = api_client.approve_discovered_synonyms([disc_id])
                    if not api_failed(res):
                        st.success("승인 완료")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("승인 실패")
            with dac2:
                if st.button("거부", key=f"disc_reject_{d_idx}"):
                    res = api_client.reject_discovered_synonyms([disc_id])
                    if not api_failed(res):
                        st.success("거부 완료")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("거부 실패")


def render_discovered_synonyms():
    """Render auto-discovered synonym candidates section."""
    with st.expander("🔗 자동 발견 동의어 후보", expanded=False):
        st.caption(
            "인제스천 중 문서 텍스트에서 패턴 매칭으로 자동 발견된 동의어 후보입니다. "
            "승인하면 해당 용어의 동의어 목록에 추가됩니다."
        )
        disc_page = st.number_input("페이지", min_value=1, value=1, key="disc_syn_page")
        disc_result = api_client.list_discovered_synonyms(
            status="pending", page=disc_page, page_size=50
        )

        if api_failed(disc_result):
            st.warning("자동 발견 동의어 조회 실패")
            return

        disc_synonyms = disc_result.get("discovered_synonyms", [])
        if not disc_synonyms:
            st.success("자동 발견된 동의어 후보가 없습니다.")
            return

        disc_total = disc_result.get("total", 0)
        st.info(f"검토 대기 중인 자동 발견 동의어: {disc_total}개")

        all_disc_ids = [d.get("id") for d in disc_synonyms if d.get("id")]
        _render_disc_bulk_actions(all_disc_ids)

        for d_idx, disc in enumerate(disc_synonyms):
            _render_disc_item(d_idx, disc)


# =============================================================================
# 승인 대기 목록
# =============================================================================
def _render_pending_term_card(term: dict) -> None:
    """Render a single pending term card with approve/reject actions."""
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

        btn_col1, btn_col2, _ = st.columns([1, 1, 3])
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
                    term_id, rejected_by="admin", reason=reject_reason,
                )
                if api_failed(result):
                    st.error("거부 실패")
                else:
                    st.success(f"'{term_name}' 거부 완료")
                    st.session_state[f"show_reject_{term_id}"] = False
                    st.cache_data.clear()
                    st.rerun()


def render_pending_list():
    """Render the pending terms list with approve/reject actions."""
    PENDING_PAGE_SIZE = 20
    pending_page = st.number_input("페이지", min_value=1, value=1, key="pending_page")

    pending_result = api_client.list_glossary_terms(
        status="pending", page=pending_page, page_size=PENDING_PAGE_SIZE
    )

    if api_failed(pending_result):
        st.error(_MSG_API_FAIL)
        if st.button(_BTN_RETRY, key="retry_pending"):
            st.cache_data.clear()
            st.rerun()
        return

    pending_terms = pending_result.get("items", pending_result.get("terms", []))
    pending_total = pending_result.get("total", len(pending_terms))
    pending_total_pages = max(1, (pending_total + PENDING_PAGE_SIZE - 1) // PENDING_PAGE_SIZE)

    if not pending_terms:
        st.success("승인 대기 중인 용어가 없습니다.")
        return

    st.info(
        f"승인 대기 중인 용어: {pending_total}개 "
        f"(페이지 {pending_page}/{pending_total_pages})"
    )
    for term in pending_terms:
        _render_pending_term_card(term)


def _render_domain_distribution(total_count: int):
    """Render domain distribution chart in glossary stats."""
    domain_result = api_client.get_glossary_domain_stats()
    if api_failed(domain_result):
        st.warning("도메인 통계를 불러올 수 없습니다.")
        return

    domains = domain_result.get("domains", {})
    if not domains:
        st.info("도메인 정보가 없습니다.")
        return

    sorted_domains = sorted(
        domains.items(), key=lambda x: x[1], reverse=True
    )[:20]
    domain_names = [d[0] for d in sorted_domains]
    domain_counts = [d[1] for d in sorted_domains]

    fig_domain = px.bar(
        x=domain_counts, y=domain_names,
        orientation="h",
        title=f"도메인별 용어 분포 (전체 {total_count:,}건, 상위 20개)",
        labels={"x": "용어 수", "y": "도메인"},
    )
    fig_domain.update_layout(
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        height=500,
        yaxis={"autorange": "reversed"},
    )
    st.plotly_chart(fig_domain, use_container_width=True)
    st.caption(f"총 {len(domains)}개 도메인, {total_count:,}건")


# =============================================================================
# 탭 3: 통계
# =============================================================================
def render_stats_tab():
    """Render glossary statistics tab content."""
    st.subheader("용어집 통계")

    word_approved = api_client.list_glossary_terms(
        status="approved", term_type="word", page_size=1
    )
    term_approved = api_client.list_glossary_terms(
        status="approved", term_type="term", page_size=1
    )
    all_pending = api_client.list_glossary_terms(status="pending", page_size=1)
    all_total = api_client.list_glossary_terms(page_size=1)

    if api_failed(all_total):
        st.error(_MSG_API_FAIL)
        if st.button(_BTN_RETRY, key="retry_glossary_stats"):
            st.cache_data.clear()
            st.rerun()
        return

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
    type_data = {
        "단어사전 (word)": word_count,
        "용어사전 (term)": term_count,
        "대기 중 (pending)": pending_count,
    }
    type_data = {k: v for k, v in type_data.items() if v > 0}

    if type_data:
        fig_type = px.pie(
            names=list(type_data.keys()),
            values=list(type_data.values()),
            title="사전 유형별 분포",
            hole=0.3,
            color_discrete_sequence=["#2196F3", "#4CAF50", "#FFC107"],
        )
        fig_type.update_layout(margin={"l": 20, "r": 20, "t": 40, "b": 20})
        st.plotly_chart(fig_type, use_container_width=True)

    # ── 도메인별 분포 ──
    st.markdown("### 도메인별 분포")
    _render_domain_distribution(total_count)

    # ── 표준분류(kb_id)별 분포 ──
    st.markdown("### 표준분류별 분포")
    source_result = api_client.get_glossary_source_stats()
    if not api_failed(source_result):
        sources = source_result.get("sources", {})
        if sources:
            fig_src = px.pie(
                names=list(sources.keys()),
                values=list(sources.values()),
                title=f"표준분류별 분포 (전체 {total_count:,}건)",
                hole=0.3,
            )
            fig_src.update_layout(margin={"l": 20, "r": 20, "t": 40, "b": 20})
            st.plotly_chart(fig_src, use_container_width=True)

    if total_count == 0:
        st.info("용어 데이터가 없습니다.")


# =============================================================================
# 탭 4: 쿼리 확장
# =============================================================================
def _build_expansion_rows(approved_terms: list[dict]) -> list[dict]:
    """Build expansion mapping rows from approved terms."""
    rows = []
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
            rows.append({
                "원본 용어": term_name,
                "확장 용어": ", ".join(all_expansions),
                "확장 수": len(all_expansions),
                "카테고리": t.get("category", "-"),
            })
    return rows


def _render_expansion_mapping(expansion_rows: list[dict]) -> None:
    """Render expansion mapping table and histogram."""
    if not expansion_rows:
        st.info("동의어/약어 매핑이 등록된 용어가 없습니다.")
        return

    st.markdown(f"**동의어/약어 매핑: {len(expansion_rows)}개 용어**")
    df_exp = pd.DataFrame(expansion_rows)
    df_exp = df_exp.sort_values("확장 수", ascending=False).reset_index(drop=True)
    st.dataframe(df_exp, use_container_width=True, hide_index=True)

    fig_exp = px.histogram(
        df_exp, x="확장 수", title="용어당 확장 수 분포",
        labels={"확장 수": "확장 수", "count": "용어 수"}, nbins=10,
    )
    fig_exp.update_layout(margin={"l": 20, "r": 20, "t": 40, "b": 20})
    st.plotly_chart(fig_exp, use_container_width=True)


def _render_expansion_test(approved_terms: list[dict]) -> None:
    """Render query expansion test input and results."""
    st.markdown("#### 쿼리 확장 테스트")
    test_query = st.text_input(
        "테스트 쿼리 입력", placeholder="예: K8s 배포 방법", key="expansion_test"
    )
    if not test_query:
        return

    matched = []
    query_lower = test_query.lower()
    for t in approved_terms:
        term_name = t.get("term", "")
        synonyms = t.get("synonyms", [])
        all_terms_for_match = [term_name] + (synonyms if isinstance(synonyms, list) else [])
        for candidate in all_terms_for_match:
            if candidate and candidate.lower() in query_lower:
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


def render_expansion_tab():
    """Render query expansion tab content."""
    st.subheader("쿼리 확장 (QueryExpander / GlossaryQueryExpander)")
    st.caption("용어와 동의어 매핑을 통한 검색 확장을 시각화합니다.")

    expansion_result = api_client.list_glossary_terms(status="approved", page_size=500)

    if api_failed(expansion_result):
        st.error(_MSG_API_FAIL)
        if st.button(_BTN_RETRY, key="retry_expansion"):
            st.cache_data.clear()
            st.rerun()
        return

    approved_terms = expansion_result.get("items", expansion_result.get("terms", []))
    if not approved_terms:
        st.info("승인된 용어가 없어 쿼리 확장 데이터를 표시할 수 없습니다.")
        return

    expansion_rows = _build_expansion_rows(approved_terms)
    _render_expansion_mapping(expansion_rows)

    st.markdown("---")
    _render_expansion_test(approved_terms)
