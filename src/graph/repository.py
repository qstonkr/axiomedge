"""Neo4j Graph Repository (SSOT data access layer).

Unified repository for all Knowledge Graph read/write operations.
All Neo4j access should go through this class to ensure:
- Single MERGE key for Documents: ``{id: $doc_id}``
- SSOT label validation via ``node_registry``
- Single driver via injected ``Neo4jClient``

Created: 2026-03-09 (Knowledge Graph Refactoring Phase 2)
"""

from __future__ import annotations

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

    # Fulltext index name -- must match graph_schema.py apply_schema()
    _FULLTEXT_INDEX = "entity_name_title"

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
        WHERE (d.url IS NOT NULL OR d.title IS NOT NULL)
          {scope_filter}
        WITH d, MIN(size(relationships(path))) AS hops
        ORDER BY hops ASC
        RETURN COALESCE(d.url, d.title) AS source_uri
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
        except Exception as e:
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
        """Entity search returning structured facts for LLM prompts."""
        lucene_query = build_lucene_or_query(keywords)
        if not lucene_query:
            return []

        cypher = f"""
        CALL db.index.fulltext.queryNodes('{self._FULLTEXT_INDEX}', $lucene_query)
        YIELD node, score WHERE score > 0.5
        WITH node, labels(node)[0] AS node_type
        OPTIONAL MATCH (node)-[r]-(connected)
        WHERE type(r) IN $rel_whitelist
        RETURN node_type, node.name AS name,
               node.email AS email,
               type(r) AS rel_type,
               labels(connected)[0] AS connected_type,
               connected.name AS connected_name
        LIMIT $max_facts
        """
        try:
            return await self._client.execute_query(
                cypher,
                {
                    "lucene_query": lucene_query,
                    "max_facts": max_facts,
                    "rel_whitelist": self._FACT_RELATION_WHITELIST,
                },
            )
        except Exception as e:
            logger.warning("Neo4j search_entities failed: %s", e)
            return []

    async def find_experts(
        self,
        topic: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find experts for a topic via COVERS relationship."""
        cypher = """
        MATCH (tp:Topic {name: $topic})<-[:COVERS]-(d:Document)-[:OWNED_BY]->(p:Person)
        WITH p, count(DISTINCT d) as doc_count, collect(DISTINCT tp.name) as topics
        OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
        RETURN p.name as name, p.email as email, doc_count,
               topics, collect(DISTINCT t.name) as departments
        ORDER BY doc_count DESC
        LIMIT $limit
        """
        try:
            return await self._client.execute_query(
                cypher, {"topic": topic, "limit": limit}
            )
        except Exception as e:
            logger.warning("Neo4j find_experts failed: %s", e)
            return []

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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
            logger.warning("Neo4j find_step_context failed: %s", e)
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
        except Exception:
            return 0

    async def get_document_count(self) -> int:
        """Count Document nodes."""
        try:
            results = await self._client.execute_query(
                "MATCH (d:Document) RETURN count(d) AS count"
            )
            return results[0]["count"] if results else 0
        except Exception:
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
        except Exception:
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

    async def upsert_document(self, doc_id: str, **kw: Any) -> dict[str, int]:
        return {"nodes_created": 0, "properties_set": 0}

    async def upsert_entity(self, entity_type: str, entity_id: str, **kw: Any) -> dict[str, int]:
        return {"nodes_created": 0, "properties_set": 0}

    async def create_relationship(self, source_id: str, target_id: str, rel_type: str, **kw: Any) -> dict[str, int]:
        return {"nodes_created": 0, "relationships_created": 0}

    async def batch_upsert_nodes(self, node_type: str, nodes: list[dict[str, Any]], **kw: Any) -> list[dict[str, Any]]:
        return []

    async def batch_upsert_edges(self, rel_type: str, edges: list[dict[str, Any]], **kw: Any) -> list[dict[str, Any]]:
        return []

    async def upsert_document_lineage(self, doc_id: str, **kw: Any) -> dict[str, int]:
        return {"nodes_created": 0, "properties_set": 0}

    async def find_related_chunks(self, entity_names: list[str], **kw: Any) -> set[str]:
        return set()

    async def search_entities(self, keywords: list[str], **kw: Any) -> list[dict[str, Any]]:
        return []

    async def find_experts(self, topic: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    async def search_related_nodes(self, doc_id: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    async def get_entity_neighbors(self, entity_name: str, entity_type: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    async def get_knowledge_path(self, source_id: str, target_id: str) -> list[dict[str, Any]]:
        return []

    async def find_common_entities(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        return []

    async def find_similar_documents(self, doc_id: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    async def query_process_chain(self, process_keyword: str, **kw: Any) -> list[dict[str, Any]]:
        return []

    async def find_step_context(self, step_keyword: str, **kw: Any) -> dict[str, Any]:
        return {}

    async def get_entity_count(self) -> int:
        return 0

    async def get_document_count(self) -> int:
        return 0

    async def get_stats(self) -> dict[str, Any]:
        return {"node_types": {}, "edge_types": {}}

    async def health_check(self) -> bool:
        return True
