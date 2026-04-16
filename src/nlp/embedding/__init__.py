"""Embedding providers for local knowledge inference.

Providers:
- OnnxBgeEmbeddingProvider: ONNX Runtime CPU inference (no server needed)
- OllamaEmbeddingProvider: Ollama Metal GPU (Apple Silicon)
- TEIEmbeddingProvider: HuggingFace TEI dedicated server (fastest)

Orchestration:
- DualEmbeddingProvider: Dense + sparse from single BGE-M3 model
- create_embedding_provider: Factory (auto-detect or explicit)

Validation:
- EmbeddingGuard: Vector anomaly detection (NaN, zero, dimension mismatch)
"""

from .onnx_provider import OnnxBgeEmbeddingProvider
from .ollama_provider import OllamaEmbeddingProvider
from .tei_provider import TEIEmbeddingProvider
from .dual_provider import DualEmbeddingProvider, DualEmbedding
from .provider_factory import create_embedding_provider
from .embedding_guard import (
    validate_vector,
    safe_embedding_or_zero,
    sparse_token_hash,
    VectorVerdict,
    VectorCheckResult,
    EXPECTED_DIMENSION,
)
from .types import EmbeddingProvider

__all__ = [
    # Protocol
    "EmbeddingProvider",
    # Providers
    "OnnxBgeEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "TEIEmbeddingProvider",
    # Dual
    "DualEmbeddingProvider",
    "DualEmbedding",
    # Factory
    "create_embedding_provider",
    # Guard
    "validate_vector",
    "safe_embedding_or_zero",
    "VectorVerdict",
    "VectorCheckResult",
    "EXPECTED_DIMENSION",
    "sparse_token_hash",
]
