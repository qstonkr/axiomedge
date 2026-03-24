"""Embedding providers for local knowledge inference."""

from .onnx_provider import OnnxBgeEmbeddingProvider
from .ollama_provider import OllamaEmbeddingProvider

__all__ = ["OnnxBgeEmbeddingProvider", "OllamaEmbeddingProvider"]
