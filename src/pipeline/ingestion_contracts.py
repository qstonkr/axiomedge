"""Ingestion pipeline contracts — Protocol interfaces and NoOp implementations.

Used by IngestionPipeline for dependency injection of embedder, vector store, and graph store.
"""

from __future__ import annotations

import asyncio
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
    """Vector store interface (e.g. Qdrant).

    Ingestion path: ``upsert_batch``
    CRUD: ``delete_by_filter``, ``delete_points``, ``count``
    Search: ``fetch_by_ids``
    """

    async def upsert_batch(
        self,
        kb_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Upsert a batch of items (content, vector, metadata)."""
        ...

    async def delete_by_filter(
        self,
        kb_id: str,
        filter_conditions: dict[str, Any],
    ) -> bool:
        """Delete points matching filter conditions."""
        ...  # pragma: no cover

    async def delete_points(
        self,
        kb_id: str,
        point_ids: list[str],
    ) -> bool:
        """Delete specific points by ID."""
        ...  # pragma: no cover

    async def count(self, kb_id: str) -> int:
        """Count total points in a KB."""
        ...  # pragma: no cover

    async def fetch_by_ids(
        self,
        kb_id: str,
        point_ids: list[str],
    ) -> list[Any]:
        """Fetch points by their IDs."""
        ...  # pragma: no cover


class ISearchEngine(Protocol):
    """Vector search engine interface (e.g. Qdrant hybrid search)."""

    async def search(
        self,
        kb_id: str,
        dense_query: list[float],
        filter_conditions: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[Any]:
        """Execute dense vector search."""
        ...  # pragma: no cover


class IGraphStore(Protocol):
    """Graph store interface (e.g. Neo4j).

    Ingestion path: ``upsert_document``, ``execute_write``
    Entity CRUD: ``upsert_entity``, ``create_relationship``, ``batch_upsert_nodes``
    Search: ``find_related_chunks``, ``search_entities``
    Stats: ``get_stats``
    """

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

    async def upsert_entity(
        self,
        entity_type: str,
        entity_id: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Upsert an entity node. Returns stats dict."""
        ...  # pragma: no cover

    async def create_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Create a relationship between two entities."""
        ...  # pragma: no cover

    async def find_related_chunks(
        self,
        entity_names: list[str],
        kb_id: str | None = None,
        limit: int = 50,
    ) -> set[str]:
        """Find chunk IDs related to given entity names via graph traversal."""
        ...  # pragma: no cover

    async def get_stats(self) -> dict[str, Any]:
        """Return graph store statistics (entity count, doc count, etc.)."""
        ...  # pragma: no cover


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
        await asyncio.sleep(0)
        return [[0.0] * self._dimension for _ in texts]


class NoOpSparseEmbedder:
    """Returns empty sparse vectors. Replace with BM25/SPLADE for production."""

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return [{"indices": [], "values": []} for _ in texts]


class NoOpVectorStore:
    """Drops all operations. Replace with a Qdrant adapter for production."""

    async def upsert_batch(self, kb_id: str, items: list[dict[str, Any]]) -> None:
        await asyncio.sleep(0)
        logger.debug("NoOpVectorStore: skipping %d items for %s", len(items), kb_id)

    async def delete_by_filter(self, kb_id: str, filter_conditions: dict[str, Any]) -> bool:
        return True

    async def delete_points(self, kb_id: str, point_ids: list[str]) -> bool:
        return True

    async def count(self, kb_id: str) -> int:
        return 0

    async def fetch_by_ids(self, kb_id: str, point_ids: list[str]) -> list[Any]:
        return []


class NoOpGraphStore:
    """Drops all graph operations. Replace with a Neo4j adapter for production."""

    async def upsert_document(self, doc_id: str, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        logger.debug("NoOpGraphStore: skipping doc %s", doc_id)

    async def execute_write(self, query: str, _parameters: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        logger.debug("NoOpGraphStore: skipping write query")

    async def upsert_entity(
        self, entity_type: str, entity_id: str, name: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        return {"created": 0, "updated": 0}

    async def create_relationship(
        self, source_id: str, target_id: str, rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        return {"created": 0}

    async def find_related_chunks(
        self, entity_names: list[str], kb_id: str | None = None, limit: int = 50,
    ) -> set[str]:
        return set()

    async def get_stats(self) -> dict[str, Any]:
        return {"entities": 0, "documents": 0, "relationships": 0}
