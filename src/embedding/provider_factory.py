"""Embedding Provider Factory.

Factory pattern for creating embedding providers.
Supports: "ollama", "onnx", "tei".

Resolution order matches app.py: TEI > Ollama > ONNX.

Adapted from oreo-ecosystem infrastructure/embedding/provider_factory.py.
Simplified: no Cohere/Remote/LiteLLM providers.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def create_embedding_provider(
    provider_type: str | None = None,
    **kwargs,
):
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


def _auto_detect(**kwargs):
    """Auto-detect best available provider: TEI > Ollama > ONNX."""
    # 1. TEI
    tei_url = kwargs.get("tei_url") or os.getenv("BGE_TEI_URL")
    if tei_url:
        try:
            provider = _create_tei(base_url=tei_url, **kwargs)
            if provider.is_ready():
                logger.info("Auto-detected TEI embedding provider: %s", tei_url)
                return provider
        except Exception as e:
            logger.debug("TEI not available: %s", e)

    # 2. Ollama
    ollama_url = kwargs.get("ollama_url") or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        provider = _create_ollama(base_url=ollama_url, **kwargs)
        if provider.is_ready():
            logger.info("Auto-detected Ollama embedding provider: %s", ollama_url)
            return provider
    except Exception as e:
        logger.debug("Ollama not available: %s", e)

    # 3. ONNX
    try:
        provider = _create_onnx(**kwargs)
        if provider.is_ready():
            logger.info("Auto-detected ONNX embedding provider")
            return provider
    except Exception as e:
        logger.debug("ONNX not available: %s", e)

    raise RuntimeError(
        "No embedding provider available. Options:\n"
        "  - Set BGE_TEI_URL for HuggingFace TEI server\n"
        "  - Run 'ollama pull bge-m3' for Ollama\n"
        "  - Set KNOWLEDGE_BGE_ONNX_MODEL_PATH for ONNX model"
    )


def _create_tei(**kwargs):
    from src.embedding.tei_provider import TEIEmbeddingProvider

    base_url = kwargs.get("base_url") or os.getenv("BGE_TEI_URL", "http://localhost:8080")
    timeout = kwargs.get("timeout", 60.0)
    return TEIEmbeddingProvider(base_url=base_url, timeout=timeout)


def _create_ollama(**kwargs):
    from src.embedding.ollama_provider import OllamaEmbeddingProvider

    base_url = kwargs.get("base_url") or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = kwargs.get("model", "bge-m3:latest")
    timeout = kwargs.get("timeout", 60.0)
    return OllamaEmbeddingProvider(base_url=base_url, model=model, timeout=timeout)


def _create_onnx(**kwargs):
    from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider

    model_path = kwargs.get("model_path") or os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "")
    model_name = kwargs.get("model_name", "BAAI/bge-m3")
    return OnnxBgeEmbeddingProvider(model_name=model_name, model_path=model_path)
