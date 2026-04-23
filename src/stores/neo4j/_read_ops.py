# pyright: reportAttributeAccessIssue=false
"""Neo4j read operations — graph traversal, process, tree index, stats.

Extracted from repository.py. Entity search methods are in ``_search_ops.py``.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Neo4jClient

from .lucene_utils import sanitize_lucene

logger = logging.getLogger(__name__)


class ReadOpsMixin:
    """Read/traversal/stats methods for Neo4jGraphRepository.

    Requires ``self._client`` (Neo4jClient) and ``_resolve_node_type``.
    """

    _client: Neo4jClient

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Neo4j find_step_context failed: %s", e)
            return {}

    # -- Tree Index (heading_path) ----------------------------------------

    async def find_tree_siblings_batch(
        self,
        chunk_ids: list[str],
        *,
        window: int = 2,
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch sibling chunk lookup."""
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        """Fulltext section title search -> chunk IDs.

        ``WHERE`` 는 ``WITH`` 뒤에 둬야 ``ts`` alias 가 scope 에 존재한다.
        과거 구현은 ``WHERE ... AND ts.kb_id = $kb_id`` 를 YIELD 직후에 둬서
        ``kb_id`` 지정 시 ``CypherSyntaxError: Variable 'ts' not defined``
        로 검색이 전부 실패했다 (전역 질의는 kb_filter 가 빈 문자열이라 운
        좋게 통과).
        """
        kb_filter = "AND ts.kb_id = $kb_id" if kb_id else ""
        cypher = f"""
        CALL db.index.fulltext.queryNodes("tree_section_title_ft", $query)
        YIELD node, score
        WITH node AS ts, score
        WHERE score > $min_score {kb_filter}
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
        except Exception as e:  # noqa: BLE001 — neo4j.exceptions.Neo4jError 계열 (CypherSyntaxError 등) 을 소화 후 빈 결과 반환. 과거 tuple 이 Neo4jError 를 놓쳐 검색 전체 500 가던 갭 수정.
            logger.warning("Neo4j search_section_titles failed: %s", e, exc_info=True)
            return []

    async def get_chunk_section_paths_batch(
        self,
        chunk_ids: list[str],
    ) -> dict[str, str]:
        """Batch section path lookup."""
        cypher = """
        UNWIND $chunk_ids AS cid
        MATCH (tp:TreePage {chunk_id: cid})<-[:HAS_TREE_PAGE]-(ts:TreeSection)
        RETURN cid AS chunk_id, ts.full_path AS section_path
        """
        try:
            results = await self._client.execute_query(cypher, {"chunk_ids": chunk_ids})
            return {r["chunk_id"]: r["section_path"] for r in results}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return 0

    async def get_document_count(self) -> int:
        """Count Document nodes."""
        try:
            results = await self._client.execute_query(
                "MATCH (d:Document) RETURN count(d) AS count"
            )
            return results[0]["count"] if results else 0
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return {"node_types": {}, "edge_types": {}}

    async def health_check(self) -> bool:
        """Delegate to Neo4jClient."""
        return await self._client.health_check()
