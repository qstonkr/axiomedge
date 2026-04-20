"""골든 셋 관리 -- 골든 셋 Q&A 조회/관리 + 평가 결과 확인

Created: 2026-04-02
"""

import streamlit as st

st.set_page_config(page_title="골든 셋 관리", page_icon="🎯", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/golden-set", "Golden Set")

import pandas as pd

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("골든 셋 관리")
st.caption("RAG 평가용 골든 셋 Q&A 조회/관리 및 평가 결과를 확인합니다.")

tab_golden, tab_eval = st.tabs(["골든 셋", "평가 결과"])

# ============================================================================
# 1) 골든 셋
# ============================================================================
with tab_golden:
    # Filters
    col_kb, col_status = st.columns(2)
    with col_kb:
        kb_filter = st.selectbox(
            "KB 필터",
            ["전체", "a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"],
            key="gs_kb",
        )
    with col_status:
        status_filter = st.selectbox(
            "상태 필터", ["전체", "approved", "pending", "rejected"], key="gs_status"
        )

    kb_val = None if kb_filter == "전체" else kb_filter
    status_val = None if status_filter == "전체" else status_filter

    data = api_client.list_golden_set(kb_id=kb_val, status=status_val, page_size=200)
    if api_failed(data):
        st.error("골든 셋 API 연결 실패")
    else:
        items = data.get("items", [])
        total = data.get("total", len(items))

        # Summary metrics
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("전체", f"{total}건")
        with m2:
            approved = sum(1 for i in items if i.get("status") == "approved")
            st.metric("승인", f"{approved}건")
        with m3:
            pending = sum(1 for i in items if i.get("status") == "pending")
            st.metric("대기", f"{pending}건")
        with m4:
            kb_count = len({i.get("kb_id", "") for i in items})
            st.metric("KB 수", f"{kb_count}개")

        # KB별 분포
        if items:
            st.markdown("---")
            st.subheader("KB별 분포")
            kb_dist: dict[str, int] = {}
            for item in items:
                kb = item.get("kb_id", "unknown")
                kb_dist[kb] = kb_dist.get(kb, 0) + 1

            dist_cols = st.columns(len(kb_dist))
            for i, (kb, cnt) in enumerate(sorted(kb_dist.items())):
                with dist_cols[i]:
                    st.metric(kb, f"{cnt}건")

        # Table
        if items:
            st.markdown("---")
            st.subheader("질문/답변 목록")

            rows = []
            for item in items:
                rows.append({
                    "ID": item.get("id", "")[:8],
                    "KB": item.get("kb_id", ""),
                    "상태": item.get("status", ""),
                    "질문": item.get("question", "")[:80],
                    "기대 답변": item.get("expected_answer", "")[:80],
                    "출처": item.get("source_document", "")[:40] if item.get("source_document") else "-",
                })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Detail expander
            st.markdown("---")
            st.subheader("상세 보기")
            selected_idx = st.selectbox(
                "질문 선택",
                range(len(items)),
                format_func=lambda i: f"[{items[i].get('kb_id', '')}] {items[i].get('question', '')[:60]}",
                key="gs_detail",
            )
            selected = items[selected_idx]
            with st.expander("상세 내용", expanded=True):
                st.markdown(f"**KB:** `{selected.get('kb_id', '')}`")
                st.markdown(f"**상태:** `{selected.get('status', '')}`")
                st.markdown(f"**질문:** {selected.get('question', '')}")
                st.markdown(f"**기대 답변:** {selected.get('expected_answer', '')}")
                if selected.get("source_document"):
                    st.markdown(f"**출처:** {selected.get('source_document', '')}")

                # Status update
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("승인", key=f"approve_{selected['id']}"):
                        api_client.update_golden_set_item(selected["id"], {"status": "approved"})
                        st.cache_data.clear()
                        st.rerun()
                with col_b:
                    if st.button("거부", key=f"reject_{selected['id']}"):
                        api_client.update_golden_set_item(selected["id"], {"status": "rejected"})
                        st.cache_data.clear()
                        st.rerun()
                with col_c:
                    if st.button("삭제", key=f"delete_{selected['id']}"):
                        api_client.delete_golden_set_item(selected["id"])
                        st.cache_data.clear()
                        st.rerun()
        else:
            st.info("골든 셋 데이터가 없습니다.")

# ============================================================================
# 2) 평가 결과
# ============================================================================
with tab_eval:
    summary_data = api_client.get_eval_results_summary()
    if api_failed(summary_data):
        st.warning("평가 결과 API 데이터를 불러올 수 없습니다.")
    else:
        runs = summary_data.get("runs", [])
        if runs:
            st.subheader("평가 실행 이력")

            # Latest run metrics
            latest = runs[0]
            st.markdown("**LLM Judge**")
            m1, m2, m3, m4, m5 = st.columns(5)
            with m1:
                st.metric("Faithfulness", f"{latest.get('avg_faithfulness', 0):.3f}")
            with m2:
                st.metric("Relevancy", f"{latest.get('avg_relevancy', 0):.3f}")
            with m3:
                st.metric("Completeness", f"{latest.get('avg_completeness', 0):.3f}")
            with m4:
                overall = (
                    latest.get("avg_faithfulness", 0)
                    + latest.get("avg_relevancy", 0)
                    + latest.get("avg_completeness", 0)
                ) / 3
                st.metric("Overall", f"{overall:.3f}")
            with m5:
                st.metric("평가 건수", f"{latest.get('count', 0)}건")

            # CRAG + Recall metrics
            cnt = latest.get("count", 1) or 1
            crag_ok = latest.get("crag_correct", 0)
            recall = latest.get("recall_hits", 0)
            st.markdown("**CRAG / Recall**")
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("CRAG Correct", f"{crag_ok}/{cnt} ({crag_ok/cnt:.0%})")
            with c2:
                st.metric("CRAG Ambiguous", f"{latest.get('crag_ambiguous', 0)}")
            with c3:
                st.metric("CRAG Incorrect", f"{latest.get('crag_incorrect', 0)}")
            with c4:
                st.metric("Avg Confidence", f"{latest.get('avg_crag_confidence', 0):.3f}")
            with c5:
                st.metric("Source Recall", f"{recall}/{cnt} ({recall/cnt:.0%})")

            # Runs table
            st.markdown("---")
            st.subheader("실행 목록")
            run_rows = []
            for r in runs:
                cnt = r.get("count", 1) or 1
                crag_ok = r.get("crag_correct", 0)
                recall_ok = r.get("recall_hits", 0)
                run_rows.append({
                    "Eval ID": r.get("eval_id", ""),
                    "KB": r.get("kb_id", ""),
                    "건수": r.get("count", 0),
                    "F": f"{r.get('avg_faithfulness', 0):.3f}",
                    "R": f"{r.get('avg_relevancy', 0):.3f}",
                    "C": f"{r.get('avg_completeness', 0):.3f}",
                    "CRAG OK": f"{crag_ok}/{cnt}",
                    "Recall": f"{recall_ok}/{cnt}",
                    "검색시간": f"{r.get('avg_search_time_ms', 0):.0f}ms",
                    "시작": (r.get("started_at") or "")[:16],
                })
            df_runs = pd.DataFrame(run_rows)
            st.dataframe(df_runs, use_container_width=True, hide_index=True)

            # Detail per eval run
            st.markdown("---")
            st.subheader("평가 상세 결과")
            eval_ids = [r.get("eval_id", "") for r in runs]
            selected_eval = st.selectbox("Eval ID 선택", eval_ids, key="eval_detail")

            if selected_eval:
                detail_data = api_client.list_eval_results(
                    eval_id=selected_eval, page_size=200
                )
                if not api_failed(detail_data):
                    detail_items = detail_data.get("items", [])
                    if detail_items:
                        detail_rows = []
                        for d in detail_items:
                            f_val = d.get("faithfulness", 0)
                            r_val = d.get("relevancy", 0)
                            c_val = d.get("completeness", 0)
                            detail_rows.append({
                                "KB": d.get("kb_id", ""),
                                "질문": d.get("question", "")[:60],
                                "F": f"{f_val:.2f}",
                                "R": f"{r_val:.2f}",
                                "C": f"{c_val:.2f}",
                                "CRAG": d.get("crag_action", "-"),
                                "Recall": "O" if d.get("recall_hit") else "X",
                                "검색시간": f"{d.get('search_time_ms', 0):.0f}ms",
                                "실제 답변": d.get("actual_answer", "")[:60],
                            })
                        df_detail = pd.DataFrame(detail_rows)
                        st.dataframe(
                            df_detail, use_container_width=True, hide_index=True
                        )
                    else:
                        st.info("해당 평가의 상세 결과가 없습니다.")
        else:
            st.info("평가 결과가 없습니다. 평가를 먼저 실행하세요.")
