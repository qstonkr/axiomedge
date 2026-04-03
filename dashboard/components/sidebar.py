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
            st.page_link("pages/my_activities.py", label="📋 나의 활동")

        # -- Group 3: Knowledge Management (always visible for local) --
        if ff.admin_enabled:
            with st.expander("📚 지식 관리", expanded=show_admin):
                st.page_link("pages/dashboard.py", label="📊 KB 현황")
                st.page_link("pages/search_groups.py", label="📂 검색 그룹")
                st.page_link("pages/quality.py", label="📈 품질 관리")
                st.page_link("pages/golden_set.py", label="🎯 골든 셋 관리")
                st.page_link("pages/glossary.py", label="📖 용어집")
                st.page_link("pages/owners.py", label="👥 담당자 관리")
                st.page_link("pages/conflicts.py", label="⚠️ 충돌 / 중복")
                st.page_link("pages/verification.py", label="✅ 검증 관리")
                st.page_link("pages/graph_explorer.py", label="🔗 지식 그래프")
                st.page_link("pages/doc_lifecycle.py", label="📋 문서 라이프사이클")

        # -- Group 4: System Operations (always visible for local) --
        if ff.operations_enabled:
            with st.expander("⚙️ 시스템 운영", expanded=False):
                st.page_link("pages/ingestion_jobs.py", label="📥 인제스천 작업")
                st.page_link("pages/data_sources.py", label="📁 데이터 소스")
                st.page_link("pages/job_monitor.py", label="⚙️ Job Monitor")
                st.page_link("pages/config_weights.py", label="⚖️ Config Weights")
                st.page_link("pages/ingestion_gate.py", label="🚦 인제스천 게이트")
                st.page_link("pages/auth_management.py", label="🔐 Auth / RBAC")

        st.markdown("---")
        if st.button("🗑️ 캐시 전체 삭제", use_container_width=True, help="UI 캐시 + 서버 검색 캐시 모두 삭제"):
            from services import api_client
            # 1. 서버 검색 캐시 (Redis)
            api_client.clear_search_cache()
            # 2. Streamlit 서버 메모리 캐시
            st.cache_data.clear()
            st.cache_resource.clear()
            st.toast("모든 캐시가 삭제되었습니다.")
            st.rerun()

        # 모델 정보 표시 (detect actual backend from env)
        st.markdown("---")
        import os
        _use_sagemaker = os.getenv("USE_SAGEMAKER_LLM", "false").lower() == "true"
        if _use_sagemaker:
            _llm_model = os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")
            _llm_backend = "SageMaker"
        else:
            _llm_model = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
            _llm_backend = "Ollama"

        # Embedding: check TEI first, then Ollama, then ONNX
        _tei_url = os.getenv("TEI_EMBEDDING_URL", "")
        if _tei_url:
            _embed_backend = "TEI"
        elif os.getenv("OLLAMA_BASE_URL"):
            _embed_backend = "Ollama"
        else:
            _embed_backend = "ONNX"
        _embed_model = os.getenv("EMBEDDING_MODEL", "bge-m3")

        st.caption(f"LLM: `{_llm_model}` ({_llm_backend})")
        st.caption(f"Embed: `{_embed_model}` ({_embed_backend})")
