"""Ingestion pipeline coordinator with enhanced search accuracy.

Extracted from oreo-ecosystem IngestionCoordinator. Simplified by removing:
- Security gates
- Deduplication pipeline
- Temporal integration
- Feature flags
- StatsD metrics
- Owner/term extraction

Enhanced with accuracy improvements:
- VEC-01: Title-only vector per document for direct title search
- VEC-02/03: Enhanced parsing (tables, OCR, images as separate chunks)
- META-02: Labels, parent_title in embedding context prefix
- META-03: Document type classification
- META-04: Heading hierarchy path per chunk
- META-05: Content type flags (has_tables, has_code, has_images)
- GRAPH-01: Document cross-references (REFERENCES edges)
- GRAPH-02: Wiki hierarchy (CHILD_OF edges)
- GRAPH-04: Person AUTHORED Document edges
- GRAPH-06: Document BELONGS_TO Space edges

Core data flow: parse -> quality_check -> owner_extract -> category_assign -> quality_score -> chunk -> add_doc_context_prefix -> embed (dense+sparse) -> store_qdrant -> graphrag_extract -> store_graph -> graph_edges (CHILD_OF, AUTHORED, BELONGS_TO, REFERENCES, OWNS, CATEGORIZED_AS).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from src.config_weights import weights
from ..domain.models import IngestionResult, RawDocument
from .chunker import Chunker, ChunkStrategy
from .document_parser import parse_bytes_enhanced, ParseResult, _table_to_markdown
from .graphrag_extractor import GraphRAGExtractor
from .qdrant_utils import str_to_uuid, truncate_content
from .quality_processor import (
    QualityTier,
    QualityMetrics,
    _calculate_metrics,
    _determine_quality_tier,
)
# Re-export from extracted modules for backward compatibility
from src.pipeline.ingestion_contracts import (  # noqa: F401, E402
    IEmbedder, ISparseEmbedder, IVectorStore, IGraphStore,
    NoOpEmbedder, NoOpSparseEmbedder, NoOpVectorStore, NoOpGraphStore,
)
from src.pipeline.ingestion_helpers import (  # noqa: F401, E402
    extract_owner, load_l1_categories_from_db, classify_l1_category,
    calculate_quality_score, classify_document_type,
    extract_cross_references as _extract_cross_references,
    _BINARY_EXTENSIONS,
)
from src.pipeline.ingestion_text import (  # noqa: E402
    extract_document_summary as _extract_document_summary,
    clean_text_for_embedding as _clean_text_for_embedding,
    clean_passage as _clean_passage,
    build_document_context_prefix as _build_document_context_prefix,
)

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class IngestionPipeline:
    """Ingestion pipeline with enhanced search accuracy.

    Produces:
    - Title vector (1 per document, chunk_type="title") [VEC-01]
    - Body chunks (N per document, chunk_type="body", with heading_path) [META-04]
    - Table chunks (M per document, chunk_type="table") [VEC-02]
    - OCR chunks (K per document, chunk_type="ocr") [VEC-03]
    - Neo4j edges: CHILD_OF, AUTHORED, BELONGS_TO, REFERENCES [GRAPH-01/02/04/06]
    - Metadata: labels, parent_title, heading_path, doc_type, has_tables/code/images [META-02/03/04/05/08]

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
        term_extractor: Any | None = None,
        dedup_cache: Any | None = None,
        dedup_pipeline: Any | None = None,
        enable_quality_filter: bool = True,
        enable_graphrag: bool = False,
        enable_term_extraction: bool = False,
        min_quality_tier: QualityTier = QualityTier.BRONZE,
        enable_ingestion_gate: bool = False,
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
        self.term_extractor = term_extractor
        self.dedup_cache = dedup_cache
        self.dedup_pipeline = dedup_pipeline  # Full 4-stage dedup (preferred over dedup_cache)

        # Ingestion gate (Stage 0 pre-validation)
        self._ingestion_gate = None
        if enable_ingestion_gate:
            try:
                from .ingestion_gate import IngestionGate
                self._ingestion_gate = IngestionGate(enabled=True)
                logger.info("Ingestion gate enabled")
            except Exception as e:
                logger.warning("Ingestion gate init failed: %s", e)
        self.enable_quality_filter = enable_quality_filter
        self.enable_graphrag = enable_graphrag
        self.enable_term_extraction = enable_term_extraction
        self.min_quality_tier = min_quality_tier

    _EMBED_MAX_RETRIES = 3
    _EMBED_RETRY_DELAY = 5  # seconds

    async def _embed_dense(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with retry on timeout/connection errors."""
        max_retries = max(self._EMBED_MAX_RETRIES, 1)  # Ensure at least 1 attempt
        for attempt in range(1, max_retries + 1):
            try:
                encode_fn = getattr(self.embedder, "encode", None)
                if encode_fn is not None:
                    result = await asyncio.to_thread(
                        lambda: encode_fn(texts, return_dense=True)
                    )
                    return result["dense_vecs"]
                return await self.embedder.embed_documents(texts)
            except Exception as e:
                if attempt < self._EMBED_MAX_RETRIES:
                    logger.warning(
                        "Embedding attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt, self._EMBED_MAX_RETRIES, e, self._EMBED_RETRY_DELAY,
                    )
                    await asyncio.sleep(self._EMBED_RETRY_DELAY)
                else:
                    raise

    async def ingest(
        self,
        raw: RawDocument,
        collection_name: str,
        parse_result: ParseResult | None = None,
    ) -> IngestionResult:
        """Execute the ingestion pipeline for a single document.

        Steps:
            1. Quality check (optional): classify content quality tier.
            2. Enhanced parse: separate body text, tables, OCR text.
            3. Chunk body text with heading hierarchy paths.
            4. Create typed chunks: body, table, OCR.
            5. Add document context prefix to each chunk.
            6. Embed all chunks (dense + sparse).
            7. Add title-only vector (VEC-01).
            8. Generate deterministic point IDs using str_to_uuid.
            9. Upsert to vector store (Qdrant).
            10. Upsert to graph store (Neo4j) if available.
            11. Create graph edges (CHILD_OF, AUTHORED, BELONGS_TO, REFERENCES).
            12. GraphRAG extraction (optional): extract entities/relationships.
        """
        try:
            # Stage 0: Ingestion gate pre-validation
            if self._ingestion_gate is not None:
                gate_result = self._ingestion_gate.run_gates(raw, collection_name)
                if gate_result.is_blocked:
                    logger.info(
                        "Ingestion gate blocked doc_id=%s: action=%s, failed=%d",
                        raw.doc_id, gate_result.action.value, gate_result.failed_count,
                    )
                    return IngestionResult.failure_result(
                        reason=f"Ingestion gate: {gate_result.action.value} ({gate_result.failed_count} check(s) failed)",
                        stage="ingestion_gate",
                    )

            # Pre-compute content hash once for both dedup check and registration
            import hashlib as _hashlib
            _content_hash = _hashlib.sha256(raw.content.lower().strip().encode()).hexdigest()[:32]

            # 0. Dedup check (skip if force_rebuild via metadata flag)
            force_rebuild = raw.metadata.get("force_rebuild", False)
            dedup_result_info: dict[str, Any] = {}
            if not force_rebuild:
                # Prefer full 4-stage pipeline over simple cache
                if self.dedup_pipeline is not None:
                    try:
                        from src.pipeline.dedup import Document as DedupDoc, DedupStatus
                        dedup_doc = DedupDoc(
                            doc_id=raw.doc_id,
                            title=raw.title,
                            content=raw.content,
                            url=raw.source_uri,
                            updated_at=raw.updated_at,
                        )
                        dedup_result = await self.dedup_pipeline.add(dedup_doc)
                        dedup_result_info = dedup_result.to_dict()
                        if dedup_result.status == DedupStatus.EXACT_DUPLICATE:
                            logger.info(
                                "Dedup(4-stage): exact duplicate doc_id=%s dup_of=%s (%.1fms)",
                                raw.doc_id, dedup_result.duplicate_of,
                                dedup_result.processing_time_ms,
                            )
                            return IngestionResult.failure_result(
                                reason=f"Exact duplicate of {dedup_result.duplicate_of} (dedup pipeline Stage 1)",
                                stage="dedup",
                            )
                        elif dedup_result.status in (DedupStatus.NEAR_DUPLICATE, DedupStatus.SEMANTIC_DUPLICATE):
                            logger.info(
                                "Dedup(4-stage): %s doc_id=%s dup_of=%s score=%.3f (%.1fms) - proceeding",
                                dedup_result.status.value, raw.doc_id,
                                dedup_result.duplicate_of, dedup_result.similarity_score,
                                dedup_result.processing_time_ms,
                            )
                    except Exception as _dedup_err:
                        logger.warning("Dedup pipeline check failed, proceeding: %s", _dedup_err)
                elif self.dedup_cache is not None:
                    try:
                        if await self.dedup_cache.exists(collection_name, _content_hash):
                            logger.info(
                                "Dedup: skipping duplicate doc_id=%s in %s (hash=%s)",
                                raw.doc_id, collection_name, _content_hash[:12],
                            )
                            return IngestionResult.failure_result(
                                reason="Duplicate content (dedup cache hit)",
                                stage="dedup",
                            )
                    except Exception as _dedup_err:
                        logger.warning("Dedup cache check failed, proceeding: %s", _dedup_err)

            # 1. Quality check
            quality_tier = QualityTier.BRONZE
            quality_metrics: QualityMetrics | None = None
            if self.enable_quality_filter:
                quality_metrics = _calculate_metrics(raw.content)
                quality_tier = _determine_quality_tier(quality_metrics)

                # Filter out documents below minimum quality tier
                tier_order = [QualityTier.NOISE, QualityTier.BRONZE, QualityTier.SILVER, QualityTier.GOLD]
                if tier_order.index(quality_tier) < tier_order.index(self.min_quality_tier):
                    return IngestionResult.failure_result(
                        reason=f"Document quality {quality_tier.value} below minimum {self.min_quality_tier.value}",
                        stage="quality_check",
                    )

            # META-03: Document type classification
            doc_type = classify_document_type(raw.title, raw.content)

            # META-06: Owner extraction
            owner = extract_owner(raw)

            # META-07: L1 category assignment
            l1_category = classify_l1_category(raw.title, raw.content)

            # META-08: Quality score (numeric 0-100)
            quality_score = calculate_quality_score(quality_metrics, quality_tier)

            # 2. Enhanced parsing (VEC-02, VEC-03)
            # If parse_result was provided (from JSONL checkpoint), skip re-parsing
            if parse_result is None:
                filename = raw.metadata.get("filename", "")
                filename_lower = filename.lower() if filename else ""
                if filename_lower and any(filename_lower.endswith(ext) for ext in _BINARY_EXTENSIONS):
                    try:
                        file_bytes = raw.metadata.get("file_bytes")
                        if isinstance(file_bytes, bytes):
                            parse_result = parse_bytes_enhanced(file_bytes, filename)
                    except Exception as e:
                        logger.warning(
                            "Enhanced parsing failed for doc_id=%s, falling back to plain text: %s",
                            raw.doc_id, e,
                        )

            # 2.5. Preprocess: clean text before chunking
            body_content = parse_result.text if parse_result else raw.content
            body_content = _clean_text_for_embedding(body_content)

            # Extract document summary for contextual retrieval prefix
            doc_summary = _extract_document_summary(body_content)

            # 3. Chunk body text with heading paths (META-04)
            # KSS sentence splitter is CPU-bound and blocks the event loop on large texts
            chunk_result = await asyncio.to_thread(
                self.chunker.chunk_with_headings, body_content,
            )
            if not chunk_result.chunks and not (parse_result and (parse_result.tables or parse_result.ocr_text)):
                return IngestionResult.failure_result(
                    reason="No chunks produced from document content",
                    stage="chunk",
                )

            # Build heading path map for body chunks
            heading_map: dict[int, str] = {}
            if chunk_result.heading_chunks:
                for i, hc in enumerate(chunk_result.heading_chunks):
                    heading_map[i] = hc.heading_path

            # 4. Build typed chunk list: (text, chunk_type, heading_path)
            typed_chunks: list[tuple[str, str, str]] = []

            # Body chunks
            for idx, chunk_text in enumerate(chunk_result.chunks):
                heading_path = heading_map.get(idx, "")
                typed_chunks.append((chunk_text, "body", heading_path))

            # Table chunks (VEC-02) - each table as a separate chunk
            if parse_result and parse_result.tables:
                for table_data in parse_result.tables:
                    table_md = _table_to_markdown(table_data)
                    if table_md.strip():
                        typed_chunks.append((table_md, "table", ""))

            # OCR chunks (VEC-03) - split by Page/Slide unit (merge images within same page)
            if parse_result and parse_result.ocr_text.strip():
                import re as _re
                ocr_text = parse_result.ocr_text.strip()
                # Split by [Page N ...] or [Slide N ...] boundaries
                page_segments = _re.split(
                    r'(?=\[(?:Page|Slide)\s+\d+[^\]]*\])', ocr_text,
                )
                # If no page/slide markers, fall back to [Image N] split
                if len(page_segments) <= 1:
                    page_segments = _re.split(
                        r'(?=\[Image\s+\d+[^\]]*\])', ocr_text,
                    )
                for seg in page_segments:
                    seg = seg.strip()
                    if not seg:
                        continue
                    # If segment still too long, chunk it further
                    if len(seg) > weights.chunking.max_chunk_chars:
                        sub_result = await asyncio.to_thread(self.chunker.chunk, seg)
                        for sc in sub_result.chunks:
                            typed_chunks.append((sc, "ocr", ""))
                    else:
                        typed_chunks.append((seg, "ocr", ""))

            if not typed_chunks:
                return IngestionResult.failure_result(
                    reason="No typed chunks produced from document content",
                    stage="chunk",
                )

            # 4.5. Passage cleaning: dedup sentences, remove fragments
            cleaned_chunks: list[tuple[str, str, str]] = []
            for chunk_text, chunk_type, heading_path in typed_chunks:
                cleaned = _clean_passage(chunk_text)
                if cleaned.strip():
                    cleaned_chunks.append((cleaned, chunk_type, heading_path))
            typed_chunks = cleaned_chunks if cleaned_chunks else typed_chunks

            # 5. Add document context prefix (Contextual Retrieval pattern)
            total_chunks_count = len(typed_chunks)
            prefixed_chunks: list[str] = []
            chunk_types: list[str] = []
            chunk_heading_paths: list[str] = []
            for idx, (chunk_text, chunk_type, heading_path) in enumerate(typed_chunks):
                doc_prefix = _build_document_context_prefix(
                    raw,
                    heading_path=heading_path,
                    chunk_type=chunk_type,
                    chunk_index=idx,
                    total_chunks=total_chunks_count,
                    doc_summary=doc_summary,
                )
                prefixed = f"{doc_prefix}{chunk_text}" if doc_prefix else chunk_text
                prefixed_chunks.append(prefixed)
                chunk_types.append(chunk_type)
                chunk_heading_paths.append(heading_path)

            # 6. Embed (dense + sparse) in parallel with retry
            async def _embed_sparse_with_retry(texts):
                for attempt in range(1, self._EMBED_MAX_RETRIES + 1):
                    try:
                        return await self.sparse_embedder.embed_sparse(texts)
                    except Exception as e:
                        if attempt < self._EMBED_MAX_RETRIES:
                            logger.warning("Sparse embed attempt %d/%d failed: %s", attempt, self._EMBED_MAX_RETRIES, e)
                            await asyncio.sleep(self._EMBED_RETRY_DELAY)
                        else:
                            raise

            dense_vectors, sparse_vectors = await asyncio.gather(
                self._embed_dense(prefixed_chunks),
                _embed_sparse_with_retry(prefixed_chunks),
            )

            if len(dense_vectors) != len(prefixed_chunks):
                raise ValueError(
                    f"Embedding count mismatch: {len(prefixed_chunks)} chunks but {len(dense_vectors)} vectors"
                )

            # META-09: KiwiPy morpheme extraction for keyword search accuracy
            chunk_morphemes: list[str] = []
            try:
                from kiwipiepy import Kiwi as _Kiwi
                _kiwi = _Kiwi()
                _noun_tags = {"NNG", "NNP", "SL"}
                for chunk_text in [ct for ct, _, _ in typed_chunks]:
                    tokens = _kiwi.tokenize(chunk_text[:2000])
                    morphs = " ".join(t.form for t in tokens if t.tag in _noun_tags and len(t.form) >= 2)
                    chunk_morphemes.append(morphs)
            except Exception:
                chunk_morphemes = [""] * len(typed_chunks)

            # META-05: Content type flags from quality metrics
            content_flags: dict[str, bool] = {}
            if quality_metrics:
                content_flags = {
                    "has_tables": quality_metrics.has_tables,
                    "has_code": quality_metrics.has_code_blocks,
                    "has_images": quality_metrics.has_images,
                }

            # 7 & 8. Build items with deterministic UUIDs and both vector types
            now_iso = datetime.now(UTC).isoformat()
            items: list[dict[str, Any]] = []
            for idx, (chunk_text, dense_vec, sparse_vec) in enumerate(
                zip(prefixed_chunks, dense_vectors, sparse_vectors)
            ):
                # Deterministic point ID using str_to_uuid
                point_id_str = f"{collection_name}:{raw.doc_id}:{idx}"
                point_uuid = str_to_uuid(point_id_str)

                chunk_metadata = dict(raw.metadata)
                # Remove non-serializable file_bytes from metadata
                chunk_metadata.pop("file_bytes", None)
                chunk_metadata.update({
                    "doc_id": raw.doc_id,
                    "document_name": raw.title,
                    "source_uri": raw.source_uri,
                    "author": raw.author,
                    "chunk_index": idx,
                    "chunk_type": chunk_types[idx],
                    "ingested_at": now_iso,
                    "quality_tier": quality_tier.value,
                    "quality_score": quality_score,
                    "original_id": point_id_str,
                    "kb_id": collection_name,
                    "doc_type": doc_type,
                    "owner": owner,
                    "l1_category": l1_category,
                    "morphemes": chunk_morphemes[idx] if idx < len(chunk_morphemes) else "",
                })
                # META-04: heading path
                if chunk_heading_paths[idx]:
                    chunk_metadata["heading_path"] = chunk_heading_paths[idx]
                # META-05: content type flags
                chunk_metadata.update(content_flags)
                # last_modified: prefer raw.updated_at, then file metadata date
                if raw.updated_at:
                    chunk_metadata["last_modified"] = raw.updated_at.isoformat()
                elif parse_result and getattr(parse_result, "file_modified_at", ""):
                    chunk_metadata["last_modified"] = parse_result.file_modified_at

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

            # VEC-01: Title-only vector for direct title search
            title_text = raw.title or ""
            labels = raw.metadata.get("labels", [])
            if labels:
                title_text += f" {' '.join(labels)}"
            if title_text.strip():
                title_dense, title_sparse = await asyncio.gather(
                    self._embed_dense([title_text]),
                    self.sparse_embedder.embed_sparse([title_text]),
                )
                title_sparse_vec = title_sparse[0] if title_sparse else {}
                if isinstance(title_sparse_vec, dict) and "indices" in title_sparse_vec:
                    title_sparse_converted = dict(
                        zip(title_sparse_vec["indices"], title_sparse_vec["values"])
                    )
                else:
                    title_sparse_converted = title_sparse_vec

                title_metadata = dict(raw.metadata)
                title_metadata.pop("file_bytes", None)
                title_metadata.update({
                    "doc_id": raw.doc_id,
                    "document_name": raw.title,
                    "source_uri": raw.source_uri,
                    "author": raw.author,
                    "chunk_type": "title",
                    "chunk_index": -1,
                    "ingested_at": now_iso,
                    "quality_tier": quality_tier.value,
                    "quality_score": quality_score,
                    "original_id": f"{collection_name}:{raw.doc_id}:title",
                    "kb_id": collection_name,
                    "doc_type": doc_type,
                    "owner": owner,
                    "l1_category": l1_category,
                })
                title_metadata.update(content_flags)
                if raw.updated_at:
                    title_metadata["last_modified"] = raw.updated_at.isoformat()
                elif parse_result and getattr(parse_result, "file_modified_at", ""):
                    title_metadata["last_modified"] = parse_result.file_modified_at

                items.append({
                    "content": raw.title,
                    "dense_vector": title_dense[0],
                    "sparse_vector": title_sparse_converted,
                    "metadata": title_metadata,
                    "point_id": str_to_uuid(f"{collection_name}:{raw.doc_id}:title"),
                })

            # 9 & 10. Store in vector DB + graph DB in parallel
            await asyncio.gather(
                self.vector_store.upsert_batch(
                    collection_name,
                    items,
                ),
                self.graph_store.upsert_document(
                    doc_id=raw.doc_id,
                    title=raw.title,
                    kb_id=collection_name,
                    source_type=raw.metadata.get("source_type", "file"),
                ),
            )

            # 11. Create graph edges (GRAPH-02, GRAPH-04, GRAPH-06, GRAPH-01, GRAPH-07, GRAPH-08)
            await self._create_graph_edges(
                raw, collection_name, owner=owner, l1_category=l1_category,
            )

            # 12. Term extraction (optional): extract domain terms from chunks
            term_extraction_stats: dict[str, Any] = {}
            if self.enable_term_extraction and self.term_extractor is not None:
                try:
                    chunk_texts = [ct for ct, _, _ in typed_chunks]
                    extracted_terms = await self.term_extractor.extract_from_chunks(
                        chunk_texts, kb_id=collection_name,
                    )
                    if extracted_terms:
                        saved_count = await self.term_extractor.save_extracted_terms(
                            extracted_terms, kb_id=collection_name,
                        )
                        term_extraction_stats = {
                            "terms_extracted": len(extracted_terms),
                            "terms_saved": saved_count,
                        }
                        logger.info(
                            "Term extraction completed for doc_id=%s: %d extracted, %d saved",
                            raw.doc_id, len(extracted_terms), saved_count,
                        )
                except Exception as e:
                    logger.warning("Term extraction failed for doc_id=%s: %s", raw.doc_id, e)
                    term_extraction_stats = {"error": str(e)}

            # 12b. Synonym discovery (optional): discover synonym pairs from full text
            synonym_discovery_stats: dict[str, Any] = {}
            if self.enable_term_extraction and self.term_extractor is not None:
                try:
                    discover_fn = getattr(self.term_extractor, "discover_synonyms", None)
                    save_syn_fn = getattr(self.term_extractor, "save_discovered_synonyms", None)
                    if discover_fn and save_syn_fn:
                        # Gather known terms for matching context
                        glossary_repo = getattr(self.term_extractor, "_glossary_repo", None)
                        known_terms: list[dict[str, Any]] = []
                        if glossary_repo:
                            list_fn = getattr(glossary_repo, "list_by_kb", None)
                            if list_fn and callable(list_fn):
                                try:
                                    known_terms = await list_fn(
                                        kb_id=collection_name, status="approved",
                                        limit=500, offset=0,
                                    )
                                except Exception:
                                    known_terms = []

                        discoveries = await discover_fn(raw.content, known_terms)
                        if discoveries:
                            syn_saved = await save_syn_fn(
                                discoveries, kb_id=collection_name,
                            )
                            synonym_discovery_stats = {
                                "synonyms_discovered": len(discoveries),
                                "synonyms_saved": syn_saved,
                            }
                            logger.info(
                                "Synonym discovery completed for doc_id=%s: %d found, %d saved",
                                raw.doc_id, len(discoveries), syn_saved,
                            )
                except Exception as e:
                    logger.warning("Synonym discovery failed for doc_id=%s: %s", raw.doc_id, e)
                    synonym_discovery_stats = {"error": str(e)}

            # 13. GraphRAG extraction (optional)
            graphrag_stats: dict[str, Any] = {}
            if self.enable_graphrag and self.graphrag_extractor is not None:
                try:
                    extraction_result = await asyncio.to_thread(
                        lambda: self.graphrag_extractor.extract(
                            document=raw.content,
                            source_title=raw.title,
                            source_page_id=raw.doc_id,
                            source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
                            kb_id=collection_name,
                        )
                    )
                    if extraction_result.node_count > 0 or extraction_result.relationship_count > 0:
                        save_stats = await asyncio.to_thread(
                            self.graphrag_extractor.save_to_neo4j, extraction_result
                        )
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

            # Count chunks by type
            body_count = sum(1 for ct in chunk_types if ct == "body")
            table_count = sum(1 for ct in chunk_types if ct == "table")
            ocr_count = sum(1 for ct in chunk_types if ct == "ocr")

            # Register content hash in dedup cache after successful ingestion
            if self.dedup_cache:
                try:
                    await self.dedup_cache.add(collection_name, _content_hash)
                except Exception as _dedup_err:
                    logger.warning("Dedup cache registration failed: %s", _dedup_err)

            result_metadata: dict[str, Any] = {
                "collection_name": collection_name,
                "chunk_strategy": self.chunker.strategy_name,
                "total_chunks": len(items),
                "body_chunks": body_count,
                "table_chunks": table_count,
                "ocr_chunks": ocr_count,
                "has_title_vector": True,
                "quality_tier": quality_tier.value,
                "quality_score": quality_score,
                "doc_type": doc_type,
                "owner": owner,
                "l1_category": l1_category,
                "has_sparse_vectors": True,
                "has_document_prefix": True,
                "has_heading_paths": bool(heading_map),
                "deterministic_uuids": True,
            }
            result_metadata.update(content_flags)
            if graphrag_stats:
                result_metadata["graphrag"] = graphrag_stats
            if term_extraction_stats:
                result_metadata["term_extraction"] = term_extraction_stats
            if synonym_discovery_stats:
                result_metadata["synonym_discovery"] = synonym_discovery_stats
            if dedup_result_info:
                result_metadata["dedup"] = dedup_result_info

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

    async def _create_graph_edges(
        self,
        raw: RawDocument,
        _collection_name: str,
        *,
        owner: str = "",
        l1_category: str = "",
    ) -> None:
        """Create structural graph edges for the ingested document.

        Edges created:
        - GRAPH-02: CHILD_OF (wiki hierarchy)
        - GRAPH-04: Person -[:AUTHORED]-> Document
        - GRAPH-06: Document -[:BELONGS_TO]-> Space
        - GRAPH-01: Document -[:REFERENCES]-> Document (cross-references)
        - GRAPH-07: Person -[:OWNS]-> Document (owner)
        - GRAPH-08: Document -[:CATEGORIZED_AS]-> Category (L1 category)
        """
        try:
            # GRAPH-02: Wiki hierarchy (CHILD_OF)
            parent_id = raw.metadata.get("parent_id")
            if parent_id:
                await self.graph_store.execute_write(
                    "MERGE (child:Document {id: $child_id}) "
                    "MERGE (parent:Document {id: $parent_id}) "
                    "MERGE (child)-[:CHILD_OF]->(parent)",
                    {"child_id": raw.doc_id, "parent_id": parent_id},
                )

            # GRAPH-04: Person AUTHORED Document
            if raw.author:
                await self.graph_store.execute_write(
                    "MERGE (p:Person {name: $author}) "
                    "MERGE (d:Document {id: $doc_id}) "
                    "MERGE (p)-[:AUTHORED]->(d)",
                    {"author": raw.author, "doc_id": raw.doc_id},
                )

            # GRAPH-06: Document BELONGS_TO Space
            space_key = raw.metadata.get("space_key")
            if space_key:
                space_name = raw.metadata.get("space_name", space_key)
                await self.graph_store.execute_write(
                    "MERGE (s:Space {key: $key}) SET s.name = $name "
                    "MERGE (d:Document {id: $doc_id}) "
                    "MERGE (d)-[:BELONGS_TO]->(s)",
                    {"key": space_key, "name": space_name, "doc_id": raw.doc_id},
                )

            # GRAPH-01: Document cross-references (REFERENCES) - batched via UNWIND
            cross_refs = _extract_cross_references(raw.content)
            if cross_refs:
                ref_params = [
                    {"tgt_uri": link_url, "link_text": link_text}
                    for link_text, link_url in cross_refs
                ]
                await self.graph_store.execute_write(
                    "UNWIND $refs AS ref "
                    "MERGE (src:Document {id: $src_id}) "
                    "MERGE (tgt:Document {uri: ref.tgt_uri}) "
                    "ON CREATE SET tgt.title = ref.link_text "
                    "MERGE (src)-[:REFERENCES {link_text: ref.link_text}]->(tgt)",
                    {"src_id": raw.doc_id, "refs": ref_params},
                )

            # GRAPH-07: Person OWNS Document (owner/담당자)
            if owner:
                await self.graph_store.execute_write(
                    "MERGE (p:Person {name: $owner}) "
                    "MERGE (d:Document {id: $doc_id}) "
                    "MERGE (p)-[:OWNS]->(d)",
                    {"owner": owner, "doc_id": raw.doc_id},
                )

            # GRAPH-08: Document CATEGORIZED_AS Category (L1)
            if l1_category and l1_category != "기타":
                await self.graph_store.execute_write(
                    "MERGE (c:Category {name: $category}) "
                    "MERGE (d:Document {id: $doc_id}) "
                    "MERGE (d)-[:CATEGORIZED_AS]->(c)",
                    {"category": l1_category, "doc_id": raw.doc_id},
                )

        except Exception as e:
            # Graph edge creation is non-critical; log and continue
            logger.warning(
                "Graph edge creation failed for doc_id=%s: %s",
                raw.doc_id, e,
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
    "classify_document_type",
]
