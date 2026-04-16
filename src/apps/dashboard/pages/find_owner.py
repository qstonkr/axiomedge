"""담당자 찾기

전문가/문서 담당자 검색 인터페이스.
OwnerQueryHandler 기반 컨텍스트 인젝션.

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="담당자 찾기", page_icon="👤", layout="wide")


import pandas as pd

from components.sidebar import render_sidebar
from components.metric_cards import get_confidence_badge
from services import api_client
from services.api_client import api_failed
from services.validators import sanitize_input

render_sidebar()

st.title("👤 담당자 찾기")
st.caption("시스템, 문서, 주제별 담당자를 검색합니다.")


# ---------------------------------------------------------------------------
# 검색 입력
# ---------------------------------------------------------------------------
# 홈에서 넘어온 검색어 확인
initial_query = st.session_state.pop("owner_query", "")

col_search, col_kb = st.columns([3, 1])

with col_search:
    query = st.text_input(
        "검색어",
        value=initial_query,
        placeholder="예: 주문결제 시스템, K8s 배포, 정산 담당...",
        key="owner_search_input",
    )

with col_kb:
    # 선택적 KB 필터
    kbs_result = api_client.list_kbs()
    kb_filter: str | None = None
    if not api_failed(kbs_result):
        kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
        kb_names = ["전체"] + [kb.get("name", kb.get("kb_id", "")) for kb in kb_items]
        kb_ids = [None] + [kb.get("kb_id", kb.get("id", "")) for kb in kb_items]
        selected_idx = st.selectbox("KB 필터", range(len(kb_names)), format_func=lambda i: kb_names[i], key="owner_kb_filter")
        kb_filter = kb_ids[selected_idx]


# ---------------------------------------------------------------------------
# 검색 실행
# ---------------------------------------------------------------------------
query = sanitize_input(query, max_length=200) if query else ""

if query:
    with st.spinner("담당자 검색 중..."):
        result = api_client.get_owner_search(query, kb_id=kb_filter)

    if api_failed(result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_owner"):
            st.cache_data.clear()
            st.rerun()
    else:
        owners = result.get("owners", result.get("results", result.get("items", result.get("experts", []))))
        search_confidence = result.get("confidence", 0)

        if owners:
            st.success(f"{len(owners)}명의 담당자를 찾았습니다.")

            for idx, owner in enumerate(owners):
                name = owner.get("name", owner.get("owner_name", "-"))
                team = owner.get("team", owner.get("department", "-"))
                confidence = owner.get("confidence", owner.get("confidence_score", search_confidence))
                system = owner.get("system", "")
                role = owner.get("role", "")
                source = owner.get("source", "")
                expertise_areas = owner.get("expertise_areas", owner.get("topics", []))
                contact = owner.get("contact", owner.get("email", ""))
                documents = owner.get("documents", owner.get("related_documents", []))

                conf_badge = get_confidence_badge(confidence)

                with st.container(border=True):
                    # 헤더
                    hcol1, hcol2, hcol3 = st.columns([2, 2, 1])
                    with hcol1:
                        st.markdown(f"### {name}")
                        st.caption(f"팀: {team}")
                    with hcol2:
                        # Show system/role if expertise_areas not available
                        if expertise_areas:
                            areas_str = ", ".join(expertise_areas[:5])
                            st.markdown(f"**전문 분야:** {areas_str}")
                        else:
                            info_parts = []
                            if system:
                                info_parts.append(f"**시스템:** {system}")
                            if role:
                                info_parts.append(f"**역할:** {role}")
                            if info_parts:
                                st.markdown(" | ".join(info_parts))
                    with hcol3:
                        st.markdown(f"**신뢰도:** {conf_badge}")
                        if confidence != 0:
                            st.caption(f"{confidence:.2f}")

                    # 출처 정보
                    if source:
                        st.caption(f"출처: {source}")

                    # 연락처
                    if contact:
                        st.markdown(f"**연락처:** {contact}")

                    # 담당 문서 목록
                    if documents:
                        with st.expander(f"📄 담당 문서 ({len(documents)}건)", expanded=False):
                            doc_rows = []
                            for doc in documents[:20]:
                                if isinstance(doc, dict):
                                    doc_rows.append({
                                        "문서 제목": doc.get("title", doc.get("document_title", "-")),
                                        "KB": doc.get("kb_name", doc.get("kb_id", "-")),
                                        "상태": doc.get("status", "-"),
                                    })
                                else:
                                    doc_rows.append({"문서 제목": str(doc), "KB": "-", "상태": "-"})
                            if doc_rows:
                                st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)
        else:
            st.info(f"'{query}'에 대한 담당자를 찾을 수 없습니다.")
            st.caption("다른 검색어를 시도하거나, 더 구체적인 주제로 검색해 보세요.")
else:
    # 검색 안내
    st.markdown("---")
    st.markdown("### 검색 안내")
    st.markdown("""
    담당자를 찾으려면 검색어를 입력하세요. 다음과 같은 검색이 가능합니다:

    - **시스템 이름**: "주문결제", "방송시스템", "정산"
    - **기술 주제**: "K8s 배포", "AWS 운영", "데이터마트"
    - **담당자 이름**: "김철수", "이영희"
    - **문서 제목**: "배포 가이드", "API 문서"
    """)

    st.markdown("### 예시 검색어")
    example_col1, example_col2, example_col3 = st.columns(3)
    with example_col1:
        if st.button("주문결제 시스템 담당자", key="ex1"):
            st.session_state.owner_query = "주문결제 시스템 담당자"
            st.rerun()
    with example_col2:
        if st.button("K8s 배포 전문가", key="ex2"):
            st.session_state.owner_query = "K8s 배포 전문가"
            st.rerun()
    with example_col3:
        if st.button("정산 담당자", key="ex3"):
            st.session_state.owner_query = "정산 담당자"
            st.rerun()


st.markdown("---")
st.caption("📌 OwnerQueryHandler 기반 | 그래프 관계 + 문서 소유권 통합 검색")
