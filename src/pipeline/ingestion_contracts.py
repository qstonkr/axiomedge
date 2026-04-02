"""Ingestion pipeline contracts — Protocol interfaces and NoOp implementations.

Used by IngestionPipeline for dependency injection of embedder, vector store, and graph store.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.config_weights import weights

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol interfaces
# ---------------------------------------------------------------------------


class IEmbedder(Protocol):
    """Embedding provider interface.

    Implementations should provide an ``encode`` method (matching BGE-M3 providers).
    The ``embed_documents`` fallback is kept for simple/no-op implementations.
    """

    def encode(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """Encode texts, returning at minimum ``{"dense_vecs": [[...], ...]}``.

        This is the primary interface used by real embedding providers
        (OnnxBgeEmbeddingProvider, OllamaEmbeddingProvider).
        """
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Legacy/simple fallback -- embed a batch of texts, returning dense vectors."""
        ...


class ISparseEmbedder(Protocol):
    """Sparse embedding provider interface (e.g. BM25, SPLADE)."""

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        """Embed a batch of texts, returning sparse vectors.

        Each item should be {"indices": [...], "values": [...]}.
        """
        ...


class IVectorStore(Protocol):
    """Vector store interface (e.g. Qdrant)."""

    async def upsert_batch(
        self,
        kb_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Upsert a batch of items (content, vector, metadata)."""
        ...


class IGraphStore(Protocol):
    """Graph store interface (e.g. Neo4j)."""

    async def upsert_document(
        self,
        doc_id: str,
        **kwargs: Any,
    ) -> None:
        """Upsert document node."""
        ...

    async def execute_write(
        self,
        query: str,
        parameters: dict[str, Any],
    ) -> None:
        """Execute a write Cypher query against the graph store."""
        ...


# ---------------------------------------------------------------------------
# No-op defaults
# ---------------------------------------------------------------------------


class NoOpEmbedder:
    """Returns zero vectors. Replace with a real embedder for production."""

    def __init__(self, dimension: int | None = None) -> None:
        if dimension is None:
            dimension = weights.embedding.dimension
        self._dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimension for _ in texts]


class NoOpSparseEmbedder:
    """Returns empty sparse vectors. Replace with BM25/SPLADE for production."""

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        return [{"indices": [], "values": []} for _ in texts]


class NoOpVectorStore:
    """Drops all upserts. Replace with a Qdrant adapter for production."""

    async def upsert_batch(self, kb_id: str, items: list[dict[str, Any]]) -> None:
        logger.debug("NoOpVectorStore: skipping %d items for %s", len(items), kb_id)


class NoOpGraphStore:
    """Drops all graph operations. Replace with a Neo4j adapter for production."""

    async def upsert_document(self, doc_id: str, **kwargs: Any) -> None:
        logger.debug("NoOpGraphStore: skipping doc %s", doc_id)

    async def execute_write(self, query: str, parameters: dict[str, Any]) -> None:
        logger.debug("NoOpGraphStore: skipping write query")
