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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config.weights import weights
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
from src.pipeline.ocr_corrector import clean_chunk_text as _clean_chunk_text

logger = logging.getLogger(__name__)


@dataclass
class _ChunkContext:
    """Shared context for building chunk items (reduces parameter count)."""

    raw: Any
    collection_name: str
    chunk_types: list[str]
    chunk_heading_paths: list[str]
    chunk_morphemes: list[str]
    now_iso: str
    quality_tier: Any  # QualityTier
    quality_score: float
    doc_type: str
    owner: str
    l1_category: str
    content_flags: dict[str, bool]
    parse_result: Any = None


@dataclass(frozen=True)
class IngestionFeatureFlags:
    """Feature toggles for the ingestion pipeline."""

    enable_quality_filter: bool = True
    enable_graphrag: bool = False
    enable_term_extraction: bool = False
    min_quality_tier: QualityTier = QualityTier.BRONZE
    enable_ingestion_gate: bool = False


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
        self.dedup_pipeline = dedup_pipeline  # Full 4-stage dedup (preferred over dedup_cache)

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

        # Ingestion gate (Stage 0 pre-validation)
        self._ingestion_gate = None
        if _f.enable_ingestion_gate:
            try:
                from .ingestion_gate import IngestionGate
                self._ingestion_gate = IngestionGate(enabled=True)
                logger.info("Ingestion gate enabled")
            except Exception as e:  # noqa: BLE001
                logger.warning("Ingestion gate init failed: %s", e)

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

    async def _check_dedup(
        self, raw: RawDocument, collection_name: str, content_hash: str,
    ) -> tuple[IngestionResult | None, dict[str, Any]]:
        """Run dedup checks. Returns (failure_result, dedup_info) or (None, dedup_info)."""
        dedup_result_info: dict[str, Any] = {}

        if raw.metadata.get("force_rebuild", False):
            return None, dedup_result_info

        if self.dedup_pipeline is not None:
            try:
                from src.pipeline.dedup import Document as DedupDoc, DedupStatus
                dedup_doc = DedupDoc(
                    doc_id=raw.doc_id, title=raw.title, content=raw.content,
                    url=raw.source_uri, updated_at=raw.updated_at,
                )
                dedup_result = await self.dedup_pipeline.add(dedup_doc)
                dedup_result_info = dedup_result.to_dict()
                if dedup_result.status == DedupStatus.EXACT_DUPLICATE:
                    logger.info(
                        "Dedup(4-stage): exact duplicate doc_id=%s dup_of=%s (%.1fms)",
                        raw.doc_id, dedup_result.duplicate_of, dedup_result.processing_time_ms,
                    )
                    return IngestionResult.failure_result(
                        reason=f"Exact duplicate of {dedup_result.duplicate_of} (dedup pipeline Stage 1)",
                        stage="dedup",
                    ), dedup_result_info
                if dedup_result.status in (DedupStatus.NEAR_DUPLICATE, DedupStatus.SEMANTIC_DUPLICATE):
                    logger.info(
                        "Dedup(4-stage): %s doc_id=%s dup_of=%s score=%.3f (%.1fms) - proceeding",
                        dedup_result.status.value, raw.doc_id,
                        dedup_result.duplicate_of, dedup_result.similarity_score,
                        dedup_result.processing_time_ms,
                    )
            except Exception as _dedup_err:  # noqa: BLE001
                logger.warning("Dedup pipeline check failed, proceeding: %s", _dedup_err)
        elif self.dedup_cache is not None:
            try:
                if await self.dedup_cache.exists(collection_name, content_hash):
                    logger.info(
                        "Dedup: skipping duplicate doc_id=%s in %s (hash=%s)",
                        raw.doc_id, collection_name, content_hash[:12],
                    )
                    return IngestionResult.failure_result(
                        reason="Duplicate content (dedup cache hit)", stage="dedup",
                    ), dedup_result_info
            except Exception as _dedup_err:  # noqa: BLE001
                logger.warning("Dedup cache check failed, proceeding: %s", _dedup_err)

        return None, dedup_result_info

    @staticmethod
    def _try_binary_parse(raw: RawDocument) -> ParseResult | None:
        """Attempt enhanced binary parsing for known file extensions."""
        filename = raw.metadata.get("filename", "")
        filename_lower = filename.lower() if filename else ""
        if not filename_lower or not any(filename_lower.endswith(ext) for ext in _BINARY_EXTENSIONS):
            return None
        try:
            file_bytes = raw.metadata.get("file_bytes")
            if isinstance(file_bytes, bytes):
                return parse_bytes_enhanced(file_bytes, filename)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Enhanced parsing failed for doc_id=%s, falling back to plain text: %s",
                raw.doc_id, e,
            )
        return None

    @staticmethod
    def _build_body_chunks(
        chunk_result, heading_map: dict[int, str],
    ) -> list[tuple[str, str, str]]:
        """Convert chunk result into typed body chunks."""
        return [
            (chunk_text, "body", heading_map.get(idx, ""))
            for idx, chunk_text in enumerate(chunk_result.chunks)
        ]

    @staticmethod
    def _append_table_chunks(
        typed_chunks: list[tuple[str, str, str]], parse_result: ParseResult | None,
    ) -> None:
        """Append table chunks from parse result."""
        if not parse_result or not parse_result.tables:
            return
        for table_data in parse_result.tables:
            table_md = _table_to_markdown(table_data)
            if table_md.strip():
                typed_chunks.append((table_md, "table", ""))

    async def _build_typed_chunks(
        self, raw: RawDocument, parse_result: ParseResult | None,
    ) -> tuple[list[tuple[str, str, str]], dict[int, str], str] | IngestionResult:
        """Parse, chunk, and clean document content.

        Returns:
            (typed_chunks, heading_map, doc_summary) on success,
            or IngestionResult on failure.
        """
        if parse_result is None:
            parse_result = self._try_binary_parse(raw)

        body_content = parse_result.text if parse_result else raw.content
        body_content = _clean_text_for_embedding(body_content)
        doc_summary = _extract_document_summary(body_content)

        # Korean legal docs (detected via git connector frontmatter) use an
        # article-preserving chunker so 제N조 stays intact as a unit.
        if raw.metadata.get("_is_legal_document"):
            chunk_result = await asyncio.to_thread(
                self.chunker.chunk_legal_articles, body_content,
            )
        else:
            chunk_result = await asyncio.to_thread(
                self.chunker.chunk_with_headings, body_content,
            )
        if not chunk_result.chunks and not (parse_result and (parse_result.tables or parse_result.ocr_text)):
            return IngestionResult.failure_result(
                reason="No chunks produced from document content", stage="chunk",
            )

        heading_map: dict[int, str] = {}
        if chunk_result.heading_chunks:
            for i, hc in enumerate(chunk_result.heading_chunks):
                heading_map[i] = hc.heading_path

        typed_chunks = self._build_body_chunks(chunk_result, heading_map)
        self._append_table_chunks(typed_chunks, parse_result)

        if parse_result and parse_result.ocr_text.strip():
            ocr_chunks = await self._split_ocr_text(parse_result.ocr_text.strip())
            typed_chunks.extend(ocr_chunks)

        if not typed_chunks:
            return IngestionResult.failure_result(
                reason="No typed chunks produced from document content", stage="chunk",
            )

        cleaned_chunks = [
            (cleaned, ct, hp)
            for chunk_text, ct, hp in typed_chunks
            if (cleaned := _clean_passage(chunk_text)).strip()
        ]
        typed_chunks = cleaned_chunks if cleaned_chunks else typed_chunks

        return typed_chunks, heading_map, doc_summary

    async def _split_ocr_text(self, ocr_text: str) -> list[tuple[str, str, str]]:
        """Split OCR text into typed chunks by page/slide/image boundaries."""
        import re as _re
        page_segments = _re.split(r'(?=\[(?:Page|Slide)\s+\d+[^\]]*\])', ocr_text)
        if len(page_segments) <= 1:
            page_segments = _re.split(r'(?=\[Image\s+\d+[^\]]*\])', ocr_text)

        ocr_chunks: list[tuple[str, str, str]] = []
        for seg in page_segments:
            seg = seg.strip()
            if not seg:
                continue
            if len(seg) > weights.chunking.max_chunk_chars:
                sub_result = await asyncio.to_thread(self.chunker.chunk, seg)
                for sc in sub_result.chunks:
                    ocr_chunks.append((sc, "ocr", ""))
            else:
                ocr_chunks.append((seg, "ocr", ""))
        return ocr_chunks

    @staticmethod
    def _extract_morphemes(typed_chunks: list[tuple[str, str, str]]) -> list[str]:
        """Extract KiwiPy morphemes from typed chunks."""
        try:
            from kiwipiepy import Kiwi as _Kiwi
            _kiwi = _Kiwi()
            _noun_tags = {"NNG", "NNP", "SL"}
            morphemes = []
            for chunk_text, _, _ in typed_chunks:
                tokens = _kiwi.tokenize(chunk_text[:2000])
                morphs = " ".join(t.form for t in tokens if t.tag in _noun_tags and len(t.form) >= 2)
                morphemes.append(morphs)
            return morphemes
        except Exception:  # noqa: BLE001
            return [""] * len(typed_chunks)

    @staticmethod
    def _append_date_author_tokens(
        chunk_morphemes: list[str], title: str | None, author: str | None,
    ) -> list[str]:
        """Append date/author tokens to morphemes for sparse matching."""
        import re as _re_morph
        _dm = _re_morph.search(r"(20\d{2})[_\-./](0[1-9]|1[0-2])", title or "")
        if not _dm:
            _dm = _re_morph.search(r"(20\d{2})년\s*(\d{1,2})월", title or "")
        _date_tokens = ""
        if _dm:
            _y, _m = _dm.group(1), str(int(_dm.group(2))).zfill(2)
            _date_tokens = f" {_y} {_y}년 {int(_m)}월 {_y}_{_m}"
        _wk = _re_morph.search(r"(\d{1,2})월\s*(\d)주차", title or "")
        if _wk:
            _date_tokens += f" {_wk.group(1)}월 {_wk.group(2)}주차"
        if author:
            _date_tokens += f" {author}"
        if _date_tokens:
            return [m + _date_tokens for m in chunk_morphemes]
        return chunk_morphemes

    def _build_chunk_item(
        self,
        idx: int,
        chunk_text: str,
        dense_vec: list[float],
        sparse_vec: Any,
        *,
        ctx: _ChunkContext,
    ) -> dict[str, Any]:
        """Build a single vector store item dict."""
        raw = ctx.raw
        point_id_str = f"{ctx.collection_name}:{raw.doc_id}:{idx}"
        chunk_metadata = dict(raw.metadata)
        chunk_metadata.pop("file_bytes", None)
        chunk_metadata.update({
            "doc_id": raw.doc_id, "document_name": raw.title,
            "source_uri": raw.source_uri, "author": raw.author,
            "chunk_index": idx, "chunk_type": ctx.chunk_types[idx],
            "ingested_at": ctx.now_iso, "quality_tier": ctx.quality_tier.value,
            "quality_score": ctx.quality_score, "original_id": point_id_str,
            "kb_id": ctx.collection_name, "doc_type": ctx.doc_type,
            "owner": ctx.owner, "l1_category": ctx.l1_category,
            "morphemes": ctx.chunk_morphemes[idx] if idx < len(ctx.chunk_morphemes) else "",
        })
        if ctx.chunk_heading_paths[idx]:
            chunk_metadata["heading_path"] = ctx.chunk_heading_paths[idx]
        chunk_metadata.update(ctx.content_flags)
        if raw.updated_at:
            chunk_metadata["last_modified"] = raw.updated_at.isoformat()
        elif ctx.parse_result and getattr(ctx.parse_result, "file_modified_at", ""):
            chunk_metadata["last_modified"] = ctx.parse_result.file_modified_at

        if isinstance(sparse_vec, dict) and "indices" in sparse_vec:
            sparse_converted = dict(zip(sparse_vec["indices"], sparse_vec["values"]))
        else:
            sparse_converted = sparse_vec

        return {
            "content": truncate_content(chunk_text),
            "dense_vector": dense_vec,
            "sparse_vector": sparse_converted,
            "metadata": chunk_metadata,
            "point_id": str_to_uuid(point_id_str),
        }

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
        """Build the title-only vector item (VEC-01). Returns None if no title."""
        title_text = raw.title or ""
        labels = raw.metadata.get("labels", [])
        if labels:
            title_text += f" {' '.join(labels)}"
        if not title_text.strip():
            return None

        title_dense, title_sparse = await asyncio.gather(
            self._embed_dense([title_text]),
            self.sparse_embedder.embed_sparse([title_text]),
        )
        title_sparse_vec = title_sparse[0] if title_sparse else {}
        if isinstance(title_sparse_vec, dict) and "indices" in title_sparse_vec:
            title_sparse_converted = dict(zip(title_sparse_vec["indices"], title_sparse_vec["values"]))
        else:
            title_sparse_converted = title_sparse_vec

        title_metadata = dict(raw.metadata)
        title_metadata.pop("file_bytes", None)
        title_metadata.update({
            "doc_id": raw.doc_id, "document_name": raw.title,
            "source_uri": raw.source_uri, "author": raw.author,
            "chunk_type": "title", "chunk_index": -1,
            "ingested_at": now_iso, "quality_tier": quality_tier.value,
            "quality_score": quality_score,
            "original_id": f"{collection_name}:{raw.doc_id}:title",
            "kb_id": collection_name, "doc_type": doc_type,
            "owner": owner, "l1_category": l1_category,
        })
        title_metadata.update(content_flags)
        if raw.updated_at:
            title_metadata["last_modified"] = raw.updated_at.isoformat()
        elif parse_result and getattr(parse_result, "file_modified_at", ""):
            title_metadata["last_modified"] = parse_result.file_modified_at

        return {
            "content": raw.title,
            "dense_vector": title_dense[0],
            "sparse_vector": title_sparse_converted,
            "metadata": title_metadata,
            "point_id": str_to_uuid(f"{collection_name}:{raw.doc_id}:title"),
        }

    async def _run_tree_index_builder(
        self,
        raw: RawDocument,
        items: list[dict[str, Any]],
        chunk_heading_paths: list[str],
        collection_name: str,
    ) -> None:
        """Stage 12: heading_path → Neo4j 트리 구축 (실패해도 인제스트 계속)."""
        from src.config import get_settings
        if not get_settings().tree_index.enabled:
            return
        if not self.graph_store:
            return
        try:
            from .tree_index_builder import build_tree_from_chunks, persist_tree_to_neo4j
            chunks_for_tree = []
            for item in items:
                meta = item.get("metadata", {})
                if meta.get("chunk_type") == "title":
                    continue
                ci = meta.get("chunk_index", 0)
                chunks_for_tree.append({
                    "chunk_id": str(item.get("point_id", "")),
                    "heading_path": chunk_heading_paths[ci] if ci < len(chunk_heading_paths) else "",
                    "chunk_index": ci,
                })
            if not chunks_for_tree:
                return
            tree_data = build_tree_from_chunks(collection_name, raw.doc_id, chunks_for_tree)
            await persist_tree_to_neo4j(self.graph_store, tree_data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tree index build failed for doc_id=%s: %s", raw.doc_id, exc)

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
        """Stage 13: RAPTOR식 요약 트리 (실패해도 인제스트 계속)."""
        from src.config import get_settings
        ts = get_settings().tree_index
        if not ts.enabled or not ts.summary_enabled:
            return
        embedder = getattr(self, 'embedding_provider', None)
        llm = getattr(self, 'llm_client', None)
        if not embedder or not llm:
            return
        try:
            from .summary_tree_builder import build_summary_tree
            from .qdrant_utils import str_to_uuid

            body_chunks = []
            for idx, item in enumerate(items):
                meta = item.get("metadata", {})
                if meta.get("chunk_type") == "title":
                    continue
                ci = meta.get("chunk_index", 0)
                body_chunks.append({
                    "text": prefixed_chunks[ci] if ci < len(prefixed_chunks) else "",
                    "embedding": dense_vectors[ci] if ci < len(dense_vectors) else [],
                    "chunk_id": str(item.get("point_id", "")),
                    "heading_path": chunk_heading_paths[ci] if ci < len(chunk_heading_paths) else "",
                })

            if len(body_chunks) < ts.summary_cluster_min_chunks:
                return

            summaries = await build_summary_tree(
                body_chunks, embedder, llm,
                max_layers=ts.summary_max_layers,
                min_chunks=ts.summary_cluster_min_chunks,
                use_umap=True,
                umap_dim=ts.summary_umap_dim,
            )

            if not summaries:
                return

            # Qdrant에 요약 청크 저장
            summary_items = []
            for i, s in enumerate(summaries):
                point_id_str = f"{collection_name}:{raw.doc_id}:summary:{s['layer']}:{i}"
                summary_items.append({
                    "content": s["text"],
                    "dense_vector": s["embedding"],
                    "sparse_vector": {},
                    "metadata": {
                        "document_id": raw.doc_id,
                        "document_name": raw.title,
                        "chunk_type": "summary",
                        "summary_layer": s["layer"],
                        # Qdrant payload 크기 제한 (16KB)을 위해 상위 10개만 저장
                        "source_chunk_ids": s["source_chunk_ids"][:10],
                        "kb_id": collection_name,
                        "ingested_at": now_iso,
                    },
                    "point_id": str_to_uuid(point_id_str),
                })
            await self.vector_store.upsert_batch(collection_name, summary_items)
            logger.info(
                "Summary tree stored: doc_id=%s, summaries=%d",
                raw.doc_id, len(summary_items),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Summary tree build failed for doc_id=%s: %s", raw.doc_id, exc)

    async def _run_term_extraction(
        self, raw: RawDocument, typed_chunks: list[tuple[str, str, str]], collection_name: str,
    ) -> dict[str, Any]:
        """Run optional term extraction and synonym discovery."""
        stats: dict[str, Any] = {}
        if not self.enable_term_extraction or self.term_extractor is None:
            return stats

        try:
            chunk_texts = [ct for ct, _, _ in typed_chunks]
            extracted_terms = await self.term_extractor.extract_from_chunks(
                chunk_texts, kb_id=collection_name,
            )
            if extracted_terms:
                saved_count = await self.term_extractor.save_extracted_terms(
                    extracted_terms, kb_id=collection_name,
                )
                stats = {"terms_extracted": len(extracted_terms), "terms_saved": saved_count}
                logger.info(
                    "Term extraction completed for doc_id=%s: %d extracted, %d saved",
                    raw.doc_id, len(extracted_terms), saved_count,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Term extraction failed for doc_id=%s: %s", raw.doc_id, e)
            stats = {"error": str(e)}
        return stats

    async def _run_synonym_discovery(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any]:
        """Run optional synonym discovery."""
        stats: dict[str, Any] = {}
        if not self.enable_term_extraction or self.term_extractor is None:
            return stats

        try:
            discover_fn = getattr(self.term_extractor, "discover_synonyms", None)
            save_syn_fn = getattr(self.term_extractor, "save_discovered_synonyms", None)
            if not discover_fn or not save_syn_fn:
                return stats

            glossary_repo = getattr(self.term_extractor, "_glossary_repo", None)
            known_terms: list[dict[str, Any]] = []
            if glossary_repo:
                list_fn = getattr(glossary_repo, "list_by_kb", None)
                if list_fn and callable(list_fn):
                    try:
                        known_terms = await list_fn(
                            kb_id=collection_name, status="approved", limit=500, offset=0,
                        )
                    except Exception:  # noqa: BLE001
                        known_terms = []

            discoveries = await discover_fn(raw.content, known_terms)
            if discoveries:
                syn_saved = await save_syn_fn(discoveries, kb_id=collection_name)
                stats = {"synonyms_discovered": len(discoveries), "synonyms_saved": syn_saved}
                logger.info(
                    "Synonym discovery completed for doc_id=%s: %d found, %d saved",
                    raw.doc_id, len(discoveries), syn_saved,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Synonym discovery failed for doc_id=%s: %s", raw.doc_id, e)
            stats = {"error": str(e)}
        return stats

    async def _run_graphrag(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any]:
        """Run optional graph extraction.

        Legal documents (detected via the ``_is_legal_document`` metadata
        flag set by the git connector's frontmatter parser) are routed to
        the rule-based :class:`LegalGraphExtractor` which is cheaper and
        more accurate than the LLM-based GraphRAG path.
        """
        if raw.metadata.get("_is_legal_document") and self.legal_graph_extractor is not None:
            return await self._run_legal_graph_extraction(raw, collection_name)

        stats: dict[str, Any] = {}
        if not self.enable_graphrag or self.graphrag_extractor is None:
            return stats

        try:
            _graphrag_content = _clean_chunk_text(raw.content)
            extraction_result = await asyncio.to_thread(
                lambda: self.graphrag_extractor.extract(
                    document=_graphrag_content,
                    source_title=raw.title,
                    source_page_id=raw.doc_id,
                    source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
                    kb_id=collection_name,
                )
            )
            if extraction_result.node_count > 0 or extraction_result.relationship_count > 0:
                save_stats = await asyncio.to_thread(
                    self.graphrag_extractor.save_to_neo4j, extraction_result,
                )
                stats = {
                    "nodes_extracted": extraction_result.node_count,
                    "relationships_extracted": extraction_result.relationship_count,
                    **save_stats,
                }
                logger.info(
                    "GraphRAG extraction completed for doc_id=%s: %d nodes, %d rels",
                    raw.doc_id, extraction_result.node_count, extraction_result.relationship_count,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("GraphRAG extraction failed for doc_id=%s: %s", raw.doc_id, e)
            stats = {"error": str(e)}
        return stats

    async def _run_legal_graph_extraction(
        self, raw: RawDocument, collection_name: str,
    ) -> dict[str, Any]:
        """Run the rule-based legal graph extractor for a single document."""
        stats: dict[str, Any] = {}
        try:
            extraction_result = await self.legal_graph_extractor.extract_from_document(
                raw, kb_id=collection_name,
            )
            if extraction_result.node_count == 0 and extraction_result.relationship_count == 0:
                return stats
            save_stats = await asyncio.to_thread(
                self.legal_graph_extractor.save_to_neo4j, extraction_result,
            )
            stats = {
                "nodes_extracted": extraction_result.node_count,
                "relationships_extracted": extraction_result.relationship_count,
                "extractor": "legal_rule_based",
                **save_stats,
            }
            logger.info(
                "Legal graph extraction completed for doc_id=%s: %d nodes, %d rels",
                raw.doc_id,
                extraction_result.node_count,
                extraction_result.relationship_count,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Legal graph extraction failed for doc_id=%s: %s", raw.doc_id, e,
            )
            stats = {"error": str(e), "extractor": "legal_rule_based"}
        return stats

    def _check_ingestion_gate(
        self, raw: RawDocument, collection_name: str,
    ) -> IngestionResult | None:
        """Check ingestion gate. Returns failure result if blocked, None if allowed."""
        if self._ingestion_gate is None:
            return None
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
        return None

    def _check_quality(
        self, raw: RawDocument,
    ) -> tuple[QualityTier, QualityMetrics | None, IngestionResult | None]:
        """Run quality check. Returns (tier, metrics, failure_or_None)."""
        quality_tier = QualityTier.BRONZE
        quality_metrics: QualityMetrics | None = None
        if not self.enable_quality_filter:
            return quality_tier, quality_metrics, None
        quality_metrics = _calculate_metrics(raw.content)
        quality_tier = _determine_quality_tier(quality_metrics)
        tier_order = [QualityTier.NOISE, QualityTier.BRONZE, QualityTier.SILVER, QualityTier.GOLD]
        if tier_order.index(quality_tier) < tier_order.index(self.min_quality_tier):
            return quality_tier, quality_metrics, IngestionResult.failure_result(
                reason=f"Document quality {quality_tier.value} below minimum {self.min_quality_tier.value}",
                stage="quality_check",
            )
        return quality_tier, quality_metrics, None

    @staticmethod
    def _add_context_prefixes(
        raw: RawDocument,
        typed_chunks: list[tuple[str, str, str]],
        doc_summary: str,
    ) -> tuple[list[str], list[str], list[str]]:
        """Add document context prefix to each chunk. Returns (prefixed, types, heading_paths)."""
        total = len(typed_chunks)
        prefixed_chunks: list[str] = []
        chunk_types: list[str] = []
        chunk_heading_paths: list[str] = []
        for idx, (chunk_text, chunk_type, heading_path) in enumerate(typed_chunks):
            doc_prefix = _build_document_context_prefix(
                raw, heading_path=heading_path, chunk_type=chunk_type,
                chunk_index=idx, total_chunks=total, doc_summary=doc_summary,
            )
            prefixed_chunks.append(f"{doc_prefix}{chunk_text}" if doc_prefix else chunk_text)
            chunk_types.append(chunk_type)
            chunk_heading_paths.append(heading_path)
        return prefixed_chunks, chunk_types, chunk_heading_paths

    async def _embed_sparse_with_retry(self, texts: list[str]):
        """Embed sparse vectors with retry logic."""
        for attempt in range(1, self._EMBED_MAX_RETRIES + 1):
            try:
                return await self.sparse_embedder.embed_sparse(texts)
            except Exception as e:
                if attempt < self._EMBED_MAX_RETRIES:
                    logger.warning("Sparse embed attempt %d/%d failed: %s", attempt, self._EMBED_MAX_RETRIES, e)
                    await asyncio.sleep(self._EMBED_RETRY_DELAY)
                else:
                    raise

    @staticmethod
    def _build_result_metadata(
        ctx: _ChunkContext, chunker_strategy: str, items: list,
        heading_map: dict,
        *,
        graphrag_stats: dict | None = None,
        term_extraction_stats: dict | None = None,
        synonym_discovery_stats: dict | None = None,
        dedup_result_info: dict | None = None,
    ) -> dict[str, Any]:
        """Build the result metadata dict for a successful ingestion."""
        result_metadata: dict[str, Any] = {
            "collection_name": ctx.collection_name,
            "chunk_strategy": chunker_strategy,
            "total_chunks": len(items),
            "body_chunks": sum(1 for ct in ctx.chunk_types if ct == "body"),
            "table_chunks": sum(1 for ct in ctx.chunk_types if ct == "table"),
            "ocr_chunks": sum(1 for ct in ctx.chunk_types if ct == "ocr"),
            "has_title_vector": True,
            "quality_tier": ctx.quality_tier.value,
            "quality_score": ctx.quality_score,
            "doc_type": ctx.doc_type, "owner": ctx.owner, "l1_category": ctx.l1_category,
            "has_sparse_vectors": True, "has_document_prefix": True,
            "has_heading_paths": bool(heading_map), "deterministic_uuids": True,
        }
        result_metadata.update(ctx.content_flags)
        if graphrag_stats:
            result_metadata["graphrag"] = graphrag_stats
        if term_extraction_stats:
            result_metadata["term_extraction"] = term_extraction_stats
        if synonym_discovery_stats:
            result_metadata["synonym_discovery"] = synonym_discovery_stats
        if dedup_result_info:
            result_metadata["dedup"] = dedup_result_info
        return result_metadata

    async def ingest(
        self,
        raw: RawDocument,
        collection_name: str,
        parse_result: ParseResult | None = None,
    ) -> IngestionResult:
        """Execute the ingestion pipeline for a single document."""
        try:
            # Stage 0: Ingestion gate
            gate_failure = self._check_ingestion_gate(raw, collection_name)
            if gate_failure is not None:
                return gate_failure

            import hashlib as _hashlib
            _content_hash = _hashlib.sha256(raw.content.lower().strip().encode()).hexdigest()[:32]

            # 0. Dedup check
            dedup_failure, dedup_result_info = await self._check_dedup(
                raw, collection_name, _content_hash,
            )
            if dedup_failure is not None:
                return dedup_failure

            # 1. Quality check
            quality_tier, quality_metrics, quality_failure = self._check_quality(raw)
            if quality_failure is not None:
                return quality_failure

            doc_type = classify_document_type(raw.title, raw.content)
            owner = extract_owner(raw)
            l1_category = classify_l1_category(raw.title, raw.content)
            quality_score = calculate_quality_score(quality_metrics, quality_tier)

            # 2-4. Parse, chunk, clean
            chunk_result = await self._build_typed_chunks(raw, parse_result)
            if isinstance(chunk_result, IngestionResult):
                return chunk_result
            typed_chunks, heading_map, doc_summary = chunk_result

            # 5. Add document context prefix
            prefixed_chunks, chunk_types, chunk_heading_paths = self._add_context_prefixes(
                raw, typed_chunks, doc_summary,
            )

            # 6. Embed (dense + sparse)
            dense_vectors, sparse_vectors = await asyncio.gather(
                self._embed_dense(prefixed_chunks),
                self._embed_sparse_with_retry(prefixed_chunks),
            )
            if len(dense_vectors) != len(prefixed_chunks):
                raise ValueError(
                    f"Embedding count mismatch: {len(prefixed_chunks)} chunks but {len(dense_vectors)} vectors"
                )

            # META-09: Morphemes
            chunk_morphemes = self._extract_morphemes(typed_chunks)
            chunk_morphemes = self._append_date_author_tokens(chunk_morphemes, raw.title, raw.author)

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
                raw=raw, collection_name=collection_name, chunk_types=chunk_types,
                chunk_heading_paths=chunk_heading_paths, chunk_morphemes=chunk_morphemes,
                now_iso=now_iso, quality_tier=quality_tier, quality_score=quality_score,
                doc_type=doc_type, owner=owner, l1_category=l1_category,
                content_flags=content_flags, parse_result=parse_result,
            )
            items = [
                self._build_chunk_item(idx, ct, dv, sv, ctx=ctx)
                for idx, (ct, dv, sv) in enumerate(zip(prefixed_chunks, dense_vectors, sparse_vectors))
            ]

            title_item = await self._build_title_item(
                raw, collection_name, now_iso, quality_tier, quality_score,
                doc_type, owner, l1_category, content_flags, parse_result,
            )
            if title_item is not None:
                items.append(title_item)

            # 9-10. Store
            await asyncio.gather(
                self.vector_store.upsert_batch(collection_name, items),
                self.graph_store.upsert_document(
                    doc_id=raw.doc_id, title=raw.title, kb_id=collection_name,
                    source_type=raw.metadata.get("source_type", "file"),
                ),
            )

            # 11. Graph edges
            await self._create_graph_edges(raw, collection_name, owner=owner, l1_category=l1_category)

            # 12. Tree index (heading_path → Neo4j 트리)
            await self._run_tree_index_builder(raw, items, chunk_heading_paths, collection_name)

            # 13. Summary tree (RAPTOR식 클러스터링 → 요약 → Qdrant)
            await self._run_summary_tree_builder(
                raw, items, dense_vectors, prefixed_chunks, collection_name,
                chunk_heading_paths, now_iso,
            )

            # 14. Term extraction + synonym discovery + GraphRAG
            term_extraction_stats = await self._run_term_extraction(raw, typed_chunks, collection_name)
            synonym_discovery_stats = await self._run_synonym_discovery(raw, collection_name)
            graphrag_stats = await self._run_graphrag(raw, collection_name)

            # Register dedup hash
            if self.dedup_cache:
                try:
                    await self.dedup_cache.add(collection_name, _content_hash)
                except Exception as _dedup_err:  # noqa: BLE001
                    logger.warning("Dedup cache registration failed: %s", _dedup_err)

            # Build result metadata
            result_metadata = self._build_result_metadata(
                ctx, self.chunker.strategy_name, items, heading_map,
                graphrag_stats=graphrag_stats,
                term_extraction_stats=term_extraction_stats,
                synonym_discovery_stats=synonym_discovery_stats,
                dedup_result_info=dedup_result_info,
            )

            return IngestionResult.success_result(chunks_stored=len(items), metadata=result_metadata)

        except Exception as exc:
            logger.exception("Ingestion pipeline failed for doc_id=%s", raw.doc_id)
            return IngestionResult.failure_result(reason=str(exc), stage="pipeline")

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

        except Exception as e:  # noqa: BLE001
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
