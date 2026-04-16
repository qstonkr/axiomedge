"""Neo4j Knowledge Graph Loader.

지식 그래프를 Neo4j에 로드하는 모듈.
GraphRAG 활성화를 위해 사용됨.

Extracted from oreo-ecosystem neo4j_loader.py.
- Removed oreo-specific imports (feature flags, graph_node_registry, hub_search_dependencies)
- Removed repository facade path (feature-flag gated)
- Kept batch upsert logic, fulltext index creation, Cypher injection prevention
- Uses direct neo4j driver

Updated: 2026-02-09 (Knowledge Graph Enhancement)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

# 안전한 라벨/관계 타입 패턴 (알파벳, 숫자, 언더스코어만 허용)
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z]\w*$")

# 허용된 노드/관계 타입 (화이트리스트)
ALLOWED_NODE_TYPES = frozenset([
    "Person", "Team", "System", "Document", "Entity",
    "Topic", "Term", "KB", "Attachment", "ProcessStep",
])

ALLOWED_RELATION_TYPES = frozenset([
    "MEMBER_OF", "MANAGES", "OWNS", "RESPONSIBLE_FOR",
    "PARTICIPATES_IN", "DEFINES", "IMPLEMENTS", "PART_OF",
    "RELATED_TO", "EXTRACTED_FROM", "HAS_CHUNK", "BELONGS_TO",
    "REFERENCES", "DEPENDS_ON", "CONTAINS",
])


@dataclass
class Neo4jConfig:
    """Neo4j 연결 설정."""

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    database: str = "neo4j"


class Neo4jKnowledgeLoader:
    """Neo4j 지식 그래프 로더."""

    def __init__(self, config: Neo4jConfig):
        self.config = config
        self._driver = None
        if not config.password:
            logger.warning("Neo4jConfig.password is empty — connection may fail")

    async def connect(self) -> None:
        """Neo4j 연결."""
        try:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
            )
            logger.info(f"Neo4j 연결 성공: {self.config.uri}")
            await self._ensure_fulltext_index()
        except ImportError:
            logger.warning("neo4j 패키지 미설치 - Mock 모드로 동작")
            self._driver = None
        except Exception as e:  # noqa: BLE001
            logger.error(f"Neo4j 연결 실패: {e}")
            self._driver = None

    async def _ensure_fulltext_index(self) -> None:
        """Ensure fulltext index exists for entity name/title search."""
        if not self._driver:
            return
        try:
            async with self._driver.session(database=self.config.database) as session:
                await session.run(
                    "CREATE FULLTEXT INDEX entity_name_title IF NOT EXISTS "
                    "FOR (n:Person|System|Topic|Term|Team|Document|ProcessStep) "
                    "ON EACH [n.name, n.title]"
                )
            logger.info("Fulltext index 'entity_name_title' ensured")
        except Exception as e:  # noqa: BLE001
            logger.warning("Fulltext index creation skipped: %s", e)

    async def close(self) -> None:
        """연결 종료."""
        if self._driver:
            await self._driver.close()

    async def load_graph(self, graph: dict[str, Any]) -> int:
        """그래프 데이터를 Neo4j에 로드.

        Args:
            graph: {"nodes": [...], "edges": [...]} 형태의 그래프 데이터

        Returns:
            로드된 레코드 수
        """
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        if not self._driver:
            await self.connect()

        if not self._driver:
            logger.warning("Neo4j 연결 없음 - 스킵")
            return 0

        loaded = 0

        async with self._driver.session(database=self.config.database) as session:
            # 노드 로드
            for node in nodes:
                try:
                    await self._create_node(session, node)
                    loaded += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(f"노드 생성 실패: {node.get('node_id')}, {e}")

            # 엣지 로드
            for edge in edges:
                try:
                    await self._create_edge(session, edge)
                    loaded += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(f"엣지 생성 실패: {edge}, {e}")

        logger.info(f"Neo4j 로드 완료: {loaded}개 (노드: {len(nodes)}, 엣지: {len(edges)})")
        return loaded

    async def load_nodes_batch(
        self, nodes: list[dict], batch_size: int = _w.search.neo4j_upsert_batch_size, max_retries: int = _w.search.neo4j_max_retries
    ) -> int:
        """노드 일괄 로드 (대량 데이터용, retry 포함).

        Args:
            nodes: 노드 목록
            batch_size: 배치 크기
            max_retries: 배치당 최대 재시도 횟수

        Returns:
            로드된 노드 수
        """
        import asyncio as _asyncio

        if not self._driver:
            await self.connect()

        if not self._driver:
            return 0

        loaded = 0
        async with self._driver.session(database=self.config.database) as session:
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i : i + batch_size]
                for attempt in range(max_retries):
                    try:
                        await self._batch_create_nodes(session, batch)
                        loaded += len(batch)
                        logger.info(f"노드 배치 로드: {loaded}/{len(nodes)}")
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt < max_retries - 1:
                            logger.warning(
                                f"노드 배치 로드 재시도 ({attempt + 1}/{max_retries}): {e}"
                            )
                            await _asyncio.sleep(min(2 ** attempt, 10))
                        else:
                            logger.error(f"노드 배치 로드 실패 (재시도 소진): {e}")

        return loaded

    async def load_edges_batch(
        self, edges: list[dict], batch_size: int = _w.search.neo4j_upsert_batch_size, max_retries: int = _w.search.neo4j_max_retries
    ) -> int:
        """엣지 일괄 로드 (대량 데이터용, retry 포함).

        Args:
            edges: 엣지 목록
            batch_size: 배치 크기
            max_retries: 배치당 최대 재시도 횟수

        Returns:
            로드된 엣지 수
        """
        import asyncio as _asyncio

        if not self._driver:
            await self.connect()

        if not self._driver:
            return 0

        loaded = 0
        async with self._driver.session(database=self.config.database) as session:
            for i in range(0, len(edges), batch_size):
                batch = edges[i : i + batch_size]
                for attempt in range(max_retries):
                    try:
                        await self._batch_create_edges(session, batch)
                        loaded += len(batch)
                        logger.info(f"엣지 배치 로드: {loaded}/{len(edges)}")
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt < max_retries - 1:
                            logger.warning(
                                f"엣지 배치 로드 재시도 ({attempt + 1}/{max_retries}): {e}"
                            )
                            await _asyncio.sleep(min(2 ** attempt, 10))
                        else:
                            logger.error(f"엣지 배치 로드 실패 (재시도 소진): {e}")

        return loaded

    async def _batch_create_nodes(self, session, nodes: list[dict]) -> None:
        """배치 노드 생성 (UNWIND 사용)."""
        # 노드 타입별로 그룹화
        nodes_by_type: dict[str, list[dict]] = {}
        for node in nodes:
            node_type = self._sanitize_label(
                node.get("node_type", "Entity"), ALLOWED_NODE_TYPES, "Entity"
            )
            if node_type not in nodes_by_type:
                nodes_by_type[node_type] = []
            nodes_by_type[node_type].append(node)

        # 타입별로 UNWIND 쿼리 실행
        for node_type, type_nodes in nodes_by_type.items():
            query = f"""
            UNWIND $nodes AS node
            MERGE (n:{node_type} {{id: node.node_id}})
            SET n.title = node.title,
                n += node.properties
            """
            await session.run(query, nodes=type_nodes)

    async def _batch_create_edges(self, session, edges: list[dict]) -> None:
        """배치 엣지 생성 (UNWIND 사용)."""
        # 관계 타입별로 그룹화
        edges_by_type: dict[str, list[dict]] = {}
        for edge in edges:
            rel_type = self._sanitize_label(
                edge.get("relation", "RELATED_TO"), ALLOWED_RELATION_TYPES, "RELATED_TO"
            )
            if rel_type not in edges_by_type:
                edges_by_type[rel_type] = []
            edges_by_type[rel_type].append(edge)

        # 타입별로 UNWIND 쿼리 실행
        for rel_type, type_edges in edges_by_type.items():
            query = f"""
            UNWIND $edges AS edge
            MATCH (a {{id: edge.source}})
            WHERE a:Document OR a:Entity OR a:Topic OR a:Person OR a:System OR a:Term OR a:Team OR a:KB OR a:Attachment OR a:ProcessStep
            MATCH (b {{id: edge.target}})
            WHERE b:Document OR b:Entity OR b:Topic OR b:Person OR b:System OR b:Term OR b:Team OR b:KB OR b:Attachment OR b:ProcessStep
            MERGE (a)-[r:{rel_type}]->(b)
            SET r += edge.properties
            """
            await session.run(query, edges=type_edges)

    def _sanitize_label(self, label: str, allowed: frozenset[str], default: str) -> str:
        """라벨/관계 타입 검증 및 정제 (Cypher Injection 방지).

        Args:
            label: 검증할 라벨
            allowed: 허용된 라벨 목록
            default: 검증 실패 시 기본값

        Returns:
            안전한 라벨 문자열
        """
        if not label:
            return default

        # 1. 화이트리스트 체크 (대소문자 무시)
        label_upper = label.upper()
        for allowed_label in allowed:
            if allowed_label.upper() == label_upper:
                return allowed_label  # 정확한 케이스로 반환

        # 2. 화이트리스트에 없으면 항상 기본값 사용
        logger.warning("Rejected non-whitelisted label: %s, using default: %s", label, default)
        return default

    async def _create_node(self, session, node: dict) -> None:
        """노드 생성 (Injection 방지)."""
        node_id = node.get("node_id")
        raw_type = node.get("node_type", "Entity")
        title = node.get("title", "")
        properties = node.get("properties", {})

        # 노드 타입 검증 (Cypher Injection 방지)
        safe_type = self._sanitize_label(raw_type, ALLOWED_NODE_TYPES, "Entity")

        # 검증된 라벨로 쿼리 생성
        query = f"""
        MERGE (n:{safe_type} {{id: $node_id}})
        SET n.title = $title
        SET n += $properties
        """

        await session.run(
            query,
            node_id=node_id,
            title=title,
            properties=properties,
        )

    async def _create_edge(self, session, edge: dict) -> None:
        """엣지 생성 (Injection 방지)."""
        source = edge.get("source")
        target = edge.get("target")
        raw_type = edge.get("relation", "RELATED_TO")
        properties = edge.get("properties", {})

        # 관계 타입 검증
        safe_type = self._sanitize_label(raw_type, ALLOWED_RELATION_TYPES, "RELATED_TO")

        query = f"""
        MATCH (a {{id: $source}})
        MATCH (b {{id: $target}})
        MERGE (a)-[r:{safe_type}]->(b)
        SET r += $properties
        """

        await session.run(
            query,
            source=source,
            target=target,
            properties=properties,
        )
