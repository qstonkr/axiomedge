"""Ingestion pipeline — graph edge creation, GraphRAG, tree/summary builders.

Extracted from ingestion.py for module size management.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.models import RawDocument
from src.stores.neo4j.errors import NEO4J_FAILURE

from .ocr_corrector import clean_chunk_text as _clean_chunk_text

logger = logging.getLogger(__name__)


async def create_graph_edges(
    raw: RawDocument,
    _collection_name: str,
    graph_store: Any,
    extract_cross_references_fn: Any,
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
            await graph_store.execute_write(
                "MERGE (child:Document {id: $child_id}) "
                "MERGE (parent:Document {id: $parent_id}) "
                "MERGE (child)-[:CHILD_OF]->(parent)",
                {"child_id": raw.doc_id, "parent_id": parent_id},
            )

        # GRAPH-04: Person AUTHORED Document
        if raw.author:
            await graph_store.execute_write(
                "MERGE (p:Person {name: $author}) "
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (p)-[:AUTHORED]->(d)",
                {"author": raw.author, "doc_id": raw.doc_id},
            )

        # GRAPH-06: Document BELONGS_TO Space
        space_key = raw.metadata.get("space_key")
        if space_key:
            space_name = raw.metadata.get("space_name", space_key)
            await graph_store.execute_write(
                "MERGE (s:Space {key: $key}) SET s.name = $name "
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (d)-[:BELONGS_TO]->(s)",
                {"key": space_key, "name": space_name, "doc_id": raw.doc_id},
            )

        # GRAPH-01: Document cross-references (REFERENCES) - batched via UNWIND
        cross_refs = extract_cross_references_fn(raw.content)
        if cross_refs:
            ref_params = [
                {"tgt_uri": link_url, "link_text": link_text}
                for link_text, link_url in cross_refs
            ]
            await graph_store.execute_write(
                "UNWIND $refs AS ref "
                "MERGE (src:Document {id: $src_id}) "
                "MERGE (tgt:Document {uri: ref.tgt_uri}) "
                "ON CREATE SET tgt.title = ref.link_text "
                "MERGE (src)-[:REFERENCES {link_text: ref.link_text}]->(tgt)",
                {"src_id": raw.doc_id, "refs": ref_params},
            )

        # GRAPH-07: Person OWNS Document (owner/담당자)
        if owner:
            await graph_store.execute_write(
                "MERGE (p:Person {name: $owner}) "
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (p)-[:OWNS]->(d)",
                {"owner": owner, "doc_id": raw.doc_id},
            )

        # GRAPH-08: Document CATEGORIZED_AS Category (L1)
        if l1_category and l1_category != "기타":
            await graph_store.execute_write(
                "MERGE (c:Category {name: $category}) "
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (d)-[:CATEGORIZED_AS]->(c)",
                {"category": l1_category, "doc_id": raw.doc_id},
            )

    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning(
            "Graph edge creation failed for doc_id=%s: %s",
            raw.doc_id, e,
        )


async def run_graphrag(
    raw: RawDocument,
    collection_name: str,
    enable_graphrag: bool,
    graphrag_extractor: Any | None,
    legal_graph_extractor: Any | None,
) -> dict[str, Any]:
    """Run optional graph extraction.

    Legal documents are routed to the rule-based LegalGraphExtractor.
    """
    if raw.metadata.get("_is_legal_document") and legal_graph_extractor is not None:
        return await _run_legal_graph_extraction(raw, collection_name, legal_graph_extractor)

    stats: dict[str, Any] = {}
    if not enable_graphrag or graphrag_extractor is None:
        return stats

    try:
        _graphrag_content = _clean_chunk_text(raw.content)
        # Phase 2: source_type 전파 → resolver 가 D-layer (connector default) 선택.
        # Schema 는 한 번만 resolve 해서 extract + save 양쪽에 재사용.
        # source_type 은 RawDocument.metadata 에 connector 가 채워둠 (RawDocument
        # 자체에는 source_type 필드가 없음). 누락 시 빈 문자열 → resolver 가
        # default schema 사용.
        _source_type = raw.metadata.get("source_type", "") if raw.metadata else ""
        from src.pipelines.graphrag.schema_resolver import SchemaResolver
        resolved_schema = SchemaResolver.resolve(
            kb_id=collection_name, source_type=_source_type,
        )
        extraction_result = await asyncio.to_thread(
            lambda: graphrag_extractor.extract(
                document=_graphrag_content,
                source_title=raw.title,
                source_page_id=raw.doc_id,
                source_updated_at=raw.updated_at.isoformat() if raw.updated_at else None,
                kb_id=collection_name,
                source_type=_source_type,
                schema=resolved_schema,
            )
        )
        if extraction_result.node_count > 0 or extraction_result.relationship_count > 0:
            save_stats = await asyncio.to_thread(
                graphrag_extractor.save_to_neo4j, extraction_result, resolved_schema,
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
    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning("GraphRAG extraction failed for doc_id=%s: %s", raw.doc_id, e)
        stats = {"error": str(e)}
    return stats


async def _run_legal_graph_extraction(
    raw: RawDocument, collection_name: str, legal_graph_extractor: Any,
) -> dict[str, Any]:
    """Run the rule-based legal graph extractor for a single document."""
    stats: dict[str, Any] = {}
    try:
        extraction_result = await legal_graph_extractor.extract_from_document(
            raw, kb_id=collection_name,
        )
        if extraction_result.node_count == 0 and extraction_result.relationship_count == 0:
            return stats
        save_stats = await asyncio.to_thread(
            legal_graph_extractor.save_to_neo4j, extraction_result,
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
    except (*NEO4J_FAILURE, ImportError) as e:
        logger.warning(
            "Legal graph extraction failed for doc_id=%s: %s", raw.doc_id, e,
        )
        stats = {"error": str(e), "extractor": "legal_rule_based"}
    return stats


async def run_tree_index_builder(
    raw: RawDocument,
    items: list[dict[str, Any]],
    chunk_heading_paths: list[str],
    collection_name: str,
    graph_store: Any,
) -> None:
    """Stage 12: heading_path -> Neo4j tree (failure does not block ingestion)."""
    from src.config import get_settings
    if not get_settings().tree_index.enabled:
        return
    if not graph_store:
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
        await persist_tree_to_neo4j(graph_store, tree_data)
    except (*NEO4J_FAILURE, ImportError) as exc:
        logger.warning("Tree index build failed for doc_id=%s: %s", raw.doc_id, exc)


async def run_summary_tree_builder(
    raw: RawDocument,
    items: list[dict[str, Any]],
    dense_vectors: list[list[float]],
    prefixed_chunks: list[str],
    collection_name: str,
    chunk_heading_paths: list[str],
    now_iso: str,
    vector_store: Any,
    embedding_provider: Any | None,
    llm_client: Any | None,
) -> None:
    """Stage 13: RAPTOR-style summary tree (failure does not block ingestion)."""
    from src.config import get_settings
    ts = get_settings().tree_index
    if not ts.enabled or not ts.summary_enabled:
        return
    if not embedding_provider or not llm_client:
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
            body_chunks, embedding_provider, llm_client,
            max_layers=ts.summary_max_layers,
            min_chunks=ts.summary_cluster_min_chunks,
            use_umap=True,
            umap_dim=ts.summary_umap_dim,
        )

        if not summaries:
            return

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
                    "source_chunk_ids": s["source_chunk_ids"][:10],
                    "kb_id": collection_name,
                    "ingested_at": now_iso,
                },
                "point_id": str_to_uuid(point_id_str),
            })
        await vector_store.upsert_batch(collection_name, summary_items)
        logger.info(
            "Summary tree stored: doc_id=%s, summaries=%d",
            raw.doc_id, len(summary_items),
        )
    except (*NEO4J_FAILURE, ImportError) as exc:
        logger.warning("Summary tree build failed for doc_id=%s: %s", raw.doc_id, exc)
