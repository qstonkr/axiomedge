"""문서 라이프사이클 -- 상태 전이 관리

상태별 현황, 자동 아카이브 예정, 상태 전이 이력.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="문서 라이프사이클", page_icon="📋", layout="wide")


import plotly.graph_objects as go

from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("📋 문서 라이프사이클")
st.info("문서의 상태(초안 -> 게시 -> 아카이브 -> 삭제) 전이를 관리합니다.")

tab_status, tab_archive, tab_transitions = st.tabs(
    ["상태별 현황", "자동 아카이브 예정", "상태 전이 이력"]
)


# ---------------------------------------------------------------------------
# KB 선택 (공통)
# ---------------------------------------------------------------------------
kbs_result = api_client.list_kbs()
kb_options: dict[str, str] = {}
if not api_failed(kbs_result):
    kb_items = kbs_result.get("items", [])
    kb_options = {
        kb.get("name", kb.get("id", "")): kb.get("id", kb.get("kb_id", ""))
        for kb in kb_items
    }


# =============================================================================
# 탭 1: 상태별 현황
# =============================================================================
with tab_status:
    if not kb_options:
        st.info("등록된 KB가 없습니다.")
    else:
        selected_kb = st.selectbox("KB 선택", list(kb_options.keys()), key="lifecycle_kb")
        kb_id = kb_options[selected_kb]

        result = api_client.get_kb_lifecycle(kb_id)

        if api_failed(result):
            st.warning("데이터를 불러올 수 없습니다.")
            if st.button("재시도", key="retry_lifecycle"):
                st.cache_data.clear()
                st.rerun()
        else:
            distribution = result.get("distribution", result.get("status_counts", {}))
            total = result.get("total", sum(distribution.values()) if distribution else 0)

            STATUS_LABELS = {
                "draft": "초안",
                "published": "게시",
                "archived": "아카이브",
                "deleted": "삭제",
            }
            STATUS_COLORS = {
                "draft": "#3498DB",
                "published": "#2ECC71",
                "archived": "#F39C12",
                "deleted": "#E74C3C",
            }

            if distribution and any(v > 0 for v in distribution.values()):
                # Summary metrics
                m_cols = st.columns(len(distribution) + 1)
                with m_cols[0]:
                    st.metric("전체 문서", f"{total:,}건")
                for i, (status, count) in enumerate(distribution.items()):
                    with m_cols[i + 1]:
                        label = STATUS_LABELS.get(status.lower(), status)
                        st.metric(label, f"{count:,}건")

                st.caption("게시(published) 상태의 문서만 검색 결과에 노출됩니다.")

                # Pie chart
                labels = [STATUS_LABELS.get(k.lower(), k) for k in distribution.keys()]
                values = list(distribution.values())
                colors = [STATUS_COLORS.get(k.lower(), "#BDC3C7") for k in distribution.keys()]

                fig = go.Figure(
                    go.Pie(
                        labels=labels,
                        values=values,
                        marker=dict(colors=colors),
                        textinfo="label+percent+value",
                        hole=0.3,
                    )
                )
                fig.update_layout(
                    title="문서 상태 분포",
                    height=400,
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                # Published ratio
                published = distribution.get("published", distribution.get("Published", 0))
                if total > 0:
                    pub_rate = published / total
                    st.progress(min(pub_rate, 1.0), text=f"게시 비율: {pub_rate:.1%}")
            else:
                st.info("이 KB에 문서 라이프사이클 데이터가 없습니다.")

    with st.expander("도움말: 문서 상태", expanded=False):
        st.markdown(
            """
            | 상태 | 설명 |
            |------|------|
            | **초안 (draft)** | 작성 중인 문서. 검색 결과에 노출되지 않음 |
            | **게시 (published)** | 활성 문서. 검색 결과에 노출됨 |
            | **아카이브 (archived)** | 더 이상 활성이 아닌 문서. 검색 제외 |
            | **삭제 (deleted)** | 논리적 삭제 상태. 복구 가능 |
            """
        )


# =============================================================================
# 탭 2: 자동 아카이브 예정
# =============================================================================
with tab_archive:
    st.warning("아래 문서들은 신선도가 낮아 자동 아카이브 예정입니다.")

    if not kb_options:
        st.info("등록된 KB가 없습니다.")
    else:
        selected_kb2 = st.selectbox("KB 선택", list(kb_options.keys()), key="archive_kb")
        kb_id2 = kb_options[selected_kb2]

        # Try lifecycle endpoint with upcoming_archive filter
        result = api_client.get_kb_lifecycle(kb_id2, filter="upcoming_archive")

        if api_failed(result):
            st.warning("데이터를 불러올 수 없습니다.")
            if st.button("재시도", key="retry_archive"):
                st.cache_data.clear()
                st.rerun()
        else:
            items = result.get("items", result.get("documents", result.get("upcoming_archive", [])))

            if items:
                st.caption(f"총 {len(items)}건의 자동 아카이브 예정 문서")

                import pandas as pd

                rows = []
                for doc in items:
                    rows.append({
                        "문서명": doc.get("name", doc.get("title", doc.get("document_name", "-"))),
                        "현재 상태": STATUS_LABELS.get(
                            doc.get("status", "published").lower(),
                            doc.get("status", "-"),
                        ),
                        "마지막 업데이트": (doc.get("updated_at", doc.get("last_updated", "")) or "")[:16],
                        "아카이브 예정일": (doc.get("archive_date", doc.get("scheduled_archive_at", "")) or "")[:10],
                        "경과일": doc.get("days_since_update", "-"),
                    })

                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("자동 아카이브 예정 문서가 없습니다.")


# =============================================================================
# 탭 3: 상태 전이 이력
# =============================================================================
with tab_transitions:
    st.subheader("최근 상태 전이 이력")

    if not kb_options:
        st.info("등록된 KB가 없습니다.")
    else:
        selected_kb3 = st.selectbox("KB 선택", list(kb_options.keys()), key="transitions_kb")
        kb_id3 = kb_options[selected_kb3]

        result = api_client.get_kb_lifecycle(kb_id3, filter="transitions")

        if api_failed(result):
            st.warning("데이터를 불러올 수 없습니다.")
            if st.button("재시도", key="retry_transitions"):
                st.cache_data.clear()
                st.rerun()
        else:
            transitions = result.get("transitions", result.get("items", result.get("history", [])))

            if transitions:
                st.caption(f"최근 {len(transitions)}건의 상태 전이")

                ARROW_COLORS = {
                    "published": ":green",
                    "archived": ":orange",
                    "deleted": ":red",
                    "draft": ":blue",
                }

                for t in transitions:
                    doc_name = t.get("document_name", t.get("name", t.get("title", "-")))
                    from_status = t.get("from_status", t.get("previous_status", "-"))
                    to_status = t.get("to_status", t.get("new_status", "-"))
                    actor = t.get("actor", t.get("changed_by", t.get("user", "-")))
                    timestamp = t.get("timestamp", t.get("changed_at", t.get("created_at", "")))

                    from_label = STATUS_LABELS.get(from_status.lower(), from_status) if from_status != "-" else "-"
                    to_label = STATUS_LABELS.get(to_status.lower(), to_status) if to_status != "-" else "-"
                    color = ARROW_COLORS.get(to_status.lower(), "")

                    with st.container(border=True):
                        tcol1, tcol2, tcol3 = st.columns([3, 2, 1])
                        with tcol1:
                            st.markdown(f"**{doc_name}**")
                        with tcol2:
                            if color:
                                st.markdown(f"{from_label} {color}[-> {to_label}]")
                            else:
                                st.markdown(f"{from_label} -> {to_label}")
                        with tcol3:
                            st.caption(actor)
                            if timestamp:
                                st.caption(str(timestamp)[:16])
            else:
                st.info("상태 전이 이력이 없습니다.")
