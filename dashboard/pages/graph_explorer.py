"""지식 그래프 탐색기

엔티티 검색, 전문가 찾기, 무결성 검사.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="지식 그래프", page_icon="🔗", layout="wide")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar(show_admin=True)

st.title("🔗 지식 그래프")
st.caption("지식 그래프의 엔티티, 관계, 전문가를 탐색하고 무결성을 검사합니다.")

tab_search, tab_expert, tab_integrity, tab_cleanup = st.tabs(
    ["그래프 검색", "전문가 찾기", "무결성 검사", "\U0001f9f9 그래프 정리"]
)

# =============================================================================
# 탭 1: 그래프 검색
# =============================================================================
with tab_search:
    st.info("엔티티 이름으로 관련 문서와 전문가를 찾을 수 있습니다.")

    col_input, col_hops = st.columns([3, 1])
    with col_input:
        entity_query = st.text_input(
            "엔티티 검색어",
            placeholder="예: Qdrant, 인제스천, 크롤러",
            key="graph_entity_query",
        )
    with col_hops:
        max_hops = st.number_input(
            "최대 홉 수",
            min_value=1,
            max_value=3,
            value=2,
            key="graph_max_hops",
            help="탐색할 관계 깊이 (1~3)",
        )

    if st.button("검색", key="graph_search_btn", type="primary"):
        if not entity_query.strip():
            st.warning("검색어를 입력해주세요.")
        else:
            with st.spinner("그래프 검색 중..."):
                result = api_client.graph_search(entity_query.strip(), max_hops=max_hops)

            if api_failed(result):
                st.warning("데이터를 불러올 수 없습니다.")
            else:
                entities = result.get("entities", result.get("items", result.get("nodes", [])))
                if entities:
                    st.success(f"{len(entities)}개의 관련 엔티티를 찾았습니다.")

                    # ── Graph visualization ──
                    from streamlit_agraph import agraph, Node, Edge, Config

                    TYPE_COLORS = {
                        "Store": "#FF6B6B",
                        "Person": "#4ECDC4",
                        "Process": "#45B7D1",
                        "Product": "#96CEB4",
                        "Team": "#FFEAA7",
                        "System": "#DDA0DD",
                        "Location": "#98D8C8",
                        "Event": "#F7DC6F",
                        "Policy": "#BB8FCE",
                        "Term": "#85C1E9",
                        "Document": "#AEB6BF",
                        "Category": "#F0B27A",
                    }
                    TYPE_ICONS = {
                        "Store": "🏪", "Person": "👤", "Process": "⚙️",
                        "Product": "📦", "Team": "👥", "System": "🖥️",
                        "Location": "📍", "Event": "📅", "Policy": "📋",
                        "Term": "📖", "Document": "📄", "Category": "🏷️",
                    }

                    graph_nodes = []
                    graph_edges = []
                    seen_nodes = set()

                    for entity in entities:
                        name = entity.get("name", "")
                        e_type = entity.get("type", "CONCEPT")
                        if name and name not in seen_nodes:
                            seen_nodes.add(name)
                            graph_nodes.append(Node(
                                id=name,
                                label=name,
                                size=30,
                                color=TYPE_COLORS.get(e_type, "#AEB6BF"),
                                font={"size": 12},
                            ))

                        for rel in entity.get("relationships", []):
                            target = rel.get("target", "")
                            target_type = rel.get("target_type", "")
                            rel_type = rel.get("type", "")
                            if target and target not in seen_nodes:
                                seen_nodes.add(target)
                                graph_nodes.append(Node(
                                    id=target,
                                    label=target,
                                    size=20,
                                    color=TYPE_COLORS.get(target_type, "#D5D8DC"),
                                    font={"size": 10},
                                ))
                            if name and target:
                                graph_edges.append(Edge(
                                    source=name,
                                    target=target,
                                    label=rel_type,
                                    color="#888888",
                                    font={"size": 8, "color": "#666666"},
                                ))

                    if graph_nodes:
                        config = Config(
                            width="100%",
                            height=500,
                            directed=True,
                            physics=True,
                            hierarchical=False,
                            nodeHighlightBehavior=True,
                            highlightColor="#F7DC6F",
                            collapsible=False,
                        )
                        agraph(nodes=graph_nodes, edges=graph_edges, config=config)

                        # Legend
                        legend_cols = st.columns(6)
                        for i, (t, color) in enumerate(list(TYPE_COLORS.items())[:6]):
                            icon = TYPE_ICONS.get(t, "📌")
                            with legend_cols[i]:
                                st.markdown(f"<span style='color:{color}'>●</span> {icon} {t}", unsafe_allow_html=True)

                    # ── List view ──
                    st.markdown("---")
                    st.subheader("상세 목록")
                    for idx, entity in enumerate(entities):
                        name = entity.get("name", entity.get("label", "-"))
                        e_type = entity.get("type", "CONCEPT")
                        icon = TYPE_ICONS.get(e_type, "📌")
                        relationships = entity.get("relationships", [])

                        with st.expander(
                            f"{icon} {name} ({e_type}) — 관계 {len(relationships)}건",
                            expanded=(idx < 2),
                        ):
                            if relationships:
                                for rel in relationships:
                                    rel_type = rel.get("type", "-")
                                    target = rel.get("target", "-")
                                    target_type = rel.get("target_type", "")
                                    t_icon = TYPE_ICONS.get(target_type, "")
                                    st.markdown(f"- **{rel_type}** → {t_icon} {target}")
                            else:
                                st.caption("관계 정보가 없습니다.")
                else:
                    st.info("검색 결과가 없습니다. 다른 검색어를 시도해 보세요.")

    with st.expander("도움말: 그래프 검색", expanded=False):
        st.markdown(
            """
            - **엔티티**: 그래프에 저장된 노드 (사람, 문서, 개념, 시스템 등)
            - **홉 수**: 시작 엔티티에서 몇 단계까지 연결된 노드를 탐색할지 결정
              - 1홉: 직접 연결된 노드만
              - 2홉: 간접 연결까지 (기본)
              - 3홉: 더 넓은 범위 탐색 (결과가 많을 수 있음)
            """
        )


# =============================================================================
# 탭 2: 전문가 찾기
# =============================================================================
with tab_expert:
    st.info("주제 키워드로 관련 전문가(담당자)를 찾습니다.")

    topic_query = st.text_input(
        "주제 키워드",
        placeholder="예: 배포, 모니터링, 데이터 표준",
        key="graph_topic_query",
    )

    if st.button("전문가 검색", key="graph_expert_btn", type="primary"):
        if not topic_query.strip():
            st.warning("주제 키워드를 입력해주세요.")
        else:
            with st.spinner("전문가 검색 중..."):
                result = api_client.graph_experts_search(topic_query.strip())

            if api_failed(result):
                st.warning("데이터를 불러올 수 없습니다.")
            else:
                experts = result.get("experts", result.get("items", result.get("authors", [])))
                if experts:
                    st.success(f"{len(experts)}명의 관련 전문가를 찾았습니다.")

                    for expert in experts:
                        name = expert.get("name", expert.get("display_name", "-"))
                        role = expert.get("role", expert.get("department", "-"))
                        doc_count = expert.get("document_count", expert.get("authored_count", 0))
                        documents = expert.get("documents", expert.get("authored_documents", []))

                        with st.container(border=True):
                            ecol1, ecol2, ecol3 = st.columns([2, 2, 1])
                            with ecol1:
                                st.markdown(f"**👤 {name}**")
                                st.caption(role)
                            with ecol2:
                                if expert.get("email"):
                                    st.caption(f"📧 {expert['email']}")
                                topics = expert.get("topics", expert.get("expertise", []))
                                if topics:
                                    st.caption(f"🏷️ {', '.join(topics[:5])}")
                            with ecol3:
                                st.metric("담당 문서", f"{doc_count}건")

                            if documents:
                                with st.expander(f"담당 문서 목록 ({len(documents)}건)", expanded=False):
                                    for doc in documents[:10]:
                                        title = doc.get("title", doc.get("name", "-"))
                                        updated = doc.get("updated_at", "")
                                        if updated:
                                            updated = updated[:10]
                                        st.markdown(f"- {title}" + (f" ({updated})" if updated else ""))
                else:
                    st.info("해당 주제의 전문가를 찾지 못했습니다. 다른 키워드를 시도해 보세요.")


# =============================================================================
# 탭 3: 무결성 검사
# =============================================================================
with tab_integrity:
    st.caption("그래프 데이터의 일관성을 검사합니다. 고아 노드, 누락된 관계 등을 확인합니다.")

    if st.button("무결성 검사 실행", key="graph_integrity_btn", type="primary"):
        with st.spinner("무결성 검사 진행 중..."):
            result = api_client.graph_integrity_check()

        if api_failed(result):
            st.warning("데이터를 불러올 수 없습니다.")
        else:
            # Summary metrics
            orphan_count = result.get("orphan_count", result.get("orphan_nodes", 0))
            missing_rels = result.get("missing_relationships", result.get("missing_edges", 0))
            inconsistencies = result.get("inconsistencies", result.get("inconsistency_count", 0))
            total_nodes = result.get("total_nodes", 0)
            total_edges = result.get("total_edges", result.get("total_relationships", 0))

            m1, m2, m3, m4, m5 = st.columns(5)
            with m1:
                st.metric("전체 노드", f"{total_nodes:,}")
            with m2:
                st.metric("전체 관계", f"{total_edges:,}")
            with m3:
                st.metric("고아 노드", f"{orphan_count:,}")
            with m4:
                st.metric("누락 관계", f"{missing_rels:,}")
            with m5:
                st.metric("비일관성", f"{inconsistencies:,}")

            # Severity badge
            total_issues = orphan_count + missing_rels + inconsistencies
            if total_issues == 0:
                st.success("무결성 검사 통과 -- 이상 없음")
            elif total_issues <= 5:
                st.warning(f"경미한 이슈 {total_issues}건 발견")
            else:
                st.error(f"주의 필요: 이슈 {total_issues}건 발견")

            # Detail lists
            details = result.get("details", result.get("issues", []))
            if details:
                st.markdown("---")
                st.subheader("상세 이슈 목록")

                SEVERITY_BADGES = {
                    "CRITICAL": ":red[CRITICAL]",
                    "HIGH": ":orange[HIGH]",
                    "MEDIUM": ":orange[MEDIUM]",
                    "LOW": ":green[LOW]",
                    "INFO": ":blue[INFO]",
                }

                for issue in details[:30]:
                    issue_type = issue.get("type", issue.get("issue_type", "-"))
                    severity = issue.get("severity", "MEDIUM").upper()
                    description = issue.get("description", issue.get("message", "-"))
                    badge = SEVERITY_BADGES.get(severity, severity)

                    st.markdown(f"- {badge} **{issue_type}**: {description}")
            elif total_issues > 0:
                st.info("상세 이슈 정보는 API에서 제공되지 않습니다.")

    with st.expander("도움말: 무결성 검사 항목", expanded=False):
        st.markdown(
            """
            | 항목 | 설명 |
            |------|------|
            | 고아 노드 | 어떤 관계에도 연결되지 않은 단독 노드 |
            | 누락 관계 | 참조는 존재하지만 실제 관계가 없는 경우 |
            | 비일관성 | 양방향 관계 불일치, 타입 오류 등 |
            """
        )


# =============================================================================
# 탭 4: 그래프 정리
# =============================================================================
with tab_cleanup:
    st.caption("그래프 품질 정리 -- 플레이스홀더, 오분류 노드, 테스트 데이터 등을 정리합니다.")

    TASK_LABELS = {
        "placeholder_persons": ("👤 플레이스홀더 Person", "명시되지 않음, 미상, unknown 등"),
        "non_person_blocklist": ("🚫 Person 오분류", "시스템/플랫폼이 Person으로 분류된 경우"),
        "store_reclassify": ("🏪 Store 오분류", "플랫폼→System, 제품→삭제"),
        "normalize_kb_ids": ("🔗 KB ID 정규화", "하이픈→언더스코어 통일"),
        "test_nodes": ("🧪 테스트 노드", "kb_id가 test로 시작하는 노드"),
        "ocr_corrupted": ("📝 OCR 손상", "반복 문자, 낱자음/모음 (수동 확인 필요)"),
    }

    cleanup_col1, cleanup_col2 = st.columns([3, 1])
    with cleanup_col2:
        cleanup_kb = st.text_input(
            "KB ID 필터 (선택)",
            placeholder="전체 KB",
            key="cleanup_kb_filter",
        )

    with cleanup_col1:
        analyze_btn = st.button("분석 실행", key="cleanup_analyze_btn", type="primary")

    if analyze_btn:
        with st.spinner("그래프 품질 분석 중..."):
            kb_val = cleanup_kb.strip() if cleanup_kb and cleanup_kb.strip() else None
            analyze_result = api_client.graph_cleanup_analyze(kb_id=kb_val)

        if api_failed(analyze_result):
            st.warning("분석을 실행할 수 없습니다.")
        elif not analyze_result.get("success"):
            st.error(f"분석 실패: {analyze_result.get('error', '알 수 없는 오류')}")
        else:
            tasks = analyze_result.get("tasks", [])
            total_found = analyze_result.get("total_found", 0)

            # Store in session state for the apply button
            st.session_state["cleanup_analysis"] = analyze_result

            # Summary metrics
            issue_tasks = [t for t in tasks if t.get("found", 0) > 0]
            clean_tasks = [t for t in tasks if t.get("found", 0) == 0]

            if total_found == 0:
                st.success("정리할 이슈가 없습니다.")
            else:
                st.warning(f"{len(issue_tasks)}개 카테고리에서 총 {total_found}건의 이슈 발견")

            # Task details
            for task in tasks:
                task_id = task.get("task", "")
                found = task.get("found", 0)
                samples = task.get("samples", [])
                label_info = TASK_LABELS.get(task_id, (task_id, ""))
                label, desc = label_info

                if found > 0:
                    with st.expander(f"{label}: {found}건", expanded=True):
                        st.caption(desc)
                        if samples:
                            for s in samples:
                                st.markdown(f"- `{s}`")
                        if task.get("error"):
                            st.error(task["error"])
                else:
                    st.markdown(f"- {label}: 이상 없음")

    # Apply button (only show if analysis was done and issues found)
    if st.session_state.get("cleanup_analysis"):
        analysis = st.session_state["cleanup_analysis"]
        total_found = analysis.get("total_found", 0)

        if total_found > 0:
            st.markdown("---")
            st.warning(
                f"총 {total_found}건의 이슈를 정리합니다. "
                "OCR 손상 항목은 자동 수정되지 않습니다."
            )

            if st.button("정리 실행", key="cleanup_apply_btn", type="primary"):
                with st.spinner("그래프 정리 실행 중..."):
                    kb_val = analysis.get("kb_id")
                    apply_result = api_client.graph_cleanup_apply(kb_id=kb_val)

                if api_failed(apply_result):
                    st.error("정리를 실행할 수 없습니다.")
                elif not apply_result.get("success"):
                    st.error(f"정리 실패: {apply_result.get('error', '알 수 없는 오류')}")
                else:
                    tasks = apply_result.get("tasks", [])
                    total_fixed = apply_result.get("total_fixed", 0)

                    st.success(f"정리 완료: {total_fixed}건 수정")

                    for task in tasks:
                        task_id = task.get("task", "")
                        found = task.get("found", 0)
                        fixed = task.get("fixed", 0)
                        label_info = TASK_LABELS.get(task_id, (task_id, ""))
                        label, _ = label_info

                        if found > 0:
                            if fixed > 0:
                                st.markdown(f"- {label}: {found}건 중 **{fixed}건 수정**")
                            else:
                                st.markdown(f"- {label}: {found}건 발견 (수동 확인 필요)")

                    # Clear analysis cache so user re-analyzes next time
                    del st.session_state["cleanup_analysis"]

    # -----------------------------------------------------------------
    # AI 자동 분류 (LLM-based)
    # -----------------------------------------------------------------
    st.markdown("---")
    st.subheader("🤖 AI 자동 분류")
    st.caption("EXAONE LLM을 사용하여 미분류/오분류 노드의 올바른 타입을 자동 판별합니다")

    ai_col1, ai_col2 = st.columns(2)
    with ai_col1:
        ai_limit = st.number_input(
            "처리 건수 (0=전체)", min_value=0, max_value=10000, value=0,
            key="ai_classify_limit", help="0으로 설정하면 전체 미분류 노드를 처리합니다",
        )
    with ai_col2:
        ai_kb = st.text_input("KB ID (선택)", placeholder="전체", key="ai_classify_kb")

    ai_preview_btn = st.button("🤖 AI 분류 분석 (미리보기)", key="ai_classify_preview_btn")
    if ai_preview_btn:
        with st.spinner("LLM으로 엔티티 분류 중... (30초~2분 소요)"):
            kb_val = ai_kb.strip() if ai_kb and ai_kb.strip() else None
            ai_result = api_client.graph_ai_classify(
                limit=ai_limit, kb_id=kb_val, apply=False,
            )

        if api_failed(ai_result):
            st.warning("AI 분류를 실행할 수 없습니다.")
        elif not ai_result.get("success"):
            st.error(f"AI 분류 실패: {ai_result.get('error', '알 수 없는 오류')}")
        else:
            classifications = ai_result.get("classifications", [])
            st.session_state["ai_classify_result"] = ai_result

            if not classifications:
                st.success("분류할 대상이 없습니다.")
            else:
                st.info(
                    f"후보 {ai_result.get('candidates', 0)}건 중 "
                    f"{len(classifications)}건 분류 완료"
                )

                import pandas as pd
                df = pd.DataFrame([
                    {
                        "이름": c["name"],
                        "현재 타입": c["current_label"],
                        "제안 타입": c["new_type"],
                        "사유": c["reason"],
                        "KB": c.get("kb_id", ""),
                    }
                    for c in classifications
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

    # Apply button
    if st.session_state.get("ai_classify_result"):
        ai_res = st.session_state["ai_classify_result"]
        cls_count = len(ai_res.get("classifications", []))

        if cls_count > 0:
            if st.button("🤖 AI 분류 적용", type="primary", key="ai_classify_apply_btn"):
                with st.spinner("AI 분류 결과 적용 중..."):
                    kb_val = ai_res.get("kb_id")
                    apply_result = api_client.graph_ai_classify(
                        limit=ai_limit, kb_id=kb_val, apply=True,
                    )

                if api_failed(apply_result):
                    st.error("AI 분류 적용에 실패했습니다.")
                elif not apply_result.get("success"):
                    st.error(f"적용 실패: {apply_result.get('error', '알 수 없는 오류')}")
                else:
                    stats = apply_result.get("stats", {})
                    st.success(
                        f"AI 분류 적용 완료: "
                        f"재분류 {stats.get('relabeled', 0)}건, "
                        f"삭제 {stats.get('deleted', 0)}건, "
                        f"건너뜀 {stats.get('skipped', 0)}건"
                    )
                    if stats.get("errors", 0) > 0:
                        st.warning(f"오류 {stats['errors']}건 발생")

                    del st.session_state["ai_classify_result"]

    with st.expander("도움말: 그래프 정리 항목", expanded=False):
        st.markdown(
            """
            | 항목 | 설명 | 자동 수정 |
            |------|------|----------|
            | 플레이스홀더 Person | "명시되지 않음", "미상" 등 의미 없는 Person 노드 | O |
            | Person 오분류 | JIRA, Kubernetes 등 시스템이 Person으로 분류 | O |
            | Store 오분류 | 플랫폼(→System)이나 제품이 Store로 분류 | O |
            | KB ID 정규화 | itops-general → itops_general 등 | O |
            | 테스트 노드 | kb_id가 test로 시작하는 노드 제거 | O |
            | OCR 손상 | 반복 문자, 낱자음/모음 등 비정상 이름 | X (수동) |
            | AI 자동 분류 | LLM으로 미분류/오분류 노드 타입 판별 | O (확인 후) |
            """
        )
