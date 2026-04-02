"""Embedding provider Protocol — structural interface for all providers.

All embedding providers (Ollama, TEI, ONNX) satisfy this Protocol
without explicit inheritance (structural/duck typing).

Usage:
    from src.embedding.types import EmbeddingProvider

    def search(embedder: EmbeddingProvider, query: str) -> list[float]:
        return embedder.encode([query])["dense_vecs"][0]
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers (Ollama, TEI, ONNX)."""

    backend: str

    def is_ready(self) -> bool:
        """Check if the provider is available and ready."""
        ...

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Encode texts into dense/sparse/colbert vectors.

        Returns:
            dict with keys: dense_vecs, lexical_weights, colbert_vecs
        """
        ...

    async def embed(self, text: str) -> list[float]:
        """Single text dense embedding (async)."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch dense embedding (async)."""
        ...

    @property
    def dimension(self) -> int:
        """Embedding vector dimension (e.g. 1024 for BGE-M3)."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...
