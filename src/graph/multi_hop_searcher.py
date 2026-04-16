"""Multi-hop Searcher.

N-hop graph traversal from seed entities.
Adapted from oreo-ecosystem multi_hop_searcher.py for knowledge-local.

Features:
- N-hop related node discovery from a document
- Topic expert finder via AUTHORED/OWNED_BY relationships
- Shortest path between documents
- Similar document discovery via shared topics
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Neo4jClient
    from .repository import Neo4jGraphRepository

logger = logging.getLogger(__name__)


@dataclass
class RelatedNode:
    """Related node found via graph traversal."""

    id: str
    name: str
    type: str  # Document, Person, Team, Topic, System
    distance: int  # hop count
    relation_types: list[str]  # relationship path
    properties: dict[str, Any]
    relevance_score: float = 1.0


@dataclass
class Expert:
    """Topic expert."""

    name: str
    email: str | None
    document_count: int
    topics: list[str]
    departments: list[str]


@dataclass
class KnowledgePath:
    """Knowledge path between two documents."""

    from_doc_id: str
    to_doc_id: str
    path_length: int
    nodes: list[dict[str, Any]]
    relationships: list[str]


class MultiHopSearcher:
    """N-hop graph traversal searcher.

    Provides methods for multi-hop relationship exploration in Neo4j.
    """

    # Related node search (N-hop)
    SEARCH_RELATED_CYPHER = """
    MATCH path = (d:Document {id: $doc_id})-[*1..$max_hops]-(related)
    WHERE related:Document OR related:Person OR related:Team OR related:Topic OR related:System
    WITH related, length(path) as distance,
         [r in relationships(path) | type(r)] as relation_types
    RETURN DISTINCT
        related.id as id,
        related.name as name,
        labels(related)[0] as type,
        distance,
        relation_types,
        properties(related) as properties
    ORDER BY distance, related.name
    LIMIT $limit
    """

    # Topic expert finder
    FIND_EXPERTS_CYPHER = """
    MATCH (tp:Topic {name: $topic})<-[:COVERS]-(d:Document)-[:OWNED_BY|AUTHORED]->(p:Person)
    WITH p, count(DISTINCT d) as doc_count, collect(DISTINCT tp.name) as topics
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
    RETURN
        p.name as name,
        p.email as email,
        doc_count,
        topics,
        collect(DISTINCT t.name) as departments
    ORDER BY doc_count DESC
    LIMIT $limit
    """

    # Topic keyword search for expert finding
    FIND_EXPERTS_BY_KEYWORD_CYPHER = """
    MATCH (tp:Topic)
    WHERE tp.name CONTAINS $keyword OR tp.category CONTAINS $keyword
    WITH tp
    MATCH (tp)<-[:COVERS]-(d:Document)-[:OWNED_BY|AUTHORED]->(p:Person)
    WITH p, count(DISTINCT d) as doc_count, collect(DISTINCT tp.name) as topics
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
    RETURN
        p.name as name,
        p.email as email,
        doc_count,
        topics,
        collect(DISTINCT t.name) as departments
    ORDER BY doc_count DESC
    LIMIT $limit
    """

    # Shortest path between documents
    SHORTEST_PATH_CYPHER = """
    MATCH path = shortestPath(
        (d1:Document {id: $from_doc_id})-[*..5]-(d2:Document {id: $to_doc_id})
    )
    RETURN
        length(path) as path_length,
        [n in nodes(path) | {id: n.id, name: n.name, type: labels(n)[0]}] as nodes,
        [r in relationships(path) | type(r)] as relationships
    """

    # Document cluster (shared topics)
    DOCUMENT_CLUSTER_CYPHER = """
    MATCH (d:Document {id: $doc_id})-[:COVERS]->(tp:Topic)<-[:COVERS]-(related:Document)
    WHERE related.id <> $doc_id
    WITH related, collect(DISTINCT tp.name) as shared_topics, count(tp) as overlap_count
    RETURN
        related.id as id,
        related.title as title,
        related.kb_id as kb_id,
        shared_topics,
        overlap_count
    ORDER BY overlap_count DESC
    LIMIT $limit
    """

    def __init__(
        self,
        neo4j_client: "Neo4jClient | None" = None,
        graph_repository: "Neo4jGraphRepository | None" = None,
    ) -> None:
        """Initialize multi-hop searcher.

        Args:
            neo4j_client: Neo4j client (direct queries)
            graph_repository: Graph repository (preferred if available)
        """
        self.neo4j = neo4j_client
        self._graph_repository = graph_repository

    def _get_client(self) -> Any:
        """Get the query execution client."""
        if self._graph_repository is not None:
            return self._graph_repository
        return self.neo4j

    async def find_related(
        self,
        entity_names: list[str],
        max_hops: int = 2,
        max_results: int = 10,
        scope_kb_ids: list[str] | None = None,
    ) -> list[RelatedNode]:
        """Find related docs/entities from seed entity names.

        Uses fulltext search to find seed entities, then N-hop traversal.

        Args:
            entity_names: Seed entity names to start traversal from
            max_hops: Maximum hop count (default: 2)
            max_results: Maximum results (default: 10)
            scope_kb_ids: Optional KB ID filter

        Returns:
            Related nodes sorted by distance
        """
        if self._graph_repository is not None:
            try:
                from .lucene_utils import build_lucene_or_query
                lucene_query = build_lucene_or_query(entity_names)
                if not lucene_query:
                    return []

                urls = await self._graph_repository.find_related_chunks(
                    entity_names,
                    max_hops=max_hops,
                    max_results=max_results,
                    scope_kb_ids=scope_kb_ids,
                )
                # Convert URLs to RelatedNode format
                return [
                    RelatedNode(
                        id=url,
                        name=url,
                        type="Document",
                        distance=1,
                        relation_types=["RELATED"],
                        properties={"url": url},
                    )
                    for url in urls
                ]
            except Exception as e:
                logger.warning("find_related via repository failed: %s", e)
                return []

        if self.neo4j is None:
            return []

        # Fallback: search each entity name as a document ID.
        # 이전엔 직렬 loop — 5개 이름에 대해 neo4j.execute_query 를 순차 호출했고
        # 각 호출이 수백 ms → 전체 1~2초. asyncio.gather 로 한 번에 5개 병렬화
        # 해서 wall-clock latency 를 max(single_call) 로 축소.
        names = entity_names[:5]  # Limit to avoid expensive queries

        async def _query_for_name(name: str) -> list[dict[str, Any]]:
            try:
                return await self.neo4j.execute_query(
                    self.SEARCH_RELATED_CYPHER,
                    {"doc_id": name, "max_hops": max_hops, "limit": max_results},
                )
            except Exception as e:
                logger.warning("Search related for entity '%s' failed: %s", name, e)
                return []

        per_name_results = await asyncio.gather(*(_query_for_name(n) for n in names))

        all_related: list[RelatedNode] = []
        seen_ids: set[str] = set()
        for results in per_name_results:
            for record in results:
                node_id = record.get("id", "")
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
                distance = record.get("distance", 1)
                relevance = 1.0 / (1 + (distance - 1) * 0.3)
                all_related.append(
                    RelatedNode(
                        id=node_id,
                        name=record.get("name", ""),
                        type=record.get("type", "Unknown"),
                        distance=distance,
                        relation_types=record.get("relation_types", []),
                        properties=record.get("properties", {}),
                        relevance_score=relevance,
                    )
                )

        all_related.sort(key=lambda n: n.distance)
        return all_related[:max_results]

    async def find_experts(
        self,
        topic: str,
        _kb_id: str | None = None,
        limit: int = 10,
    ) -> list[Expert]:
        """Find experts for a topic via AUTHORED/OWNED_BY relationships.

        Args:
            topic: Topic name
            _kb_id: Optional KB ID filter (unused currently)
            limit: Maximum results (default: 10)

        Returns:
            Expert list sorted by document count
        """
        client = self._get_client()
        if client is None:
            return []

        try:
            # Try repository method first
            if self._graph_repository is not None:
                records = await self._graph_repository.find_experts(topic=topic, limit=limit)
                return [
                    Expert(
                        name=r.get("name", ""),
                        email=r.get("email"),
                        document_count=r.get("doc_count", 0),
                        topics=r.get("topics", []),
                        departments=r.get("departments", []),
                    )
                    for r in records
                ]

            # Direct query
            results = await self.neo4j.execute_query(
                self.FIND_EXPERTS_CYPHER,
                {"topic": topic, "limit": limit},
            )

            # Fallback to keyword search if no exact match
            if not results:
                results = await self.neo4j.execute_query(
                    self.FIND_EXPERTS_BY_KEYWORD_CYPHER,
                    {"keyword": topic, "limit": limit},
                )

            return [
                Expert(
                    name=record.get("name", ""),
                    email=record.get("email"),
                    document_count=record.get("doc_count", 0),
                    topics=record.get("topics", []),
                    departments=record.get("departments", []),
                )
                for record in results
            ]

        except Exception as e:
            logger.error("Failed to find experts for topic '%s': %s", topic, e)
            return []

    async def search_related(
        self,
        doc_id: str,
        max_hops: int = 3,
        limit: int = 50,
    ) -> list[RelatedNode]:
        """Search N-hop related nodes from a document.

        Args:
            doc_id: Starting document ID
            max_hops: Maximum hops (default: 3)
            limit: Maximum results (default: 50)

        Returns:
            Related node list
        """
        try:
            if self._graph_repository is not None:
                records = await self._graph_repository.search_related_nodes(
                    doc_id=doc_id, max_hops=max_hops, limit=limit,
                )
                return [
                    RelatedNode(
                        id=r.get("id", ""),
                        name=r.get("name", ""),
                        type=r.get("type", "Unknown"),
                        distance=r.get("distance", 1),
                        relation_types=r.get("relation_types", []),
                        properties=r.get("properties", {}),
                        relevance_score=1.0 / (1 + (r.get("distance", 1) - 1) * 0.3),
                    )
                    for r in records
                ]

            if self.neo4j is None:
                return []

            results = await self.neo4j.execute_query(
                self.SEARCH_RELATED_CYPHER,
                {"doc_id": doc_id, "max_hops": max_hops, "limit": limit},
            )

            return [
                RelatedNode(
                    id=record.get("id", ""),
                    name=record.get("name", ""),
                    type=record.get("type", "Unknown"),
                    distance=record.get("distance", 1),
                    relation_types=record.get("relation_types", []),
                    properties=record.get("properties", {}),
                    relevance_score=1.0 / (1 + (record.get("distance", 1) - 1) * 0.3),
                )
                for record in results
            ]

        except Exception as e:
            logger.error("Failed to search related nodes for doc %s: %s", doc_id, e)
            return []

    async def get_knowledge_path(
        self,
        from_doc_id: str,
        to_doc_id: str,
    ) -> KnowledgePath | None:
        """Find shortest path between two documents.

        Args:
            from_doc_id: Source document ID
            to_doc_id: Target document ID

        Returns:
            Knowledge path or None if no path exists
        """
        try:
            if self._graph_repository is not None:
                records = await self._graph_repository.get_knowledge_path(
                    source_id=from_doc_id, target_id=to_doc_id,
                )
                if not records:
                    return None
                record = records[0]
                return KnowledgePath(
                    from_doc_id=from_doc_id,
                    to_doc_id=to_doc_id,
                    path_length=record.get("path_length", 0),
                    nodes=record.get("nodes", []),
                    relationships=record.get("relationships", []),
                )

            if self.neo4j is None:
                return None

            results = await self.neo4j.execute_query(
                self.SHORTEST_PATH_CYPHER,
                {"from_doc_id": from_doc_id, "to_doc_id": to_doc_id},
            )

            if not results:
                return None

            record = results[0]
            return KnowledgePath(
                from_doc_id=from_doc_id,
                to_doc_id=to_doc_id,
                path_length=record.get("path_length", 0),
                nodes=record.get("nodes", []),
                relationships=record.get("relationships", []),
            )

        except Exception as e:
            logger.error("Failed to get knowledge path: %s", e)
            return None

    async def find_similar_documents(
        self,
        doc_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find documents sharing topics with the given document.

        Args:
            doc_id: Document ID
            limit: Maximum results (default: 10)

        Returns:
            Similar document list sorted by topic overlap count
        """
        try:
            if self._graph_repository is not None:
                return await self._graph_repository.find_similar_documents(doc_id=doc_id, limit=limit)

            if self.neo4j is None:
                return []

            results = await self.neo4j.execute_query(
                self.DOCUMENT_CLUSTER_CYPHER,
                {"doc_id": doc_id, "limit": limit},
            )

            return [
                {
                    "id": r.get("id", ""),
                    "title": r.get("title", ""),
                    "kb_id": r.get("kb_id", ""),
                    "shared_topics": r.get("shared_topics", []),
                    "overlap_count": r.get("overlap_count", 0),
                }
                for r in results
            ]

        except Exception as e:
            logger.error("Failed to find similar documents for %s: %s", doc_id, e)
            return []
