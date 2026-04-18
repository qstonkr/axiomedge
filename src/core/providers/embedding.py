"""Embedding Provider Factory.

Factory pattern for creating embedding providers.
Supports: "ollama", "onnx", "tei".

Resolution order matches app.py: TEI > Ollama > ONNX.

py.
Simplified: no Cohere/Remote/LiteLLM providers.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.nlp.embedding.types import EmbeddingProvider

logger = logging.getLogger(__name__)


def create_embedding_provider(
    provider_type: str | None = None,
    **kwargs,
) -> EmbeddingProvider:
    """Create an embedding provider by type.

    Args:
        provider_type: One of "tei", "ollama", "onnx", or None (auto-detect).
        **kwargs: Provider-specific keyword arguments.

    Returns:
        An embedding provider instance.

    Raises:
        RuntimeError: If no provider could be created.
    """
    if provider_type is None:
        return _auto_detect(**kwargs)

    provider_type = provider_type.lower().strip()

    if provider_type == "tei":
        return _create_tei(**kwargs)
    elif provider_type == "ollama":
        return _create_ollama(**kwargs)
    elif provider_type == "onnx":
        return _create_onnx(**kwargs)
    else:
        raise ValueError(f"Unknown embedding provider type: {provider_type}")


def _auto_detect(**kwargs) -> EmbeddingProvider:
    """Auto-detect best available provider: TEI > Ollama > ONNX."""
    # 1. TEI
    tei_url = kwargs.get("tei_url") or os.getenv("BGE_TEI_URL")
    if tei_url:
        try:
            provider = _create_tei(base_url=tei_url, **kwargs)
            if provider.is_ready():
                logger.info("Auto-detected TEI embedding provider: %s", tei_url)
                return provider
        except (OSError, ImportError, RuntimeError, ValueError) as e:
            logger.debug("TEI not available: %s", e)

    # 2. Ollama
    from src.config import get_settings
    ollama_url = kwargs.get("ollama_url") or get_settings().ollama.base_url
    try:
        provider = _create_ollama(base_url=ollama_url, **kwargs)
        if provider.is_ready():
            logger.info("Auto-detected Ollama embedding provider: %s", ollama_url)
            return provider
    except (OSError, ImportError, RuntimeError, ValueError) as e:
        logger.debug("Ollama not available: %s", e)

    # 3. ONNX
    try:
        provider = _create_onnx(**kwargs)
        if provider.is_ready():
            logger.info("Auto-detected ONNX embedding provider")
            return provider
    except (OSError, ImportError, RuntimeError, ValueError) as e:
        logger.debug("ONNX not available: %s", e)

    raise RuntimeError(
        "No embedding provider available. Options:\n"
        "  - Set BGE_TEI_URL for HuggingFace TEI server\n"
        "  - Run 'ollama pull bge-m3' for Ollama\n"
        "  - Set KNOWLEDGE_BGE_ONNX_MODEL_PATH for ONNX model"
    )


def _create_tei(**kwargs) -> EmbeddingProvider:
    from src.nlp.embedding.tei_provider import TEIEmbeddingProvider

    from src.config import get_settings as _gs
    base_url = kwargs.get("base_url") or _gs().tei.embedding_url
    timeout = kwargs.get("timeout", 60.0)
    return TEIEmbeddingProvider(base_url=base_url, timeout=timeout)


def _create_ollama(**kwargs) -> EmbeddingProvider:
    from src.nlp.embedding.ollama_provider import OllamaEmbeddingProvider

    from src.config import DEFAULT_EMBEDDING_MODEL, get_settings as _gs2
    base_url = kwargs.get("base_url") or _gs2().ollama.base_url
    model = kwargs.get("model", DEFAULT_EMBEDDING_MODEL)
    timeout = kwargs.get("timeout", 60.0)
    return OllamaEmbeddingProvider(base_url=base_url, model=model, timeout=timeout)


def _create_onnx(**kwargs) -> EmbeddingProvider:
    from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider

    model_path = kwargs.get("model_path") or os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "")
    from src.config import DEFAULT_EMBEDDING_MODEL_HF

    model_name = kwargs.get("model_name", DEFAULT_EMBEDDING_MODEL_HF)
    return OnnxBgeEmbeddingProvider(model_name=model_name, model_path=model_path)
