"""Neo4j Graph Repository (SSOT data access layer).

Unified repository for all Knowledge Graph read/write operations.
All Neo4j access should go through this class to ensure:
- Single MERGE key for Documents: ``{id: $doc_id}``
- SSOT label validation via ``node_registry``
- Single driver via injected ``Neo4jClient``

Created: 2026-03-09 (Knowledge Graph Refactoring Phase 2)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .node_registry import (
    NODE_LABELS,
    RELATION_LABELS,
    is_supported_node_label,
    is_supported_relation_label,
)
from .lucene_utils import build_lucene_or_query, sanitize_lucene

logger = logging.getLogger(__name__)


class Neo4jGraphRepository:
    """Unified data access layer for the Knowledge Graph.

    All write paths use ``MERGE (d:Document {id: $doc_id})`` as the
    single MERGE key to prevent duplicate Document nodes.
    """

    def __init__(self, neo4j_client: Any) -> None:
        self._client = neo4j_client

    async def execute_write(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """IGraphStore passthrough to the underlying Neo4j client.

        The ingestion pipeline talks to the graph via the
        :class:`IGraphStore` protocol, which requires an ``execute_write``
        method on the store itself. Historically this repository only
        exposed domain-level helpers and relied on callers accessing
        ``self._client.execute_write`` directly — the ingestion path
        was therefore hitting an ``AttributeError`` for every document
        and silently dropping base graph edges (CHILD_OF, BELONGS_TO,
        AUTHORED, etc.). This thin passthrough closes that gap.
        """
        return await self._client.execute_write(query, parameters or {})

    # -- Write Methods ----------------------------------------------------

    async def upsert_document(
        self,
        doc_id: str,
        *,
        title: str = "",
        kb_id: str = "",
        knowledge_id: str | None = None,
        status: str = "published",
        url: str | None = None,
        source_type: str | None = None,
        extra_properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Single-key Document MERGE."""
        cypher = """
        MERGE (d:Document {id: $doc_id})
        SET d.title = $title,
            d.kb_id = $kb_id,
            d.knowledge_id = $knowledge_id,
            d.status = $status,
            d.url = $url,
            d.source_type = $source_type,
            d.updated_at = datetime()
        """
        params: dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "kb_id": kb_id,
            "knowledge_id": knowledge_id,
            "status": status,
            "url": url,
            "source_type": source_type,
        }
        if extra_properties:
            cypher += "\nSET d += $extra_props"
            params["extra_props"] = extra_properties
        return await self._client.execute_write(cypher, params)

    async def upsert_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        name: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """SSOT-validated entity MERGE.

        Falls back to 'Entity' label if *entity_type* is not registered.
        """
        safe_type = self._resolve_node_type(entity_type)
        props = dict(properties or {})
        if name:
            props["name"] = name

        cypher = f"""
        MERGE (e:{safe_type} {{id: $entity_id}})
        SET e += $props, e.updated_at = datetime()
        """
        return await self._client.execute_write(
            cypher, {"entity_id": entity_id, "props": props}
        )

    async def create_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        *,
        source_label: str = "Document",
        target_label: str = "Document",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """SSOT-validated relationship MERGE."""
        safe_rel = self._resolve_relation_type(rel_type)
        safe_source = self._resolve_node_type(source_label)
        safe_target = self._resolve_node_type(target_label)
        props = properties or {}
        cypher = f"""
        MATCH (a:{safe_source} {{id: $source_id}})
        MATCH (b:{safe_target} {{id: $target_id}})
        MERGE (a)-[r:{safe_rel}]->(b)
        SET r += $props
        """
        return await self._client.execute_write(
            cypher,
            {"source_id": source_id, "target_id": target_id, "props": props},
        )

    async def batch_upsert_nodes(
        self,
        node_type: str,
        nodes: list[dict[str, Any]],
        *,
        batch_size: int = 500,
    ) -> list[dict[str, Any]]:
        """Batch node MERGE via ``Neo4jClient.execute_unwind_batch``."""
        safe_type = self._resolve_node_type(node_type)
        cypher = f"""
        UNWIND $nodes AS node
        MERGE (n:{safe_type} {{id: node.node_id}})
        SET n.title = node.title,
            n += node.properties
        """
        return await self._client.execute_unwind_batch(
            cypher, param_name="nodes", items=nodes, batch_size=batch_size,
        )

    async def batch_upsert_edges(
        self,
        rel_type: str,
        edges: list[dict[str, Any]],
        *,
        batch_size: int = 500,
    ) -> list[dict[str, Any]]:
        """Batch edge MERGE via ``Neo4jClient.execute_unwind_batch``."""
        safe_rel = self._resolve_relation_type(rel_type)
        cypher = f"""
        UNWIND $edges AS edge
        MATCH (a {{id: edge.source}})
        MATCH (b {{id: edge.target}})
        MERGE (a)-[r:{safe_rel}]->(b)
        SET r += edge.properties
        """
        return await self._client.execute_unwind_batch(
            cypher, param_name="edges", items=edges, batch_size=batch_size,
        )

    async def upsert_document_lineage(
        self,
        doc_id: str,
        *,
        kb_id: str,
        knowledge_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Document upsert with lineage properties + KnowledgeBase link.

        Uses the same single MERGE key ``{id: $doc_id}`` as ``upsert_document``.
        """
        prov = provenance or {}
        doc_result = await self._client.execute_write(
            """
            MERGE (d:Document {id: $doc_id})
            SET d.knowledge_id = $knowledge_id,
                d.kb_id = $kb_id,
                d.source_type = $source_type,
                d.source_url = $source_url,
                d.original_author = $original_author,
                d.content_hash = $content_hash,
                d.provenance_id = $provenance_id,
                d.ingestion_run_id = $ingestion_run_id,
                d.quality_score = $quality_score,
                d.crawled_at = $crawled_at,
                d.updated_at = datetime()
            """,
            {
                "doc_id": doc_id,
                "knowledge_id": knowledge_id,
                "kb_id": kb_id,
                "source_type": prov.get("source_type", ""),
                "source_url": prov.get("source_url", ""),
                "original_author": prov.get("original_author", ""),
                "content_hash": prov.get("content_hash", ""),
                "provenance_id": prov.get("provenance_id", ""),
                "ingestion_run_id": prov.get("ingestion_run_id", ""),
                "quality_score": prov.get("quality_score", 0.0),
                "crawled_at": prov.get("crawled_at", ""),
            },
        )
        # Ensure KnowledgeBase node + BELONGS_TO relationship
        await self._client.execute_write(
            """
            MERGE (kb:KnowledgeBase {id: $kb_id})
            WITH kb
            MATCH (d:Document {id: $doc_id})
            MERGE (d)-[:BELONGS_TO]->(kb)
            """,
            {"kb_id": kb_id, "doc_id": doc_id},
        )
        return doc_result

    # -- Read Methods -----------------------------------------------------

    # Fulltext index names
    _FULLTEXT_INDEX = "entity_name_title"       # Entity nodes (wiki-based)
    _FULLTEXT_INDEX_GRAPHRAG = "entity_search"  # __Entity__ nodes (GraphRAG-extracted)

    async def find_related_chunks(
        self,
        entity_names: list[str],
        *,
        max_hops: int = 2,
        max_results: int = 50,
        scope_kb_ids: list[str] | None = None,
    ) -> set[str]:
        """Fulltext search + N-hop traversal -> Document.url set.

        Compatibility: Uses size(relationships(path)) for Neo4j 5.x,
        conditional scope_filter to avoid parameter comparison issues,
        and WHERE guard on empty start_nodes.
        """
        lucene_query = build_lucene_or_query(entity_names)
        if not lucene_query:
            return set()

        safe_hops = max(1, min(int(max_hops), 5))
        scope_kb_ids = scope_kb_ids or []

        # Build scope filter conditionally (avoids $scope_size=0 driver compat issue)
        scope_filter = "AND d.kb_id IN $scope_kb_ids" if scope_kb_ids else ""

        cypher = f"""
        CALL db.index.fulltext.queryNodes('{self._FULLTEXT_INDEX}', $lucene_query)
        YIELD node AS e, score
        WHERE score > 0.3
        WITH COLLECT(DISTINCT e) AS start_nodes
        WHERE size(start_nodes) > 0

        UNWIND start_nodes AS start
        MATCH path = (start)-[*1..{safe_hops}]-(d:Document)
        WHERE (d.url IS NOT NULL OR d.title IS NOT NULL OR d.name IS NOT NULL)
          {scope_filter}
        WITH d, MIN(size(relationships(path))) AS hops
        ORDER BY hops ASC
        RETURN COALESCE(d.url, d.title, d.name) AS source_uri
        LIMIT $max_results
        """

        params: dict[str, Any] = {
            "lucene_query": lucene_query,
            "max_results": max_results,
        }
        if scope_kb_ids:
            params["scope_kb_ids"] = scope_kb_ids

        try:
            records = await self._client.execute_query(cypher, params)
            return {r["source_uri"] for r in records if r.get("source_uri")}
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Neo4j find_related_chunks failed (entities=%s): %s",
                entity_names[:3],
                e,
            )
            return set()

    # Relationship whitelist for entity fact queries -- sourced from node_registry SSOT.
    # Subset of RELATION_LABELS relevant for LLM prompt context.
    _FACT_RELATION_WHITELIST: list[str] = [
        "RESPONSIBLE_FOR", "CREATED_BY", "MODIFIED_BY",
        "MEMBER_OF", "MENTIONS", "COVERS", "OWNS",
        "BELONGS_TO", "NEXT_STEP", "HAS_PROCESS_STEP",
    ]

    async def search_entities(
        self,
        keywords: list[str],
        *,
        max_facts: int = 20,
    ) -> list[dict[str, Any]]:
        """Entity search across both wiki and GraphRAG nodes.

        Searches two fulltext indexes:
        1. entity_name_title: Entity nodes (wiki-based)
        2. entity_search: __Entity__ nodes (GraphRAG Store/Person/Process etc.)
        """
        lucene_query = build_lucene_or_query(keywords)
        if not lucene_query:
            # Fallback: CONTAINS search on id field
            keyword = " ".join(keywords)
            return await self._search_by_contains(keyword, max_facts)

        results: list[dict[str, Any]] = []

        # Search GraphRAG entities (__Entity__ nodes)
        graphrag_cypher = f"""
        CALL db.index.fulltext.queryNodes('{self._FULLTEXT_INDEX_GRAPHRAG}', $lucene_query)
        YIELD node, score WHERE score > 0.3
        WITH node, score, [l IN labels(node) WHERE l <> '__Entity__'][0] AS node_type
        OPTIONAL MATCH (node)-[r]-(connected)
        WITH node, score, node_type, r, connected
        RETURN node_type,
               COALESCE(node.name, node.id) AS name,
               node.id AS entity_id,
               score,
               type(r) AS rel_type,
               [l IN labels(connected) WHERE l <> '__Entity__'][0] AS connected_type,
               COALESCE(connected.name, connected.id) AS connected_name
        ORDER BY score DESC
        LIMIT $max_facts
        """
        try:
            results.extend(await self._client.execute_query(
                graphrag_cypher,
                {"lucene_query": lucene_query, "max_facts": max_facts},
            ))
        except Exception as e:  # noqa: BLE001
            logger.debug("GraphRAG entity search failed: %s", e)

        # Search wiki entities (Entity nodes)
        wiki_cypher = f"""
        CALL db.index.fulltext.queryNodes('{self._FULLTEXT_INDEX}', $lucene_query)
        YIELD node, score WHERE score > 0.5
        WITH node, score, labels(node)[0] AS node_type
        OPTIONAL MATCH (node)-[r]-(connected)
        WHERE type(r) IN $rel_whitelist
        RETURN node_type, node.name AS name,
               node.id AS entity_id,
               score,
               type(r) AS rel_type,
               labels(connected)[0] AS connected_type,
               COALESCE(connected.name, connected.id) AS connected_name
        ORDER BY score DESC
        LIMIT $max_facts
        """
        try:
            results.extend(await self._client.execute_query(
                wiki_cypher,
                {
                    "lucene_query": lucene_query,
                    "max_facts": max_facts,
                    "rel_whitelist": self._FACT_RELATION_WHITELIST,
                },
            ))
        except Exception as e:  # noqa: BLE001
            logger.debug("Wiki entity search failed: %s", e)

        return results[:max_facts]

    async def _search_by_contains(
        self, keyword: str, max_facts: int = 20,
    ) -> list[dict[str, Any]]:
        """Fallback CONTAINS search when fulltext query fails."""
        import unicodedata
        keyword_nfc = unicodedata.normalize("NFC", keyword)
        keyword_nfd = unicodedata.normalize("NFD", keyword)
        cypher = """
        MATCH (n)
        WHERE n.id CONTAINS $keyword_nfc OR n.name CONTAINS $keyword_nfc
           OR n.id CONTAINS $keyword_nfd OR n.name CONTAINS $keyword_nfd
        WITH n, [l IN labels(n) WHERE l <> '__Entity__'][0] AS node_type
        OPTIONAL MATCH (n)-[r]-(connected)
        RETURN node_type,
               COALESCE(n.name, n.id) AS name,
               n.id AS entity_id,
               1.0 AS score,
               type(r) AS rel_type,
               [l IN labels(connected) WHERE l <> '__Entity__'][0] AS connected_type,
               COALESCE(connected.name, connected.id) AS connected_name
        LIMIT $max_facts
        """
        try:
            return await self._client.execute_query(
                cypher, {"keyword_nfc": keyword_nfc, "keyword_nfd": keyword_nfd, "max_facts": max_facts},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("CONTAINS search failed: %s", e)
            return []

    async def find_experts(
        self,
        topic: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find experts for a topic.

        Three search paths:
        1. Person who OWNS/AUTHORED Documents matching the topic
        2. Person connected to GraphRAG entities matching the topic
           (Person → MANAGES/OWNS → Store/Process that matches)
        3. Person whose name appears in documents related to the topic
        """
        import unicodedata
        # macOS HFS+ stores filenames as NFD; normalize search term to match
        topic_nfc = unicodedata.normalize("NFC", topic)
        topic_nfd = unicodedata.normalize("NFD", topic)

        results: list[dict[str, Any]] = []

        # Path 1: Direct document ownership (title/id match)
        # Try both NFC and NFD forms for macOS filename compatibility
        cypher_docs = """
        MATCH (p:Person)-[r:OWNS|AUTHORED|MANAGES|RESPONSIBLE_FOR|MENTIONED_IN]->(d:Document)
        WHERE COALESCE(d.title, d.id, '') CONTAINS $topic_nfc
           OR COALESCE(d.title, d.id, '') CONTAINS $topic_nfd
        WITH p, count(DISTINCT d) AS doc_count,
             collect(DISTINCT COALESCE(d.title, d.id))[..5] AS related
        RETURN p.name AS name, p.id AS person_id,
               doc_count, related, 'document_owner' AS source
        ORDER BY doc_count DESC
        LIMIT $limit
        """
        try:
            results.extend(await self._client.execute_query(
                cypher_docs, {"topic_nfc": topic_nfc, "topic_nfd": topic_nfd, "limit": limit}
            ))
        except Exception as e:  # noqa: BLE001
            logger.debug("Owner search path 1 (document_owner) failed: %s", e)

        # Path 2: GraphRAG entity connection
        cypher_entity = """
        MATCH (entity:__Entity__)
        WHERE COALESCE(entity.id, entity.name, '') CONTAINS $topic_nfc
           OR COALESCE(entity.id, entity.name, '') CONTAINS $topic_nfd
        WITH entity
        OPTIONAL MATCH (entity)-[:EXTRACTED_FROM]->(d:Document)<-[:OWNS|AUTHORED]-(p:Person)
        WHERE p IS NOT NULL
        WITH p, count(DISTINCT entity) AS entity_count,
             collect(DISTINCT COALESCE(entity.id, entity.name))[..5] AS related
        WHERE p IS NOT NULL
        RETURN p.name AS name, p.id AS person_id,
               entity_count AS doc_count, related, 'entity_expert' AS source
        ORDER BY entity_count DESC
        LIMIT $limit
        """
        try:
            results.extend(await self._client.execute_query(
                cypher_entity, {"topic_nfc": topic_nfc, "topic_nfd": topic_nfd, "limit": limit}
            ))
        except Exception as e:  # noqa: BLE001
            logger.debug("Owner search path 2 (entity_expert) failed: %s", e)

        # Path 3: Person directly connected to matching entity (GraphRAG)
        cypher_direct = """
        MATCH (p:Person)-[r]-(entity:__Entity__)
        WHERE COALESCE(entity.id, entity.name, '') CONTAINS $topic_nfc
           OR COALESCE(entity.id, entity.name, '') CONTAINS $topic_nfd
        WITH p, type(r) AS rel, count(DISTINCT entity) AS entity_count,
             collect(DISTINCT COALESCE(entity.id, entity.name))[..5] AS related
        RETURN p.name AS name, p.id AS person_id,
               entity_count AS doc_count, related, 'direct_connection' AS source
        ORDER BY entity_count DESC
        LIMIT $limit
        """
        try:
            results.extend(await self._client.execute_query(
                cypher_direct, {"topic_nfc": topic_nfc, "topic_nfd": topic_nfd, "limit": limit}
            ))
        except Exception as e:  # noqa: BLE001
            logger.debug("Owner search path 3 (direct_connection) failed: %s", e)

        # Deduplicate by person name, merge counts
        merged: dict[str, dict] = {}
        for r in results:
            name = r.get("name", "")
            if not name:
                continue
            if name in merged:
                merged[name]["doc_count"] = merged[name].get("doc_count", 0) + r.get("doc_count", 0)
                existing_related = merged[name].get("related", [])
                new_related = r.get("related", [])
                merged[name]["related"] = list(set(existing_related + new_related))[:10]
            else:
                merged[name] = dict(r)

        return sorted(merged.values(), key=lambda x: x.get("doc_count", 0), reverse=True)[:limit]

    async def search_related_nodes(
        self,
        doc_id: str,
        *,
        max_hops: int = 3,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """N-hop related node search from a document."""
        safe_hops = max(1, min(int(max_hops), 5))
        cypher = f"""
        MATCH path = (d:Document {{id: $doc_id}})-[*1..{safe_hops}]-(related)
        WHERE related:Document OR related:Person OR related:Team
              OR related:Topic OR related:System
        WITH related, length(path) as distance,
             [r in relationships(path) | type(r)] as relation_types
        RETURN DISTINCT
            related.id as id, related.name as name,
            labels(related)[0] as type, distance,
            relation_types, properties(related) as properties
        ORDER BY distance, related.name
        LIMIT $limit
        """
        try:
            return await self._client.execute_query(
                cypher, {"doc_id": doc_id, "limit": limit}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j search_related_nodes failed: %s", e)
            return []

    async def get_entity_neighbors(
        self,
        entity_name: str,
        entity_type: str,
        *,
        max_hops: int = 1,
    ) -> list[dict[str, Any]]:
        """Entity neighbor lookup."""
        safe_hops = max(1, min(int(max_hops), 5))
        safe_type = self._resolve_node_type(entity_type)
        cypher = f"""
        MATCH (e:{safe_type} {{name: $entity_name}})-[*1..{safe_hops}]-(neighbor)
        RETURN DISTINCT neighbor.name AS name,
               labels(neighbor)[0] AS type,
               neighbor.id AS id
        LIMIT 100
        """
        try:
            return await self._client.execute_query(
                cypher, {"entity_name": entity_name}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j get_entity_neighbors failed: %s", e)
            return []

    async def get_knowledge_path(
        self,
        source_id: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        """Shortest path between two documents."""
        cypher = """
        MATCH path = shortestPath(
            (d1:Document {id: $from_id})-[*..5]-(d2:Document {id: $to_id})
        )
        RETURN length(path) as path_length,
               [n in nodes(path) | {id: n.id, name: n.name, type: labels(n)[0]}] as nodes,
               [r in relationships(path) | type(r)] as relationships
        """
        try:
            return await self._client.execute_query(
                cypher, {"from_id": source_id, "to_id": target_id}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j get_knowledge_path failed: %s", e)
            return []

    async def find_common_entities(
        self,
        doc_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Find entities shared by multiple documents."""
        if len(doc_ids) < 2:
            return []
        cypher = """
        MATCH (d1:Document {id: $doc1_id})-[:MENTIONS|COVERS]->(e)
              <-[:MENTIONS|COVERS]-(d2:Document {id: $doc2_id})
        RETURN e.name as name, labels(e)[0] as type,
               properties(e) as properties
        """
        try:
            return await self._client.execute_query(
                cypher, {"doc1_id": doc_ids[0], "doc2_id": doc_ids[1]}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j find_common_entities failed: %s", e)
            return []

    async def find_similar_documents(
        self,
        doc_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find documents sharing topics with *doc_id*."""
        cypher = """
        MATCH (d:Document {id: $doc_id})-[:COVERS]->(tp:Topic)
              <-[:COVERS]-(related:Document)
        WHERE related.id <> $doc_id
        WITH related, collect(DISTINCT tp.name) as shared_topics,
             count(tp) as overlap_count
        RETURN related.id as id, related.title as title,
               related.kb_id as kb_id, shared_topics, overlap_count
        ORDER BY overlap_count DESC
        LIMIT $limit
        """
        try:
            return await self._client.execute_query(
                cypher, {"doc_id": doc_id, "limit": limit}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j find_similar_documents failed: %s", e)
            return []

    # -- Process / Procedure (Phase 7) ------------------------------------

    async def query_process_chain(
        self,
        process_keyword: str,
        *,
        start_step: int | None = None,
        max_steps: int = 20,
    ) -> list[dict[str, Any]]:
        """Find a process by keyword and return ordered ProcessStep chain."""
        keyword = sanitize_lucene(process_keyword)
        if not keyword:
            return []

        first_step_filter = (
            f"WHERE first.step_number = {int(start_step)}" if start_step else ""
        )

        cypher = f"""
        CALL db.index.fulltext.queryNodes('entity_name_title', $keyword)
        YIELD node, score WHERE score > 0.3
        WITH node
        MATCH (node)-[:HAS_PROCESS_STEP]->(first:ProcessStep)
        {first_step_filter}
        WITH node, first
        ORDER BY first.step_number ASC
        LIMIT 1
        MATCH path = (first)-[:NEXT_STEP*0..]->(step:ProcessStep)
        RETURN step.step_number AS step_number,
               step.action AS action,
               step.id AS step_id,
               node.title AS process_name
        ORDER BY step.step_number
        LIMIT $max_steps
        """
        try:
            return await self._client.execute_query(
                cypher, {"keyword": keyword, "max_steps": max_steps}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j query_process_chain failed: %s", e)
            return []

    async def find_step_context(
        self,
        step_keyword: str,
        *,
        context_window: int = 2,
    ) -> dict[str, Any]:
        """Find a step by keyword and return surrounding steps."""
        keyword = sanitize_lucene(step_keyword)
        if not keyword:
            return {}

        safe_window = max(1, min(int(context_window), 5))
        cypher = f"""
        CALL db.index.fulltext.queryNodes('entity_name_title', $keyword)
        YIELD node, score WHERE score > 0.3
        WITH node
        WHERE 'ProcessStep' IN labels(node)
        WITH node LIMIT 1
        OPTIONAL MATCH (prev)-[:NEXT_STEP*1..{safe_window}]->(node)
        WHERE prev:ProcessStep
        WITH node, collect(DISTINCT prev) AS prev_steps
        OPTIONAL MATCH (node)-[:NEXT_STEP*1..{safe_window}]->(nxt)
        WHERE nxt:ProcessStep
        RETURN node.step_number AS step_number,
               node.action AS action,
               node.id AS step_id,
               [p IN prev_steps | {{step_number: p.step_number, action: p.action}}] AS before,
               collect(DISTINCT {{step_number: nxt.step_number, action: nxt.action}}) AS after
        """
        try:
            results = await self._client.execute_query(
                cypher, {"keyword": keyword}
            )
            return results[0] if results else {}
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j find_step_context failed: %s", e)
            return {}

    # -- Tree Index (heading_path 기반 문서 구조 트리) ----------------------

    async def find_tree_siblings_batch(
        self,
        chunk_ids: list[str],
        *,
        window: int = 2,
    ) -> dict[str, list[dict[str, Any]]]:
        """여러 chunk_id에 대한 형제 청크 일괄 조회."""
        cypher = """
        UNWIND $chunk_ids AS cid
        MATCH (tp:TreePage {chunk_id: cid})<-[:HAS_TREE_PAGE]-(ts:TreeSection)
        MATCH (ts)-[:HAS_TREE_PAGE]->(sibling:TreePage)
        WHERE sibling.chunk_index >= tp.chunk_index - $window
          AND sibling.chunk_index <= tp.chunk_index + $window
          AND sibling.chunk_id <> cid
        RETURN cid AS source_chunk_id,
               sibling.chunk_id AS chunk_id,
               sibling.chunk_index AS chunk_index,
               ts.title AS section_title,
               ts.full_path AS section_path
        ORDER BY cid, sibling.chunk_index
        """
        try:
            results = await self._client.execute_query(
                cypher, {"chunk_ids": chunk_ids, "window": window},
            )
            grouped: dict[str, list[dict[str, Any]]] = {}
            for r in results:
                source = r.pop("source_chunk_id", "")
                grouped.setdefault(source, []).append(r)
            return grouped
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j find_tree_siblings_batch failed: %s", e)
            return {}

    async def search_section_titles(
        self,
        query: str,
        *,
        kb_id: str | None = None,
        limit: int = 10,
        min_score: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Neo4j fulltext 인덱스로 섹션 제목 검색 → 해당 청크 ID 반환."""
        kb_filter = "AND ts.kb_id = $kb_id" if kb_id else ""
        cypher = f"""
        CALL db.index.fulltext.queryNodes("tree_section_title_ft", $query)
        YIELD node, score
        WHERE score > $min_score {kb_filter}
        WITH node AS ts, score
        MATCH (ts)-[:HAS_TREE_PAGE]->(tp:TreePage)
        RETURN tp.chunk_id AS chunk_id,
               ts.title AS section_title,
               ts.full_path AS section_path,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        params: dict[str, Any] = {"query": query, "min_score": min_score, "limit": limit}
        if kb_id:
            params["kb_id"] = kb_id
        try:
            return await self._client.execute_query(cypher, params)
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j search_section_titles failed: %s", e)
            return []

    async def get_chunk_section_paths_batch(
        self,
        chunk_ids: list[str],
    ) -> dict[str, str]:
        """여러 chunk_id의 섹션 경로 일괄 조회."""
        cypher = """
        UNWIND $chunk_ids AS cid
        MATCH (tp:TreePage {chunk_id: cid})<-[:HAS_TREE_PAGE]-(ts:TreeSection)
        RETURN cid AS chunk_id, ts.full_path AS section_path
        """
        try:
            results = await self._client.execute_query(cypher, {"chunk_ids": chunk_ids})
            return {r["chunk_id"]: r["section_path"] for r in results}
        except Exception:  # noqa: BLE001
            return {}

    # -- Stats / Health ---------------------------------------------------

    async def get_entity_count(self) -> int:
        """Count Person + System + Topic nodes."""
        try:
            results = await self._client.execute_query(
                "MATCH (n) WHERE n:Person OR n:System OR n:Topic "
                "RETURN count(n) AS count"
            )
            return results[0]["count"] if results else 0
        except Exception:  # noqa: BLE001
            return 0

    async def get_document_count(self) -> int:
        """Count Document nodes."""
        try:
            results = await self._client.execute_query(
                "MATCH (d:Document) RETURN count(d) AS count"
            )
            return results[0]["count"] if results else 0
        except Exception:  # noqa: BLE001
            return 0

    async def get_stats(self) -> dict[str, Any]:
        """Graph statistics (node/edge type counts)."""
        try:
            node_results = await self._client.execute_query(
                "MATCH (n) RETURN labels(n)[0] as label, count(n) as count "
                "ORDER BY count DESC"
            )
            edge_results = await self._client.execute_query(
                "MATCH ()-[r]->() RETURN type(r) as type, count(r) as count "
                "ORDER BY count DESC"
            )
            return {
                "node_types": {r["label"]: r["count"] for r in node_results},
                "edge_types": {r["type"]: r["count"] for r in edge_results},
            }
        except Exception:  # noqa: BLE001
            return {"node_types": {}, "edge_types": {}}

    async def health_check(self) -> bool:
        """Delegate to Neo4jClient."""
        return await self._client.health_check()

    # -- Private Helpers --------------------------------------------------

    def _resolve_node_type(self, raw_type: str) -> str:
        """Validate and return a SSOT-registered node label."""
        if is_supported_node_label(raw_type):
            return raw_type
        # Case-insensitive lookup
        for label in NODE_LABELS:
            if label.lower() == raw_type.lower():
                return label
        logger.warning("Unsupported node type %r, falling back to Entity", raw_type)
        return "Entity"

    def _resolve_relation_type(self, raw_type: str) -> str:
        """Validate and return a SSOT-registered relation type."""
        if is_supported_relation_label(raw_type):
            return raw_type
        for label in RELATION_LABELS:
            if label.lower() == raw_type.lower():
                return label
        logger.warning(
            "Unsupported relation type %r, falling back to RELATED_TO", raw_type
        )
        return "RELATED_TO"


class NoOpNeo4jGraphRepository:
    """Test / development no-op implementation."""

    # -- shared no-op returns (avoids S4144 duplicate implementation) --
    _EMPTY_UPSERT: dict[str, int] = {"nodes_created": 0, "properties_set": 0}
    _EMPTY_REL: dict[str, int] = {"nodes_created": 0, "relationships_created": 0}

    async def _noop_upsert(self) -> dict[str, int]:
        await asyncio.sleep(0)
        return dict(self._EMPTY_UPSERT)

    async def _noop_rel(self) -> dict[str, int]:
        await asyncio.sleep(0)
        return dict(self._EMPTY_REL)

    async def _noop_list(self) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return []

    async def upsert_document(self, _doc_id: str, **_kw: Any) -> dict[str, int]:
        return await self._noop_upsert()

    async def upsert_entity(self, _entity_type: str, _entity_id: str, **_kw: Any) -> dict[str, int]:
        return await self._noop_upsert()

    async def create_relationship(self, _source_id: str, _target_id: str, _rel_type: str, **_kw: Any) -> dict[str, int]:
        return await self._noop_rel()

    async def batch_upsert_nodes(self, _node_type: str, _nodes: list[dict[str, Any]], **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def batch_upsert_edges(self, _rel_type: str, _edges: list[dict[str, Any]], **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def upsert_document_lineage(self, _doc_id: str, **_kw: Any) -> dict[str, int]:
        return await self._noop_upsert()

    async def find_related_chunks(self, _entity_names: list[str], **_kw: Any) -> set[str]:
        await asyncio.sleep(0)
        return set()

    async def search_entities(self, _keywords: list[str], **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def find_experts(self, _topic: str, **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def search_related_nodes(self, _doc_id: str, **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def get_entity_neighbors(self, _entity_name: str, _entity_type: str, **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def get_knowledge_path(self, _source_id: str, _target_id: str) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def find_common_entities(self, _doc_ids: list[str]) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def find_similar_documents(self, _doc_id: str, **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def query_process_chain(self, _process_keyword: str, **_kw: Any) -> list[dict[str, Any]]:
        return await self._noop_list()

    async def find_step_context(self, _step_keyword: str, **_kw: Any) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {}

    async def _noop_zero(self) -> int:
        await asyncio.sleep(0)
        return 0

    async def get_entity_count(self) -> int:
        return await self._noop_zero()

    async def get_document_count(self) -> int:
        return await self._noop_zero()

    async def get_stats(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"node_types": {}, "edge_types": {}}

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True
