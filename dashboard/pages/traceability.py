"""추적 / 버전 -- 출처, 계보, 버전, 생애주기 통합 페이지

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="추적 / 버전", page_icon="🔗", layout="wide")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("추적 / 버전")
st.caption("문서의 출처, 계보, 버전 이력, 생애주기를 추적합니다.")

# ---------------------------------------------------------------------------
# 공통: 문서 ID 입력
# ---------------------------------------------------------------------------
doc_id = st.text_input("문서 ID", placeholder="추적할 문서 ID를 입력하세요", key="trace_doc_id")

tab_provenance, tab_lineage, tab_version, tab_lifecycle = st.tabs(
    ["출처 (Provenance)", "계보 (Lineage)", "버전", "생애주기"]
)

# ============================================================================
# 1) 출처 (Provenance)
# ============================================================================
with tab_provenance:
    if not doc_id:
        st.info("문서 ID를 입력하면 출처 정보를 조회합니다.")
    else:
        data = api_client.get_document_provenance(doc_id)
        if api_failed(data):
            st.error("API 연결 실패")
            if st.button("재시도", key="retry_prov"):
                st.cache_data.clear()
                st.rerun()
        else:
            st.subheader("출처 정보")

            # SourceType 6종
            SOURCE_TYPES = [
                "CONFLUENCE", "JIRA", "SHAREPOINT", "GIT_DOCS", "TEAMS", "MANUAL"
            ]
            # ParserType 8종
            PARSER_TYPES = [
                "HTML", "MARKDOWN", "PDF", "DOCX", "XLSX", "PPTX", "CSV", "PLAIN_TEXT"
            ]
            # VerificationStatus 5종
            VERIFICATION_STATUSES = [
                "UNVERIFIED", "PENDING", "VERIFIED", "REJECTED", "EXPIRED"
            ]

            col1, col2, col3 = st.columns(3)
            with col1:
                source_type = data.get("source_type", "UNKNOWN")
                st.metric("소스 유형", source_type)
                st.caption(f"지원: {', '.join(SOURCE_TYPES)}")
            with col2:
                parser_type = data.get("parser_type", "UNKNOWN")
                st.metric("파서 유형", parser_type)
                st.caption(f"지원: {', '.join(PARSER_TYPES)}")
            with col3:
                verification = data.get("verification_status", "UNVERIFIED")
                color_map = {
                    "VERIFIED": "green", "PENDING": "orange", "REJECTED": "red",
                    "EXPIRED": "gray", "UNVERIFIED": "blue",
                }
                color = color_map.get(verification, "gray")
                st.markdown(
                    f"**검증 상태**: :{color}[{verification}]"
                )

            # ExtractionMetadata
            st.markdown("---")
            st.subheader("추출 메타데이터")
            extraction = data.get("extraction_metadata", {})
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("추출 시간", f"{extraction.get('extraction_time_ms', 0)}ms")
            with m2:
                st.metric("토큰 수", f"{extraction.get('token_count', 0):,}")
            with m3:
                confidence = extraction.get("confidence", 0)
                st.metric("신뢰도", f"{confidence:.2%}")

            # ProvenanceChain timeline
            st.markdown("---")
            st.subheader("출처 체인 (타임라인)")
            chain = data.get("provenance_chain", [])
            if chain:
                for i, entry in enumerate(chain):
                    icon = "🟢" if i == 0 else "🔵"
                    ts = entry.get("timestamp", "")
                    action = entry.get("action", "")
                    actor = entry.get("actor", "")
                    st.markdown(f"{icon} **{ts}** - {action} (by {actor})")
            else:
                st.info("출처 체인 데이터가 없습니다.")

# ============================================================================
# 2) 계보 (Lineage)
# ============================================================================
with tab_lineage:
    if not doc_id:
        st.info("문서 ID를 입력하면 계보 정보를 조회합니다.")
    else:
        data = api_client.get_document_lineage(doc_id)
        if api_failed(data):
            st.error("API 연결 실패")
            if st.button("재시도", key="retry_lineage"):
                st.cache_data.clear()
                st.rerun()
        else:
            st.subheader("문서 계보")

            # LineageRelationType 12종
            RELATION_TYPES = [
                "DERIVED_FROM", "SUPERSEDES", "REFERENCES", "PART_OF",
                "DEPENDS_ON", "EXTENDS", "IMPLEMENTS", "CONTRADICTS",
                "SUPPLEMENTS", "UPDATES", "REPLACES", "RELATED_TO",
            ]

            # LineageEventType 9종
            EVENT_TYPES = [
                "CREATED", "UPDATED", "MERGED", "SPLIT", "ARCHIVED",
                "RESTORED", "LINKED", "UNLINKED", "MIGRATED",
            ]

            # Graph visualization
            st.markdown("#### 관계 그래프")
            relations = data.get("relations", [])
            if relations:
                import plotly.graph_objects as go

                nodes = set()
                edges = []
                for rel in relations:
                    src = rel.get("source_id", "")
                    tgt = rel.get("target_id", "")
                    rtype = rel.get("relation_type", "RELATED_TO")
                    nodes.add(src)
                    nodes.add(tgt)
                    edges.append((src, tgt, rtype))

                node_list = list(nodes)
                node_idx = {n: i for i, n in enumerate(node_list)}
                import math

                n = len(node_list)
                x_pos = [math.cos(2 * math.pi * i / max(n, 1)) for i in range(n)]
                y_pos = [math.sin(2 * math.pi * i / max(n, 1)) for i in range(n)]

                edge_x, edge_y = [], []
                annotations = []
                for src, tgt, rtype in edges:
                    si, ti = node_idx[src], node_idx[tgt]
                    edge_x += [x_pos[si], x_pos[ti], None]
                    edge_y += [y_pos[si], y_pos[ti], None]
                    annotations.append(
                        dict(
                            x=(x_pos[si] + x_pos[ti]) / 2,
                            y=(y_pos[si] + y_pos[ti]) / 2,
                            text=rtype,
                            showarrow=False,
                            font=dict(size=9, color="gray"),
                        )
                    )

                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(color="#ccc"), hoverinfo="none")
                )
                fig.add_trace(
                    go.Scatter(
                        x=x_pos, y=y_pos, mode="markers+text",
                        marker=dict(size=20, color="#4A90D9"),
                        text=[nid[:12] for nid in node_list],
                        textposition="top center",
                        hovertext=node_list,
                    )
                )
                fig.update_layout(
                    showlegend=False, margin=dict(l=20, r=20, t=20, b=20),
                    xaxis=dict(visible=False), yaxis=dict(visible=False),
                    annotations=annotations, height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"관계 유형 ({len(RELATION_TYPES)}종): {', '.join(RELATION_TYPES)}")
            else:
                st.info("계보 관계 데이터가 없습니다.")

            # Event timeline
            st.markdown("---")
            st.subheader("이벤트 타임라인")
            events = data.get("events", [])
            if events:
                for evt in events:
                    etype = evt.get("event_type", "UNKNOWN")
                    ts = evt.get("timestamp", "")
                    detail = evt.get("detail", "")
                    icon_map = {
                        "CREATED": "🆕", "UPDATED": "📝", "MERGED": "🔀",
                        "SPLIT": "✂️", "ARCHIVED": "📦", "RESTORED": "♻️",
                        "LINKED": "🔗", "UNLINKED": "🔓", "MIGRATED": "🚚",
                    }
                    icon = icon_map.get(etype, "📌")
                    st.markdown(f"{icon} **{ts}** - `{etype}` {detail}")
                st.caption(f"이벤트 유형 ({len(EVENT_TYPES)}종): {', '.join(EVENT_TYPES)}")
            else:
                st.info("계보 이벤트 데이터가 없습니다.")

# ============================================================================
# 3) 버전
# ============================================================================
with tab_version:
    if not doc_id:
        st.info("문서 ID를 입력하면 버전 이력을 조회합니다.")
    else:
        data = api_client.get_document_versions(doc_id)
        if api_failed(data):
            st.error("API 연결 실패")
            if st.button("재시도", key="retry_ver"):
                st.cache_data.clear()
                st.rerun()
        else:
            st.subheader("버전 이력")

            versions = data.get("versions", [])
            if versions:
                # VersionChangeType timeline
                import plotly.graph_objects as go

                fig = go.Figure()
                for ver in versions:
                    change_type = ver.get("change_type", "PATCH")
                    color_map = {"MAJOR": "red", "MINOR": "#FFB300", "PATCH": "green"}
                    color = color_map.get(change_type, "gray")
                    version_num = ver.get("version", "")
                    ts = ver.get("created_at", "")

                    fig.add_trace(
                        go.Scatter(
                            x=[ts], y=[version_num],
                            mode="markers+text",
                            marker=dict(size=14, color=color),
                            text=[f"{change_type}"],
                            textposition="top center",
                            name=f"v{version_num} ({change_type})",
                        )
                    )

                fig.update_layout(
                    title="버전 변경 타임라인",
                    xaxis_title="시간", yaxis_title="버전",
                    showlegend=True, height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                # Version diff view
                st.markdown("---")
                st.subheader("버전 상세")
                for ver in versions:
                    version_num = ver.get("version", "")
                    change_type = ver.get("change_type", "PATCH")
                    author = ver.get("author", "")
                    created = ver.get("created_at", "")
                    summary = ver.get("summary", "변경 내용 없음")
                    approval = ver.get("approval_status", "PENDING")

                    # ApprovalStatus workflow badges
                    badge_map = {
                        "APPROVED": "🟢 승인", "PENDING": "🟡 대기",
                        "REJECTED": "🔴 반려", "DRAFT": "⚪ 초안",
                    }
                    badge = badge_map.get(approval, f"⚪ {approval}")

                    with st.expander(f"v{version_num} ({change_type}) - {badge}"):
                        st.markdown(f"**작성자**: {author}")
                        st.markdown(f"**생성일**: {created}")
                        st.markdown(f"**요약**: {summary}")

                        diff_content = ver.get("diff", "")
                        if diff_content:
                            st.code(diff_content, language="diff")

                # Rollback
                st.markdown("---")
                st.subheader("버전 롤백")
                target_version = st.text_input("롤백 대상 버전", key="rollback_ver")
                rollback_reason = st.text_area("롤백 사유", key="rollback_reason")
                if st.button("롤백 실행", type="primary", key="btn_rollback"):
                    if target_version:
                        result = api_client.rollback_document_version(
                            doc_id,
                            {"target_version": target_version, "reason": rollback_reason},
                        )
                        if api_failed(result):
                            st.error("롤백 실패: API 연결 실패")
                        else:
                            st.success(f"v{target_version}으로 롤백 완료")
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        st.warning("롤백 대상 버전을 입력하세요.")
            else:
                st.info("버전 이력이 없습니다.")

# ============================================================================
# 4) 생애주기
# ============================================================================
with tab_lifecycle:
    st.subheader("KB 생애주기")

    kbs_result = api_client.list_kbs()
    if api_failed(kbs_result):
        st.error("API 연결 실패")
        if st.button("재시도", key="retry_lc"):
            st.cache_data.clear()
            st.rerun()
    else:
        kb_items = kbs_result.get("items", [])
        kb_options = {kb.get("name", kb.get("id", "")): kb.get("id", kb.get("kb_id", "")) for kb in kb_items}

        if kb_options:
            selected_kb_name = st.selectbox("KB 선택", list(kb_options.keys()), key="lc_kb")
            kb_id = kb_options[selected_kb_name]

            lc_data = api_client.get_kb_lifecycle(kb_id)
            if api_failed(lc_data):
                st.error("API 연결 실패")
                if st.button("재시도", key="retry_lc2"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                # 5 LifecycleStatus states
                LIFECYCLE_STATES = ["PROPOSED", "APPROVED", "ACTIVE", "DEPRECATED", "ARCHIVED"]

                current = lc_data.get("status", "ACTIVE")
                st.markdown(f"**현재 상태**: `{current}`")

                # State machine diagram (mermaid)
                st.markdown("#### 상태 전이 다이어그램")
                st.markdown("""```mermaid
stateDiagram-v2
    [*] --> PROPOSED
    PROPOSED --> APPROVED : 승인
    PROPOSED --> ARCHIVED : 거절
    APPROVED --> ACTIVE : 활성화
    ACTIVE --> DEPRECATED : 비활성화
    DEPRECATED --> ARCHIVED : 보관
    DEPRECATED --> ACTIVE : 재활성화
    ARCHIVED --> [*]
```""")

                # Highlight current state
                cols = st.columns(5)
                for i, state in enumerate(LIFECYCLE_STATES):
                    with cols[i]:
                        if state == current:
                            st.markdown(f"**:green[● {state}]**")
                        else:
                            st.markdown(f":gray[○ {state}]")

                # DocumentFreshness 90/180 day decay chart
                st.markdown("---")
                st.subheader("문서 신선도 감쇠")
                freshness_data = lc_data.get("freshness", {})
                total = freshness_data.get("total_documents", 0)
                fresh_90 = freshness_data.get("fresh_90d", 0)
                fresh_180 = freshness_data.get("fresh_180d", 0)
                stale = freshness_data.get("stale", 0)

                if total > 0:
                    import plotly.graph_objects as go

                    fig = go.Figure(
                        go.Bar(
                            x=["90일 이내", "180일 이내", "180일 초과"],
                            y=[fresh_90, fresh_180 - fresh_90, stale],
                            marker_color=["#2ECC71", "#F39C12", "#E74C3C"],
                        )
                    )
                    fig.update_layout(
                        title="문서 신선도 분포",
                        xaxis_title="기간", yaxis_title="문서 수",
                        height=300, margin=dict(l=20, r=20, t=40, b=20),
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("신선도 데이터가 없습니다.")

                # Transition history
                transitions = lc_data.get("transitions", [])
                if transitions:
                    st.markdown("#### 전이 이력")
                    for t in transitions:
                        st.markdown(
                            f"- **{t.get('from_status', '')}** → **{t.get('to_status', '')}** "
                            f"({t.get('timestamp', '')}) by {t.get('actor', '')}"
                        )
        else:
            st.info("등록된 KB가 없습니다.")
