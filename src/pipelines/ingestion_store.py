"""Ingestion pipeline — chunk/title item building and result metadata.

Extracted from ingestion.py for module size management.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.core.models import RawDocument
from .document_parser import ParseResult
from .qdrant_utils import str_to_uuid, truncate_content
from .quality_processor import QualityTier

logger = logging.getLogger(__name__)


@dataclass
class ChunkContext:
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


def build_chunk_item(
    idx: int,
    chunk_text: str,
    dense_vec: list[float],
    sparse_vec: Any,
    *,
    ctx: ChunkContext,
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


async def build_title_item(
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
    embed_dense_fn: Any,
    sparse_embedder: Any,
) -> dict[str, Any] | None:
    """Build the title-only vector item (VEC-01). Returns None if no title."""
    title_text = raw.title or ""
    labels = raw.metadata.get("labels", [])
    if labels:
        title_text += f" {' '.join(labels)}"
    if not title_text.strip():
        return None

    title_dense, title_sparse = await asyncio.gather(
        embed_dense_fn([title_text]),
        sparse_embedder.embed_sparse([title_text]),
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


def build_result_metadata(
    ctx: ChunkContext, chunker_strategy: str, items: list,
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
