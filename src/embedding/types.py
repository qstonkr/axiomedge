"""Embedding provider Protocols — structural interfaces for all providers.

Two protocols for different usage patterns:

- SyncEmbeddingEncoder: CPU-bound batch encoding (dense+sparse+colbert).
  Called via asyncio.to_thread() from async context.
  Used by: search routes (encode query), dense_term_index (batch encode terms).

- AsyncEmbeddingProvider: Async single/batch dense embedding.
  Used by: L2 cache (embed query for similarity), dedup (semantic hash).

All providers (Ollama, TEI, ONNX) satisfy BOTH protocols.

Usage:
    # Sync encoding (wrap in to_thread for async context)
    output = embedder.encode(["query"], return_dense=True, return_sparse=True)
    dense = output["dense_vecs"][0]

    # Async single embedding
    vector = await embedder.embed("query text")
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SyncEmbeddingEncoder(Protocol):
    """Sync batch encoder for dense/sparse/colbert vectors.

    CPU-bound — callers must use asyncio.to_thread() in async context.
    """

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

    @property
    def dimension(self) -> int:
        """Embedding vector dimension (e.g. 1024 for BGE-M3)."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class AsyncEmbeddingProvider(Protocol):
    """Async embedding for single/batch dense vectors.

    Used by cache layers and dedup for similarity computation.
    """

    async def embed(self, text: str) -> list[float]:
        """Single text dense embedding (async)."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch dense embedding (async)."""
        ...


@runtime_checkable
class EmbeddingProvider(SyncEmbeddingEncoder, AsyncEmbeddingProvider, Protocol):
    """Full embedding provider — satisfies both sync and async interfaces.

    This is the unified Protocol that all providers implement.
    Use the narrower protocols (SyncEmbeddingEncoder, AsyncEmbeddingProvider)
    when only one capability is needed, to make intent explicit.
    """

    ...
