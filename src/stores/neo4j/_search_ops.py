# pyright: reportAttributeAccessIssue=false
"""Neo4j entity search operations — extracted from _read_ops.py.

Contains: find_related_chunks, search_entities, _search_by_contains, find_experts.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Neo4jClient

from .lucene_utils import build_lucene_or_query

logger = logging.getLogger(__name__)


class SearchOpsMixin:
    """Entity search methods for Neo4jGraphRepository.

    Requires ``self._client`` (Neo4jClient).
    """

    _client: Neo4jClient

    # Fulltext index names
    _FULLTEXT_INDEX = "entity_name_title"       # Entity nodes (wiki-based)
    _FULLTEXT_INDEX_GRAPHRAG = "entity_search"  # __Entity__ nodes (GraphRAG-extracted)

    # Relationship whitelist for entity fact queries -- sourced from node_registry SSOT.
    _FACT_RELATION_WHITELIST: list[str] = [
        "RESPONSIBLE_FOR", "CREATED_BY", "MODIFIED_BY",
        "MEMBER_OF", "MENTIONS", "COVERS", "OWNS",
        "BELONGS_TO", "NEXT_STEP", "HAS_PROCESS_STEP",
    ]

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "Neo4j find_related_chunks failed (entities=%s): %s",
                entity_names[:3],
                e,
            )
            return set()

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
            keyword = " ".join(keywords)
            return await self._search_by_contains(keyword, max_facts)

        results: list[dict[str, Any]] = []

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("GraphRAG entity search failed: %s", e)

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        3. Person whose name appears in documents related to the topic
        """
        import unicodedata
        topic_nfc = unicodedata.normalize("NFC", topic)
        topic_nfd = unicodedata.normalize("NFD", topic)

        results: list[dict[str, Any]] = []

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Owner search path 1 (document_owner) failed: %s", e)

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Owner search path 2 (entity_expert) failed: %s", e)

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Owner search path 3 (direct_connection) failed: %s", e)

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
