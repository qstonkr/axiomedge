# pyright: reportAttributeAccessIssue=false
"""Ingestion pipeline coordinator with enhanced search accuracy.

Orchestrator facade -- delegates to sub-modules:
- ingestion_chunks.py: chunk building, OCR splitting, morpheme extraction
- ingestion_store.py: chunk/title item building, result metadata
- ingestion_graph.py: graph edges, GraphRAG, tree/summary builders
- ingestion_extras.py: dedup, quality check, ingestion gate, term extraction

Core data flow: parse -> quality_check -> owner_extract -> category_assign
-> quality_score -> chunk -> add_doc_context_prefix -> embed (dense+sparse)
-> store_qdrant -> graphrag_extract -> store_graph -> graph_edges.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config.weights import weights
from src.core.models import IngestionResult, RawDocument
from .chunker import Chunker, ChunkStrategy
from .document_parser import ParseResult, parse_bytes_enhanced  # noqa: F401
from .graphrag_extractor import GraphRAGExtractor
from .quality_processor import QualityTier

# Sub-module imports (implementation)
from src.pipelines.ingestion_chunks import (
    build_typed_chunks,
    extract_morphemes,
    append_date_author_tokens,
    add_context_prefixes,
)
from src.pipelines.ingestion_store import (
    ChunkContext as _ChunkContext,
    build_chunk_item,
    build_title_item,
    build_result_metadata,
)
from src.pipelines.ingestion_graph import (
    create_graph_edges,
    run_graphrag,
    run_tree_index_builder,
    run_summary_tree_builder,
)
from src.pipelines.ingestion_extras import (
    check_dedup,
    check_ingestion_gate,
    check_quality,
    run_term_extraction,
    run_synonym_discovery,
)

# Re-export from extracted modules for backward compatibility
from src.pipelines.ingestion_contracts import (  # noqa: F401
    IEmbedder,
    ISparseEmbedder,
    IVectorStore,
    IGraphStore,
    NoOpEmbedder,
    NoOpSparseEmbedder,
    NoOpVectorStore,
    NoOpGraphStore,
)
from src.pipelines.ingestion_helpers import (  # noqa: F401
    extract_owner,
    load_l1_categories_from_db,
    classify_l1_category,
    calculate_quality_score,
    classify_document_type,
    extract_cross_references as _extract_cross_references,
    _BINARY_EXTENSIONS,
)
from src.pipelines.ingestion_text import (  # noqa: F401
    extract_document_summary as _extract_document_summary,
    clean_text_for_embedding as _clean_text_for_embedding,
    clean_passage as _clean_passage,
    build_document_context_prefix as _build_document_context_prefix,
)
# Re-export quality_processor symbols used by external scripts
from src.pipelines.quality_processor import (  # noqa: F401
    _calculate_metrics,
    _determine_quality_tier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionFeatureFlags:
    """Feature toggles for the ingestion pipeline."""

    enable_quality_filter: bool = True
    enable_graphrag: bool = False
    enable_term_extraction: bool = False
    min_quality_tier: QualityTier = QualityTier.BRONZE
    enable_ingestion_gate: bool = False


class IngestionPipeline:
    """Ingestion pipeline with enhanced search accuracy.

    Usage:
        pipeline = IngestionPipeline(
            embedder=my_embedder,
            sparse_embedder=my_bm25,
            vector_store=my_qdrant,
            graph_store=my_neo4j,
        )
        result = await pipeline.ingest(raw_doc, collection_name="my-kb")
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
        legal_graph_extractor: Any | None = None,
        term_extractor: Any | None = None,
        dedup_cache: Any | None = None,
        dedup_pipeline: Any | None = None,
        flags: IngestionFeatureFlags | None = None,
        **flag_kwargs: Any,
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
        self.legal_graph_extractor = legal_graph_extractor
        self.term_extractor = term_extractor
        self.dedup_cache = dedup_cache
        self.dedup_pipeline = dedup_pipeline

        if flags is not None:
            _f = flags
        elif flag_kwargs:
            _f = IngestionFeatureFlags(**{
                k: v for k, v in flag_kwargs.items()
                if k in IngestionFeatureFlags.__dataclass_fields__
            })
        else:
            _f = IngestionFeatureFlags()
        self.enable_quality_filter = _f.enable_quality_filter
        self.enable_graphrag = _f.enable_graphrag
        self.enable_term_extraction = _f.enable_term_extraction
        self.min_quality_tier = _f.min_quality_tier

        self._ingestion_gate = None
        if _f.enable_ingestion_gate:
            try:
                from .ingestion_gate import IngestionGate
                self._ingestion_gate = IngestionGate(enabled=True)
                logger.info("Ingestion gate enabled")
            except (  # noqa: BLE001
                RuntimeError, OSError, ValueError, TypeError,
                KeyError, AttributeError, ImportError,
            ) as e:
                logger.warning("Ingestion gate init failed: %s", e)

    _EMBED_MAX_RETRIES = 3
    _EMBED_RETRY_DELAY = 5

    async def _embed_dense(
        self, texts: list[str],
    ) -> list[list[float]]:
        """Embed texts with retry on timeout/connection errors."""
        max_retries = max(self._EMBED_MAX_RETRIES, 1)
        for attempt in range(1, max_retries + 1):
            try:
                encode_fn = getattr(self.embedder, "encode", None)
                if encode_fn is not None:
                    result = await asyncio.to_thread(
                        lambda: encode_fn(texts, return_dense=True)
                    )
                    return result["dense_vecs"]
                return await self.embedder.embed_documents(texts)
            except Exception as e:  # noqa: BLE001
                if attempt < self._EMBED_MAX_RETRIES:
                    logger.warning(
                        "Embed attempt %d/%d failed: %s",
                        attempt, self._EMBED_MAX_RETRIES, e,
                    )
                    await asyncio.sleep(self._EMBED_RETRY_DELAY)
                else:
                    raise
        raise RuntimeError("unreachable: loop exits via return or raise")  # for type checker

    async def _embed_sparse_with_retry(
        self, texts: list[str],
    ) -> list[dict[str, list]]:
        """Embed sparse vectors with retry logic."""
        for attempt in range(1, self._EMBED_MAX_RETRIES + 1):
            try:
                return await self.sparse_embedder.embed_sparse(texts)
            except Exception as e:  # noqa: BLE001
                if attempt < self._EMBED_MAX_RETRIES:
                    logger.warning(
                        "Sparse embed attempt %d/%d failed: %s",
                        attempt, self._EMBED_MAX_RETRIES, e,
                    )
                    await asyncio.sleep(self._EMBED_RETRY_DELAY)
                else:
                    raise
        raise RuntimeError("unreachable: loop exits via return or raise")  # for type checker

    # -- Backward-compat delegation methods --

    @staticmethod
    def _try_binary_parse(raw: RawDocument) -> ParseResult | None:
        """Attempt enhanced binary parsing for known extensions."""
        filename = raw.metadata.get("filename", "")
        filename_lower = filename.lower() if filename else ""
        if not filename_lower or not any(
            filename_lower.endswith(ext)
            for ext in _BINARY_EXTENSIONS
        ):
            return None
        try:
            file_bytes = raw.metadata.get("file_bytes")
            if isinstance(file_bytes, bytes):
                return parse_bytes_enhanced(file_bytes, filename)
        except (  # noqa: BLE001
            RuntimeError, OSError, ValueError, TypeError,
            KeyError, AttributeError, ImportError,
        ) as e:
            logger.warning(
                "Enhanced parsing failed for doc_id=%s: %s",
                raw.doc_id, e,
            )
        return None

    @staticmethod
    def _build_body_chunks(
        chunk_result: Any, heading_map: dict[int, str],
    ) -> list[tuple[str, str, str]]:
        from src.pipelines.ingestion_chunks import build_body_chunks
        return build_body_chunks(chunk_result, heading_map)

    @staticmethod
    def _append_table_chunks(
        typed_chunks: list[tuple[str, str, str]],
        parse_result: ParseResult | None,
    ) -> None:
        from src.pipelines.ingestion_chunks import append_table_chunks
        append_table_chunks(typed_chunks, parse_result)

    @staticmethod
    def _extract_morphemes(
        typed_chunks: list[tuple[str, str]],
    ) -> list[str]:
        return extract_morphemes(typed_chunks)

    @staticmethod
    def _append_date_author_tokens(
        chunk_morphemes: list[str], title: str, author: str,
    ) -> list[str]:
        return append_date_author_tokens(chunk_morphemes, title, author)

    @staticmethod
    def _add_context_prefixes(
        raw: RawDocument,
        typed_chunks: list[tuple[str, str]],
        doc_summary: str,
    ) -> tuple[list[str], list[str], list[str]]:
        return add_context_prefixes(raw, typed_chunks, doc_summary)

    @staticmethod
    def _build_result_metadata(
        ctx: _ChunkContext,
        chunker_strategy: str,
        items: list[dict[str, Any]],
        heading_map: dict[int, str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return build_result_metadata(
            ctx, chunker_strategy, items, heading_map, **kwargs,
        )

    def _build_chunk_item(
        self,
        idx: int,
        chunk_text: str,
        dense_vec: list[float],
        sparse_vec: dict[str, list],
        *,
        ctx: _ChunkContext,
    ) -> dict[str, Any]:
        return build_chunk_item(
            idx, chunk_text, dense_vec, sparse_vec, ctx=ctx,
        )

    async def _build_title_item(
        self,
        raw: RawDocument,
        collection_name: str,
        now_iso: str,
        quality_tier: QualityTier,
        quality_score: float,
        doc_type: str,
        owner: str,
        l1_category: str,
        content_flags: dict[str, bool],
        parse_result: ParseResult | None,
    ) -> dict[str, Any] | None:
        return await build_title_item(
            raw, collection_name, now_iso, quality_tier,
            quality_score, doc_type, owner, l1_category,
            content_flags, parse_result,
            self._embed_dense, self.sparse_embedder,
        )

    async def _build_typed_chunks(
        self,
        raw: RawDocument,
        parse_result: ParseResult | None,
    ) -> (
        tuple[list[tuple[str, str, str]], dict[int, str], str]
        | IngestionResult
    ):
        return await build_typed_chunks(
            raw, parse_result, self.chunker,
        )

    async def _split_ocr_text(
        self, ocr_text: str,
    ) -> list[tuple[str, str, str]]:
        from src.pipelines.ingestion_chunks import split_ocr_text
        return await split_ocr_text(ocr_text, self.chunker)

    def _check_ingestion_gate(
        self,
        raw: RawDocument,
        collection_name: str,
    ) -> IngestionResult | None:
        return check_ingestion_gate(
            raw, collection_name, self._ingestion_gate,
        )

    def _check_quality(
        self, raw: RawDocument,
    ) -> tuple[QualityTier, Any, IngestionResult | None]:
        return check_quality(
            raw, self.enable_quality_filter, self.min_quality_tier,
        )

    async def _check_dedup(
        self,
        raw: RawDocument,
        collection_name: str,
        content_hash: str,
    ) -> tuple[IngestionResult | None, dict[str, Any] | None]:
        return await check_dedup(
            raw, collection_name, content_hash,
            self.dedup_pipeline, self.dedup_cache,
        )

    async def _run_term_extraction(
        self,
        raw: RawDocument,
        typed_chunks: list[tuple[str, str]],
        collection_name: str,
    ) -> dict[str, Any] | None:
        return await run_term_extraction(
            raw, typed_chunks, collection_name,
            self.enable_term_extraction, self.term_extractor,
        )

    async def _run_synonym_discovery(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any] | None:
        return await run_synonym_discovery(
            raw, collection_name,
            self.enable_term_extraction, self.term_extractor,
        )

    async def _run_graphrag(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any] | None:
        return await run_graphrag(
            raw, collection_name, self.enable_graphrag,
            self.graphrag_extractor, self.legal_graph_extractor,
        )

    async def _run_legal_graph_extraction(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any] | None:
        from src.pipelines.ingestion_graph import (
            _run_legal_graph_extraction as _impl,
        )
        return await _impl(
            raw, collection_name, self.legal_graph_extractor,
        )

    async def _run_tree_index_builder(
        self,
        raw: RawDocument,
        items: list[dict[str, Any]],
        chunk_heading_paths: list[str],
        collection_name: str,
    ) -> None:
        return await run_tree_index_builder(
            raw, items, chunk_heading_paths,
            collection_name, self.graph_store,
        )

    async def _run_summary_tree_builder(
        self,
        raw: RawDocument,
        items: list[dict[str, Any]],
        dense_vectors: list[list[float]],
        prefixed_chunks: list[str],
        collection_name: str,
        chunk_heading_paths: list[str],
        now_iso: str,
    ) -> None:
        return await run_summary_tree_builder(
            raw, items, dense_vectors, prefixed_chunks,
            collection_name, chunk_heading_paths, now_iso,
            self.vector_store,
            getattr(self, 'embedding_provider', None),
            getattr(self, 'llm_client', None),
        )

    async def _create_graph_edges(
        self,
        raw: RawDocument,
        _collection_name: str,
        *,
        owner: str = "",
        l1_category: str = "",
    ) -> None:
        return await create_graph_edges(
            raw, _collection_name, self.graph_store,
            _extract_cross_references,
            owner=owner, l1_category=l1_category,
        )

    # -- Main pipeline --

    async def ingest(
        self,
        raw: RawDocument,
        collection_name: str,
        parse_result: ParseResult | None = None,
    ) -> IngestionResult:
        """Execute the ingestion pipeline for a single document."""
        try:
            # Stage 0: Ingestion gate
            gate_failure = check_ingestion_gate(
                raw, collection_name, self._ingestion_gate,
            )
            if gate_failure is not None:
                return gate_failure

            import hashlib as _hashlib
            _content_hash = _hashlib.sha256(
                raw.content.lower().strip().encode(),
            ).hexdigest()[:32]

            # 0. Dedup check
            dedup_failure, dedup_result_info = await check_dedup(
                raw, collection_name, _content_hash,
                self.dedup_pipeline, self.dedup_cache,
            )
            if dedup_failure is not None:
                return dedup_failure

            # 1. Quality check
            quality_tier, quality_metrics, quality_failure = (
                check_quality(
                    raw, self.enable_quality_filter,
                    self.min_quality_tier,
                )
            )
            if quality_failure is not None:
                return quality_failure

            doc_type = classify_document_type(raw.title, raw.content)
            owner = extract_owner(raw)
            l1_category = classify_l1_category(
                raw.title, raw.content,
            )
            quality_score = calculate_quality_score(
                quality_metrics, quality_tier,
            )

            # 2-4. Parse, chunk, clean
            chunk_result = await build_typed_chunks(
                raw, parse_result, self.chunker,
            )
            if isinstance(chunk_result, IngestionResult):
                return chunk_result
            typed_chunks, heading_map, doc_summary = chunk_result

            # 5. Add document context prefix
            prefixed, chunk_types, chunk_heading_paths = (
                add_context_prefixes(raw, typed_chunks, doc_summary)
            )

            # 6. Embed (dense + sparse)
            dense_vectors, sparse_vectors = await asyncio.gather(
                self._embed_dense(prefixed),
                self._embed_sparse_with_retry(prefixed),
            )
            if len(dense_vectors) != len(prefixed):
                raise ValueError(
                    f"Embedding count mismatch: "
                    f"{len(prefixed)} chunks but "
                    f"{len(dense_vectors)} vectors"
                )

            # META-09: Morphemes
            chunk_morphemes = extract_morphemes(typed_chunks)
            chunk_morphemes = append_date_author_tokens(
                chunk_morphemes, raw.title, raw.author,
            )

            content_flags: dict[str, bool] = {}
            if quality_metrics:
                content_flags = {
                    "has_tables": quality_metrics.has_tables,
                    "has_code": quality_metrics.has_code_blocks,
                    "has_images": quality_metrics.has_images,
                }

            # 7-8. Build items
            now_iso = datetime.now(UTC).isoformat()
            ctx = _ChunkContext(
                raw=raw,
                collection_name=collection_name,
                chunk_types=chunk_types,
                chunk_heading_paths=chunk_heading_paths,
                chunk_morphemes=chunk_morphemes,
                now_iso=now_iso,
                quality_tier=quality_tier,
                quality_score=quality_score,
                doc_type=doc_type,
                owner=owner,
                l1_category=l1_category,
                content_flags=content_flags,
                parse_result=parse_result,
            )
            items = [
                build_chunk_item(idx, ct, dv, sv, ctx=ctx)
                for idx, (ct, dv, sv) in enumerate(
                    zip(prefixed, dense_vectors, sparse_vectors),
                )
            ]

            title_item = await build_title_item(
                raw, collection_name, now_iso, quality_tier,
                quality_score, doc_type, owner, l1_category,
                content_flags, parse_result,
                self._embed_dense, self.sparse_embedder,
            )
            if title_item is not None:
                items.append(title_item)

            # 9-10. Store
            await asyncio.gather(
                self.vector_store.upsert_batch(
                    collection_name, items,
                ),
                self.graph_store.upsert_document(
                    doc_id=raw.doc_id,
                    title=raw.title,
                    kb_id=collection_name,
                    source_type=raw.metadata.get(
                        "source_type", "file",
                    ),
                ),
            )

            # 11. Graph edges
            await create_graph_edges(
                raw, collection_name, self.graph_store,
                _extract_cross_references,
                owner=owner, l1_category=l1_category,
            )

            # 12. Tree index
            await run_tree_index_builder(
                raw, items, chunk_heading_paths,
                collection_name, self.graph_store,
            )

            # 13. Summary tree
            await run_summary_tree_builder(
                raw, items, dense_vectors, prefixed,
                collection_name, chunk_heading_paths,
                now_iso, self.vector_store,
                getattr(self, 'embedding_provider', None),
                getattr(self, 'llm_client', None),
            )

            # 14. Term extraction + synonym discovery + GraphRAG
            term_stats = await run_term_extraction(
                raw, typed_chunks, collection_name,
                self.enable_term_extraction,
                self.term_extractor,
            )
            synonym_stats = await run_synonym_discovery(
                raw, collection_name,
                self.enable_term_extraction,
                self.term_extractor,
            )
            graphrag_stats = await run_graphrag(
                raw, collection_name, self.enable_graphrag,
                self.graphrag_extractor,
                self.legal_graph_extractor,
            )

            # Register dedup hash
            if self.dedup_cache:
                try:
                    await self.dedup_cache.add(
                        collection_name, _content_hash,
                    )
                except (  # noqa: BLE001
                    RuntimeError, OSError, ValueError,
                    TypeError, KeyError, AttributeError,
                    ImportError,
                ) as err:
                    logger.warning(
                        "Dedup cache registration failed: %s",
                        err,
                    )

            # Build result metadata
            result_metadata = build_result_metadata(
                ctx, self.chunker.strategy_name, items,
                heading_map,
                graphrag_stats=graphrag_stats,
                term_extraction_stats=term_stats,
                synonym_discovery_stats=synonym_stats,
                dedup_result_info=dedup_result_info,
            )

            return IngestionResult.success_result(
                chunks_stored=len(items),
                metadata=result_metadata,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Ingestion pipeline failed for doc_id=%s",
                raw.doc_id,
            )
            return IngestionResult.failure_result(
                reason=str(exc), stage="pipeline",
            )


__all__ = [
    "IEmbedder",
    "IGraphStore",
    "ISparseEmbedder",
    "IVectorStore",
    "IngestionFeatureFlags",
    "IngestionPipeline",
    "NoOpEmbedder",
    "NoOpGraphStore",
    "NoOpSparseEmbedder",
    "NoOpVectorStore",
    "_ChunkContext",
    "classify_document_type",
    "extract_owner",
    "classify_l1_category",
    "calculate_quality_score",
    "load_l1_categories_from_db",
    "_calculate_metrics",
    "_determine_quality_tier",
]
