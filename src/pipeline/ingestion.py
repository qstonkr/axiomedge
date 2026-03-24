"""Simplified ingestion pipeline coordinator.

Extracted from oreo-ecosystem IngestionCoordinator. Simplified by removing:
- Security gates
- Deduplication pipeline
- Temporal integration
- Feature flags
- StatsD metrics
- Owner/term extraction

Core data flow: parse -> quality_check -> chunk -> add_doc_context_prefix -> embed (dense+sparse) -> store_qdrant -> graphrag_extract -> store_graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from src.config_weights import weights
from ..domain.models import IngestionResult, RawDocument
from .chunker import Chunker, ChunkStrategy
from .document_parser import parse_bytes
from .graphrag_extractor import GraphRAGExtractor
from .qdrant_utils import str_to_uuid, truncate_content, MAX_PAYLOAD_CONTENT_LENGTH
from .quality_processor import (
    QualityTier,
    _calculate_metrics,
    _determine_quality_tier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pluggable interfaces
# ---------------------------------------------------------------------------


class IEmbedder(Protocol):
    """Embedding provider interface."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning dense vectors."""
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
        *,
        doc_id: str,
        title: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        """Upsert document node and chunk relationships."""
        ...


# ---------------------------------------------------------------------------
# No-op defaults
# ---------------------------------------------------------------------------


class NoOpEmbedder:
    """Returns zero vectors. Replace with a real embedder for production."""

    def __init__(self, dimension: int = 1024) -> None:
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

    async def upsert_document(
        self,
        *,
        doc_id: str,
        title: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        logger.debug("NoOpGraphStore: skipping doc %s", doc_id)


# ---------------------------------------------------------------------------
# Document context prefix
# ---------------------------------------------------------------------------


def _build_document_context_prefix(raw: RawDocument) -> str:
    """Build a document context prefix string for chunk text.

    Prepends source metadata to each chunk so the embedding captures
    document-level context (title, source, author).
    """
    parts: list[str] = []
    if raw.title:
        parts.append(f"[문서: {raw.title}]")
    if raw.source_uri:
        parts.append(f"[출처: {raw.source_uri}]")
    if raw.author:
        parts.append(f"[작성자: {raw.author}]")
    if not parts:
        return ""
    return " ".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class IngestionPipeline:
    """Ingestion pipeline: parse -> quality -> chunk -> prefix -> embed(dense+sparse) -> store -> graphrag.

    Usage:
        pipeline = IngestionPipeline(
            embedder=my_embedder,
            sparse_embedder=my_bm25,
            vector_store=my_qdrant,
            graph_store=my_neo4j,
            graphrag_extractor=my_extractor,
        )
        result = await pipeline.ingest(raw_document, collection_name="my-kb")
    """

    def __init__(
        self,
        *,
        embedder: IEmbedder | None = None,
        sparse_embedder: ISparseEmbedder | None = None,
        vector_store: IVectorStore | None = None,
        graph_store: IGraphStore | None = None,
        chunker: Chunker | None = None,
        graphrag_extractor: GraphRAGExtractor | None = None,
        enable_quality_filter: bool = True,
        enable_graphrag: bool = False,
        min_quality_tier: QualityTier = QualityTier.BRONZE,
    ) -> None:
        self.embedder = embedder or NoOpEmbedder()
        self.sparse_embedder = sparse_embedder or NoOpSparseEmbedder()
        self.vector_store = vector_store or NoOpVectorStore()
        self.graph_store = graph_store or NoOpGraphStore()
        self.chunker = chunker or Chunker(
            max_chunk_chars=weights.chunking.max_chunk_chars,
            overlap_sentences=weights.chunking.overlap_sentences,
            strategy=ChunkStrategy.SEMANTIC,
        )
        self.graphrag_extractor = graphrag_extractor
        self.enable_quality_filter = enable_quality_filter
        self.enable_graphrag = enable_graphrag
        self.min_quality_tier = min_quality_tier

    async def ingest(
        self,
        raw: RawDocument,
        collection_name: str,
    ) -> IngestionResult:
        """Execute the ingestion pipeline for a single document.

        Steps:
            1. Quality check (optional): classify content quality tier.
            2. Chunk the document content.
            3. Add document context prefix to each chunk.
            4. Embed all chunks (dense + sparse).
            5. Generate deterministic point IDs using str_to_uuid.
            6. Upsert to vector store (Qdrant).
            7. Upsert to graph store (Neo4j) if available.
            8. GraphRAG extraction (optional): extract entities/relationships.
        """
        try:
            # 1. Quality check
            quality_tier = QualityTier.BRONZE
            if self.enable_quality_filter:
                metrics = _calculate_metrics(raw.content)
                quality_tier = _determine_quality_tier(metrics)

                # Filter out documents below minimum quality tier
                tier_order = [QualityTier.NOISE, QualityTier.BRONZE, QualityTier.SILVER, QualityTier.GOLD]
                if tier_order.index(quality_tier) < tier_order.index(self.min_quality_tier):
                    return IngestionResult.failure_result(
                        reason=f"Document quality {quality_tier.value} below minimum {self.min_quality_tier.value}",
                        stage="quality_check",
                    )

            # 2. Chunk
            chunk_result = self.chunker.chunk(raw.content)
            if not chunk_result.chunks:
                return IngestionResult.failure_result(
                    reason="No chunks produced from document content",
                    stage="chunk",
                )

            # 3. Add document context prefix to each chunk
            doc_prefix = _build_document_context_prefix(raw)
            prefixed_chunks = [
                f"{doc_prefix}{chunk}" if doc_prefix else chunk
                for chunk in chunk_result.chunks
            ]

            # 4. Embed (dense + sparse)
            dense_vectors = await self.embedder.embed_documents(prefixed_chunks)
            sparse_vectors = await self.sparse_embedder.embed_sparse(prefixed_chunks)

            if len(dense_vectors) != len(prefixed_chunks):
                raise ValueError(
                    f"Embedding count mismatch: {len(prefixed_chunks)} chunks but {len(dense_vectors)} vectors"
                )

            # 5. Build items with deterministic UUIDs and both vector types
            now_iso = datetime.now(UTC).isoformat()
            items: list[dict[str, Any]] = []
            for idx, (chunk_text, dense_vec, sparse_vec) in enumerate(
                zip(prefixed_chunks, dense_vectors, sparse_vectors)
            ):
                # Deterministic point ID using str_to_uuid
                point_id_str = f"{collection_name}:{raw.doc_id}:{idx}"
                point_uuid = str_to_uuid(point_id_str)

                chunk_metadata = dict(raw.metadata)
                chunk_metadata.update({
                    "doc_id": raw.doc_id,
                    "document_name": raw.title,
                    "source_uri": raw.source_uri,
                    "author": raw.author,
                    "chunk_index": idx,
                    "ingested_at": now_iso,
                    "quality_tier": quality_tier.value,
                    "original_id": point_id_str,
                    "kb_id": collection_name,
                })
                if raw.updated_at:
                    chunk_metadata["last_modified"] = raw.updated_at.isoformat()

                # Convert sparse vector from {"indices": [...], "values": [...]}
                # to dict[int, float] as expected by QdrantStoreOperations
                if isinstance(sparse_vec, dict) and "indices" in sparse_vec:
                    sparse_converted = dict(zip(sparse_vec["indices"], sparse_vec["values"]))
                else:
                    sparse_converted = sparse_vec

                # Truncate content for payload safety
                safe_content = truncate_content(chunk_text)

                items.append({
                    "content": safe_content,
                    "dense_vector": dense_vec,
                    "sparse_vector": sparse_converted,
                    "metadata": chunk_metadata,
                    "point_id": point_uuid,
                })

            # 6. Store in vector DB
            await self.vector_store.upsert_batch(
                collection_name,
                items,
            )

            # 7. Store in graph DB
            graph_chunks = [
                {
                    "chunk_index": idx,
                    "content": chunk_text,
                    "char_count": len(chunk_text),
                }
                for idx, chunk_text in enumerate(chunk_result.chunks)
            ]
            await self.graph_store.upsert_document(
                doc_id=raw.doc_id,
                title=raw.title,
                chunks=graph_chunks,
                metadata=dict(raw.metadata),
            )

            # 8. GraphRAG extraction (optional)
            graphrag_stats: dict[str, Any] = {}
            if self.enable_graphrag and self.graphrag_extractor is not None:
                try:
                    extraction_result = self.graphrag_extractor.extract(
                        document=raw.content,
                        source_title=raw.title,
                        source_page_id=raw.doc_id,
                        source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
                    )
                    if extraction_result.node_count > 0 or extraction_result.relationship_count > 0:
                        save_stats = self.graphrag_extractor.save_to_neo4j(extraction_result)
                        graphrag_stats = {
                            "nodes_extracted": extraction_result.node_count,
                            "relationships_extracted": extraction_result.relationship_count,
                            **save_stats,
                        }
                        logger.info(
                            "GraphRAG extraction completed for doc_id=%s: %d nodes, %d rels",
                            raw.doc_id,
                            extraction_result.node_count,
                            extraction_result.relationship_count,
                        )
                except Exception as e:
                    logger.warning("GraphRAG extraction failed for doc_id=%s: %s", raw.doc_id, e)
                    graphrag_stats = {"error": str(e)}

            result_metadata: dict[str, Any] = {
                "collection_name": collection_name,
                "chunk_strategy": self.chunker._strategy.value,
                "total_chunks": chunk_result.total_chunks,
                "quality_tier": quality_tier.value,
                "has_sparse_vectors": True,
                "has_document_prefix": bool(doc_prefix),
                "deterministic_uuids": True,
            }
            if graphrag_stats:
                result_metadata["graphrag"] = graphrag_stats

            return IngestionResult.success_result(
                chunks_stored=len(items),
                metadata=result_metadata,
            )

        except Exception as exc:
            logger.exception("Ingestion pipeline failed for doc_id=%s", raw.doc_id)
            return IngestionResult.failure_result(
                reason=str(exc),
                stage="pipeline",
            )


__all__ = [
    "IEmbedder",
    "IGraphStore",
    "ISparseEmbedder",
    "IVectorStore",
    "IngestionPipeline",
    "NoOpEmbedder",
    "NoOpGraphStore",
    "NoOpSparseEmbedder",
    "NoOpVectorStore",
]
