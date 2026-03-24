"""4-Group Persona-Based Sidebar (Local version)

All pages visible (admin mode), no session expiry warnings.
"""

import streamlit as st

from services.feature_flags import get_feature_flags


def hide_default_nav():
    """Hide Streamlit auto-generated page navigation."""
    st.markdown(
        "<style>[data-testid='stSidebarNav'] {display: none;}</style>",
        unsafe_allow_html=True,
    )


def render_sidebar(show_admin: bool = False, user_role: str | None = None):
    """Custom Korean sidebar rendering (4-group persona-based).

    All pages are visible for local development (admin mode).
    """
    hide_default_nav()
    user_role = "admin"  # Always admin for local

    with st.sidebar:
        st.title("지식 관리 시스템")
        st.caption("Knowledge Dashboard (Local)")

        st.markdown("---")

        ff = get_feature_flags()

        # -- Group 1: All users --
        st.page_link("app.py", label="🏠 홈")
        if ff.chat_enabled:
            active_group = st.session_state.get("_active_group_name")
            group_label = f"💬 지식 검색 [{active_group}]" if active_group else "💬 지식 검색"
            st.page_link("pages/chat.py", label=group_label)
        st.page_link("pages/find_owner.py", label="👤 담당자 찾기")
        st.page_link("pages/error_report.py", label="🚨 오류 신고")

        if ff.chat_enabled:
            if st.button("🔄 새 대화", use_container_width=True):
                import uuid
                st.session_state.chat_session_id = str(uuid.uuid4())
                st.session_state.chat_messages = []
                st.session_state.feedback_submitted = {}
                st.session_state.show_error_report = None
                st.switch_page("pages/chat.py")

        st.markdown("---")

        # -- Group 2: My Activity --
        with st.expander("📋 나의 활동", expanded=False):
            st.page_link("pages/my_feedback.py", label="📝 내 피드백")
            st.page_link("pages/my_documents.py", label="📄 내 담당 문서")
            st.page_link("pages/search_history.py", label="🕐 검색 이력")

        # -- Group 3: Knowledge Management (always visible for local) --
        if ff.admin_enabled:
            with st.expander("📚 지식 관리", expanded=show_admin):
                st.page_link("pages/dashboard.py", label="📊 KB 현황")
                st.page_link("pages/quality.py", label="📈 품질 관리")
                if ff.graph_enabled:
                    st.page_link("pages/graph.py", label="🕸️ 지식 그래프")
                st.page_link("pages/glossary.py", label="📖 용어집")
                st.page_link("pages/owners.py", label="👥 담당자 관리")
                st.page_link("pages/conflicts.py", label="⚠️ 충돌 / 중복")
                st.page_link("pages/traceability.py", label="🔗 추적 / 버전")
                st.page_link("pages/gaps.py", label="📉 지식 갭")
                st.page_link("pages/verification.py", label="✅ 검증 관리")
                st.page_link("pages/feedback_admin.py", label="💬 피드백 관리")

        # -- Group 4: System Operations (always visible for local) --
        if ff.operations_enabled:
            with st.expander("⚙️ 시스템 운영", expanded=False):
                st.page_link("pages/operations.py", label="🔄 파이프라인 현황")
                st.page_link("pages/ingestion_jobs.py", label="📥 인제스천 작업")
                st.page_link("pages/data_sources.py", label="📁 데이터 소스")
                st.page_link("pages/search_analytics.py", label="🔍 검색 분석")
                st.page_link("pages/impact.py", label="📊 영향도 / 사용량")
                st.page_link("pages/evaluation.py", label="🧪 RAG 평가")
                st.page_link("pages/rag_quality.py", label="🎯 RAG 품질 검증")
                if ff.graph_enabled:
                    st.page_link("pages/graph_integrity.py", label="🛡️ 그래프 무결성")
                st.page_link("pages/pipeline_health.py", label="🏥 파이프라인 건강")
                st.page_link("pages/whitelist.py", label="🔑 접근 허용 목록")

        st.markdown("---")
        col_cache1, col_cache2 = st.columns(2)
        with col_cache1:
            if st.button("🗑️ UI 캐시", use_container_width=True, help="대시보드 캐시 초기화"):
                st.cache_data.clear()
                st.toast("UI 캐시가 초기화되었습니다.")
                st.rerun()
        with col_cache2:
            if st.button("🔄 검색 캐시", use_container_width=True, help="서버 검색 캐시 삭제"):
                from services import api_client
                result = api_client.clear_search_cache()
                if not api_client.api_failed(result):
                    st.cache_data.clear()
                    st.toast("서버 검색 캐시가 삭제되었습니다.")
                else:
                    st.toast("캐시 삭제 실패", icon="⚠️")
                st.rerun()

        # 모델 정보 표시
        st.markdown("---")
        import os
        _llm_model = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
        _embed_model = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")
        _embed_backend = "Ollama" if os.getenv("OLLAMA_BASE_URL") else "ONNX"
        st.caption(f"LLM: `{_llm_model}`")
        st.caption(f"Embed: `{_embed_model}` ({_embed_backend})")
