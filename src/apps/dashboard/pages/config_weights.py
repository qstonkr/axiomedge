"""Config Weights

검색 가중치, 임베딩/LLM 설정, 리셋 관리.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="Config Weights", page_icon="⚖️", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/config", "가중치 설정")

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar()

st.title("⚖️ Config Weights")

tab_weights, tab_models, tab_reset = st.tabs(["검색 가중치", "임베딩/LLM", "리셋"])

# =============================================================================
# Tab 1: 검색 가중치
# =============================================================================
with tab_weights:
    st.subheader("검색 가중치 설정")
    st.caption("검색 결과의 리랭킹에 사용되는 가중치를 조절합니다.")

    # Load current weights
    weights_result = api_client.get_config_weights()

    if api_failed(weights_result):
        st.warning("현재 가중치를 불러올 수 없습니다. 기본값을 표시합니다.")
        current_weights = {}
    else:
        current_weights = weights_result.get("weights", weights_result)

    # Weight sliders
    with st.form("weights_form"):
        st.markdown("각 가중치를 조절한 후 **저장**을 클릭하세요.")

        w_col1, w_col2 = st.columns(2)

        with w_col1:
            dense_weight = st.slider(
                "Dense Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("hybrid_search", {}).get("dense_weight", 0.4)),
                step=0.05,
                help="Dense vector 검색 가중치",
            )
            sparse_weight = st.slider(
                "Sparse Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("hybrid_search", {}).get("sparse_weight", 0.3)),
                step=0.05,
                help="Sparse (BM25) 검색 가중치",
            )
            colbert_weight = st.slider(
                "ColBERT Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("hybrid_search", {}).get("colbert_weight", 0.3)),
                step=0.05,
                help="ColBERT 검색 가중치",
            )

        with w_col2:
            model_weight = st.slider(
                "Model Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("reranker", {}).get("model_weight", 0.5)),
                step=0.05,
                help="Cross-encoder 모델 가중치",
            )
            base_score_weight = st.slider(
                "Base Score Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("reranker", {}).get("base_weight", 0.3)),
                step=0.05,
                help="기본 점수 가중치",
            )
            source_weight = st.slider(
                "Source Weight",
                min_value=0.0, max_value=1.0,
                value=float(current_weights.get("reranker", {}).get("source_weight", 0.1)),
                step=0.05,
                help="소스 신뢰도 가중치",
            )

        submitted = st.form_submit_button("저장", type="primary")

        if submitted:
            new_weights = {
                "hybrid_search.dense_weight": dense_weight,
                "hybrid_search.sparse_weight": sparse_weight,
                "hybrid_search.colbert_weight": colbert_weight,
                "reranker.model_weight": model_weight,
                "reranker.base_weight": base_score_weight,
                "reranker.source_weight": source_weight,
            }
            update_result = api_client.update_config_weights(new_weights)
            if api_failed(update_result):
                st.error("가중치 저장에 실패했습니다.")
            else:
                st.success("가중치가 저장되었습니다.")
                st.rerun()

    # Display current total
    total = dense_weight + sparse_weight + colbert_weight
    st.caption(f"검색 가중치 합계: {total:.2f} (Dense + Sparse + ColBERT)")
    if abs(total - 1.0) > 0.01:
        st.warning(f"검색 가중치 합계가 1.0이 아닙니다 ({total:.2f}). 정규화됩니다.")

# =============================================================================
# Tab 2: 임베딩/LLM 설정 (read-only)
# =============================================================================
with tab_models:
    st.subheader("임베딩 / LLM / OCR 설정")
    st.caption("현재 설정된 모델 정보입니다. (읽기 전용)")

    import os

    config_col1, config_col2 = st.columns(2)

    with config_col1:
        st.markdown("**LLM 설정**")
        with st.container(border=True):
            llm_model = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
            llm_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            st.markdown(f"- **모델**: `{llm_model}`")
            st.markdown(f"- **Ollama URL**: `{llm_url}`")
            st.markdown(f"- **Temperature**: `{os.getenv('LLM_TEMPERATURE', '0.1')}`")
            st.markdown(f"- **Max Tokens**: `{os.getenv('LLM_MAX_TOKENS', '2048')}`")

        st.markdown("**OCR 설정**")
        with st.container(border=True):
            ocr_engine = os.getenv("OCR_ENGINE", "tesseract")
            st.markdown(f"- **엔진**: `{ocr_engine}`")
            st.markdown(f"- **언어**: `{os.getenv('OCR_LANGUAGE', 'kor+eng')}`")

    with config_col2:
        st.markdown("**임베딩 설정**")
        with st.container(border=True):
            embed_model = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")
            embed_backend = "Ollama" if os.getenv("OLLAMA_BASE_URL") else "ONNX"
            st.markdown(f"- **모델**: `{embed_model}`")
            st.markdown(f"- **백엔드**: `{embed_backend}`")
            st.markdown(f"- **차원**: `{os.getenv('EMBEDDING_DIMENSION', '1024')}`")

        st.markdown("**Qdrant 설정**")
        with st.container(border=True):
            qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
            st.markdown(f"- **URL**: `{qdrant_url}`")
            st.markdown(f"- **Collection prefix**: `{os.getenv('QDRANT_COLLECTION_PREFIX', 'kb_')}`")

# =============================================================================
# Tab 3: 리셋
# =============================================================================
with tab_reset:
    st.subheader("가중치 리셋")
    st.caption("모든 가중치를 기본값으로 초기화합니다.")

    st.warning("이 작업은 현재 설정된 검색 가중치를 모두 기본값으로 되돌립니다.")

    if st.button("가중치 초기화", type="primary"):
        reset_result = api_client.reset_config_weights()
        if api_failed(reset_result):
            st.error("리셋에 실패했습니다.")
        else:
            default_weights = reset_result.get("current", {})
            st.success("가중치가 기본값으로 초기화되었습니다.")
            if default_weights:
                st.json(default_weights)
            st.rerun()
