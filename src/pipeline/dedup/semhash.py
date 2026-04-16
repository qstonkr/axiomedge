"""SemHash - Semantic Hash for Duplicate Detection.

Stage 3: Embedding-based semantic duplicate detection.

Features:
- Cosine similarity based
- ~50ms processing time
- 5-8% duplicate confirmation
- Threshold: 0.90

Uses the local embedder interface (IEmbedder from ingestion.py) for embedding.
Falls back to zero vectors if no embedder is available.

Adapted from oreo-ecosystem infrastructure/dedup/semhash.py.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class IEmbeddingProvider(Protocol):
    """Embedding provider interface for SemHash.

    Compatible with both:
    - Local embedders (encode() method returning {"dense_vecs": [...]})
    - Simple async embedders (embed() method)
    """

    async def embed(self, text: str) -> list[float]:
        """Embed a single text, returning a dense vector."""
        ...


class NoOpEmbeddingProvider:
    """No-op embedding provider that returns zero vectors."""

    def __init__(self, dimension: int | None = None) -> None:
        if dimension is None:
            from src.config.weights import weights
            dimension = weights.embedding.dimension
        self._dimension = dimension

    async def embed(self, _text: str) -> list[float]:
        await asyncio.sleep(0)
        return [0.0] * self._dimension


@dataclass
class DocumentEmbedding:
    """Document embedding.

    Attributes:
        doc_id: Document ID
        embedding: Embedding vector
        text_preview: Text preview (first 200 chars)
    """

    doc_id: str
    embedding: list[float]
    text_preview: str = ""


@dataclass
class SemanticMatch:
    """Semantic match result.

    Attributes:
        doc_id_1: First document ID
        doc_id_2: Second document ID
        similarity: Cosine similarity
        is_duplicate: Whether it exceeds threshold
    """

    doc_id_1: str
    doc_id_2: str
    similarity: float
    is_duplicate: bool = False


class SemHash:
    """Semantic Hash based duplicate detection.

    Uses embeddings to detect semantically similar documents.

    SSOT:
    - Threshold: 0.90 (high similarity only)
    - Processing time: ~50ms
    - Expected detection rate: 5-8%
    """

    DEFAULT_THRESHOLD = 0.90

    def __init__(
        self,
        embedding_provider: IEmbeddingProvider | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        self._embedding_provider = embedding_provider or NoOpEmbeddingProvider()
        self._threshold = threshold
        self._expected_dim: int | None = None

        # Document embedding storage
        self._documents: dict[str, DocumentEmbedding] = {}

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity (0.0-1.0)
        """
        if len(a) != len(b) or not a:
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    async def add(self, doc_id: str, text: str) -> DocumentEmbedding:
        """Add a document to the index.

        Args:
            doc_id: Document ID
            text: Document text

        Returns:
            Created document embedding
        """
        embedding = await self._embedding_provider.embed(text)
        if self._expected_dim is None:
            self._expected_dim = len(embedding)
        elif len(embedding) != self._expected_dim:
            logger.error(
                "Embedding dimension mismatch: expected %d, got %d. Skipping document.",
                self._expected_dim, len(embedding),
            )
            return DocumentEmbedding(doc_id=doc_id, embedding=[], text_preview=text[:200])

        doc_embedding = DocumentEmbedding(
            doc_id=doc_id,
            embedding=embedding,
            text_preview=text[:200],
        )

        self._documents[doc_id] = doc_embedding
        return doc_embedding

    async def add_batch(
        self, documents: list[tuple[str, str]]
    ) -> list[DocumentEmbedding]:
        """Add a batch of documents.

        Args:
            documents: List of (doc_id, text) tuples

        Returns:
            Created document embeddings
        """
        results: list[DocumentEmbedding] = []
        for doc_id, text in documents:
            result = await self.add(doc_id, text)
            results.append(result)
        return results

    async def find_similar(
        self, doc_id: str, text: str, top_k: int = 5
    ) -> list[SemanticMatch]:
        """Find similar documents.

        Args:
            doc_id: Target document ID
            text: Document text
            top_k: Maximum results to return

        Returns:
            Similar document list
        """
        query_embedding = await self._embedding_provider.embed(text)
        if self._expected_dim is not None and len(query_embedding) != self._expected_dim:
            logger.error(
                "Embedding dimension mismatch in query: %d vs %d",
                len(query_embedding), self._expected_dim,
            )
            return []

        matches: list[SemanticMatch] = []

        for other_id, other_doc in self._documents.items():
            if other_id == doc_id:
                continue

            similarity = self.cosine_similarity(query_embedding, other_doc.embedding)

            if similarity >= self._threshold * 0.9:  # Slightly lower threshold for candidates
                matches.append(
                    SemanticMatch(
                        doc_id_1=doc_id,
                        doc_id_2=other_id,
                        similarity=similarity,
                        is_duplicate=similarity >= self._threshold,
                    )
                )

        matches.sort(key=lambda m: m.similarity, reverse=True)

        return matches[:top_k]

    async def find_duplicates(self) -> list[SemanticMatch]:
        """Find all duplicate document pairs."""
        await asyncio.sleep(0)
        _MAX_PAIRWISE_DOCS = 10_000
        seen_pairs: set[tuple[str, str]] = set()
        duplicates: list[SemanticMatch] = []

        doc_ids = list(self._documents.keys())
        if len(doc_ids) > _MAX_PAIRWISE_DOCS:
            logger.warning(
                "find_duplicates: %d docs exceeds max %d, truncating",
                len(doc_ids), _MAX_PAIRWISE_DOCS,
            )
            doc_ids = doc_ids[:_MAX_PAIRWISE_DOCS]

        for i, doc_id_1 in enumerate(doc_ids):
            for doc_id_2 in doc_ids[i + 1 :]:
                pair_key = tuple(sorted([doc_id_1, doc_id_2]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                doc1 = self._documents[doc_id_1]
                doc2 = self._documents[doc_id_2]

                similarity = self.cosine_similarity(doc1.embedding, doc2.embedding)

                if similarity >= self._threshold:
                    duplicates.append(
                        SemanticMatch(
                            doc_id_1=doc_id_1,
                            doc_id_2=doc_id_2,
                            similarity=similarity,
                            is_duplicate=True,
                        )
                    )

        duplicates.sort(key=lambda m: m.similarity, reverse=True)
        return duplicates

    async def check_duplicate(self, doc_id: str, text: str) -> SemanticMatch | None:
        """Check if a new document is a duplicate of existing documents.

        Args:
            doc_id: Document ID
            text: Document text

        Returns:
            Most similar duplicate match or None
        """
        matches = await self.find_similar(doc_id, text, top_k=1)

        if matches and matches[0].is_duplicate:
            return matches[0]

        return None

    @property
    def document_count(self) -> int:
        """Number of stored documents."""
        return len(self._documents)

    def get_document(self, doc_id: str) -> DocumentEmbedding | None:
        """Get a document by ID."""
        return self._documents.get(doc_id)

    def remove(self, doc_id: str) -> bool:
        """Remove a document."""
        if doc_id in self._documents:
            del self._documents[doc_id]
            return True
        return False

    def clear(self) -> int:
        """Clear all documents."""
        count = len(self._documents)
        self._documents.clear()
        return count

    def to_dict(self) -> dict[str, Any]:
        """State information."""
        return {
            "threshold": self._threshold,
            "document_count": self.document_count,
        }
