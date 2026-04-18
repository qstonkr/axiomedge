# pyright: reportAttributeAccessIssue=false
"""Neo4j write operations — extracted from repository.py.

Contains: upsert_document, upsert_entity, create_relationship,
batch_upsert_nodes, batch_upsert_edges, upsert_document_lineage.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Neo4jClient

logger = logging.getLogger(__name__)


class WriteOpsMixin:
    """Write methods for Neo4jGraphRepository.

    Requires ``self._client`` (Neo4jClient) and helper methods
    ``_resolve_node_type`` / ``_resolve_relation_type``.
    """

    _client: Neo4jClient

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
