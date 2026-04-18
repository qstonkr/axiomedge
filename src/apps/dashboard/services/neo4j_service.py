"""Neo4j Service

Neo4j 그래프 데이터베이스 서비스.

Created: 2026-02-04 (Sprint 10)
Updated: 2026-03-14 - Configurable query timeout via NEO4J_QUERY_TIMEOUT
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

from services import config as app_config

logger = logging.getLogger(__name__)

try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
except ImportError:
    AsyncGraphDatabase = None
    AsyncDriver = None

# Configurable query timeout (seconds). Used in all session.run() calls.
QUERY_TIMEOUT = int(os.getenv("NEO4J_QUERY_TIMEOUT", "30"))

# =============================================================================
# 노드/관계 타입 상수 (SSOT: src/pipelines/graphrag/extractor.py)
# =============================================================================

NODE_TYPES = {
    "Person",         # 사람
    "Team",           # 팀/부서
    "System",         # 시스템/서비스
    "Document",       # 문서
    "Policy",         # 정책
    "Logic",          # 비즈니스 로직
    "Process",        # 프로세스
    "Term",           # 용어
    "Project",        # 프로젝트
    "Role",           # 역할
    "Attachment",     # 첨부파일
    "Topic",          # 토픽/주제
    "KnowledgeBase",  # 지식베이스
    "ProcessStep",    # 프로세스 단계 (플로우차트)
    "Entity",         # 엔티티 (시각 분석 추출)
}

ALLOWED_RELATIONSHIPS = {
    "MEMBER_OF",        # 소속
    "MANAGES",          # 관리
    "OWNS",             # 소유
    "RESPONSIBLE_FOR",  # 책임
    "PARTICIPATES_IN",  # 참여
    "DEFINES",          # 정의
    "IMPLEMENTS",       # 구현
    "PART_OF",          # 포함
    "RELATED_TO",       # 관련
    "EXTRACTED_FROM",   # 추출 출처
    "BELONGS_TO",       # 소속 (문서→KB)
    "MODIFIED_BY",      # 수정자
    "MENTIONS",         # 언급
    "CREATED_BY",       # 작성자
    "HAS_ATTACHMENT",   # 첨부파일 보유
    "COVERS",           # 주제 포함
    "NEXT_STEP",        # 다음 단계 (플로우차트)
    "FLOWS_TO",         # 흐름 방향
    "CONNECTS_TO",      # 연결
}

# 이력 관계 (현재 → 과거)
HISTORY_RELATIONSHIPS = {
    "WAS_MEMBER_OF", "PREVIOUSLY_MANAGED", "PREVIOUSLY_OWNED",
    "WAS_RESPONSIBLE_FOR", "PREVIOUSLY_PARTICIPATED_IN",
    "PREVIOUSLY_DEFINED", "PREVIOUSLY_IMPLEMENTED", "WAS_PART_OF",
}

ALL_RELATION_TYPES = ALLOWED_RELATIONSHIPS | HISTORY_RELATIONSHIPS


@dataclass
class Neo4jConfig:
    """Neo4j 설정."""

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    database: str = "knowledge-graph"


@dataclass
class GraphNode:
    """그래프 노드."""

    id: str
    label: str
    node_type: str
    properties: dict[str, Any]


@dataclass
class GraphEdge:
    """그래프 엣지."""

    source: str
    target: str
    relation_type: str
    properties: dict[str, Any]


@dataclass
class GraphData:
    """그래프 데이터."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Neo4jService:
    """Neo4j 그래프 서비스."""

    def __init__(self, config: Neo4jConfig | None = None):
        """
        서비스 초기화.

        Args:
            config: Neo4j 설정 (None이면 환경변수에서 로드)
        """
        if config is None:
            config = Neo4jConfig(
                uri=app_config.NEO4J_URI,
                user=app_config.NEO4J_USER,
                password=app_config.NEO4J_PASSWORD,
                database=app_config.NEO4J_DATABASE,
            )
        self.config = config
        self._driver: Any = None

    def connect(self) -> None:
        """Neo4j 연결."""
        if AsyncGraphDatabase is None:
            return

        if not self.config.password:
            logger.warning(
                "Neo4j password is empty. Set NEO4J_PASSWORD env var for production use."
            )

        self._driver = AsyncGraphDatabase.driver(
            self.config.uri,
            auth=(self.config.user, self.config.password),
        )

    async def close(self) -> None:
        """연결 종료."""
        if self._driver:
            await self._driver.close()

    async def __aenter__(self) -> "Neo4jService":
        """비동기 컨텍스트 매니저 진입."""
        self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """비동기 컨텍스트 매니저 종료."""
        await self.close()

    async def search_graph(
        self,
        query: str,
        max_hops: int = 2,
        node_types: list[str] | None = None,
    ) -> GraphData:
        """
        그래프 검색.

        Args:
            query: 검색어 (노드 이름 또는 ID)
            max_hops: 최대 탐색 깊이
            node_types: 노드 유형 필터

        Returns:
            GraphData 객체
        """
        if self._driver is None:
            return self._get_empty_graph_data(query)

        cypher = """
        MATCH (n)
        WHERE n.name CONTAINS $query OR n.title CONTAINS $query OR n.id = $query
        CALL {
            WITH n
            MATCH path = (n)-[*1..$max_hops]-(related)
            RETURN path
        }
        WITH n, path
        UNWIND nodes(path) AS node
        UNWIND relationships(path) AS rel
        RETURN DISTINCT
            node,
            rel,
            startNode(rel) AS start,
            endNode(rel) AS end
        """

        async with self._driver.session(database=self.config.database) as session:
            result = await session.run(
                cypher,
                query=query,
                max_hops=max_hops,
                timeout=QUERY_TIMEOUT,
            )
            records = await result.data()

        return self._parse_graph_data(records, node_types)

    async def find_experts(
        self,
        topic: str,
        min_docs: int = 3,
    ) -> list[dict[str, Any]]:
        """
        특정 토픽의 전문가 찾기.

        Args:
            topic: 토픽 이름
            min_docs: 최소 담당 문서 수

        Returns:
            전문가 목록
        """
        if self._driver is None:
            return self._get_empty_experts(topic)

        cypher = """
        MATCH (p:Person)-[:OWNS|RESPONSIBLE_FOR|MANAGES]->(d:Document)
        WHERE toLower(d.title) CONTAINS toLower($topic)
           OR toLower(d.id) CONTAINS toLower($topic)
        WITH p, count(d) as doc_count
        WHERE doc_count >= $min_docs
        OPTIONAL MATCH (p)-[:MEMBER_OF]->(team:Team)
        RETURN
            p.name as name,
            p.id as person_id,
            team.name as team,
            doc_count,
            toFloat(doc_count) / 20.0 as expertise_score
        ORDER BY doc_count DESC
        LIMIT 10
        """

        async with self._driver.session(database=self.config.database) as session:
            result = await session.run(cypher, topic=topic, min_docs=min_docs, timeout=QUERY_TIMEOUT)
            records = await result.data()

        return records

    async def get_knowledge_path(
        self,
        from_node: str,
        to_node: str,
    ) -> list[dict[str, Any]]:
        """
        두 노드 간 지식 경로 탐색.

        Args:
            from_node: 시작 노드 ID
            to_node: 끝 노드 ID

        Returns:
            경로 정보
        """
        if self._driver is None:
            return []

        cypher = """
        MATCH path = shortestPath(
            (start {id: $from_node})-[*..5]-(end {id: $to_node})
        )
        RETURN
            [n IN nodes(path) | {id: n.id, name: coalesce(n.name, n.title), type: labels(n)[0]}] as nodes,
            [r IN relationships(path) | type(r)] as relations,
            length(path) as distance
        """

        async with self._driver.session(database=self.config.database) as session:
            result = await session.run(cypher, from_node=from_node, to_node=to_node, timeout=QUERY_TIMEOUT)
            records = await result.data()

        return records

    async def get_graph_stats(self) -> dict[str, Any]:
        """
        그래프 통계 조회.

        Returns:
            통계 정보
        """
        if self._driver is None:
            return self._get_empty_stats()

        cypher = """
        MATCH (n)
        WITH labels(n)[0] as label, count(*) as count
        RETURN label, count
        ORDER BY count DESC
        """

        async with self._driver.session(database=self.config.database) as session:
            result = await session.run(cypher, timeout=QUERY_TIMEOUT)
            node_counts = await result.data()

        cypher_edges = """
        MATCH ()-[r]->()
        WITH type(r) as rel_type, count(*) as count
        RETURN rel_type, count
        ORDER BY count DESC
        """

        async with self._driver.session(database=self.config.database) as session:
            result = await session.run(cypher_edges, timeout=QUERY_TIMEOUT)
            edge_counts = await result.data()

        return {
            "node_counts": {r["label"]: r["count"] for r in node_counts},
            "edge_counts": {r["rel_type"]: r["count"] for r in edge_counts},
            "total_nodes": sum(r["count"] for r in node_counts),
            "total_edges": sum(r["count"] for r in edge_counts),
        }

    @staticmethod
    def _parse_node(
        node, node_types: list[str] | None, nodes_map: dict[str, GraphNode],
    ) -> None:
        """Parse a single node record into nodes_map if it passes type filter."""
        if not node:
            return
        node_id = node.get("id") or str(node.id)
        node_type = list(node.labels)[0] if node.labels else "Unknown"
        if node_types and node_type not in node_types:
            return
        if node_id in nodes_map:
            return
        nodes_map[node_id] = GraphNode(
            id=node_id,
            label=node.get("name") or node.get("title") or node_id,
            node_type=node_type,
            properties=dict(node),
        )

    @staticmethod
    def _parse_edge(record: dict) -> GraphEdge | None:
        """Parse a single edge record, returning None if incomplete."""
        rel = record.get("rel")
        start = record.get("start")
        end = record.get("end")
        if not (rel and start and end):
            return None
        return GraphEdge(
            source=start.get("id") or str(start.id),
            target=end.get("id") or str(end.id),
            relation_type=rel.type,
            properties=dict(rel),
        )

    def _parse_graph_data(
        self,
        records: list[dict],
        node_types: list[str] | None,
    ) -> GraphData:
        """Neo4j 결과를 GraphData로 변환."""
        nodes_map: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for record in records:
            self._parse_node(record.get("node"), node_types, nodes_map)
            edge = self._parse_edge(record)
            if edge:
                edges.append(edge)

        return GraphData(
            nodes=list(nodes_map.values()),
            edges=edges,
        )

    def _get_empty_graph_data(self, _query: str) -> GraphData:
        """Neo4j 미연결 시 빈 그래프 데이터 반환."""
        return GraphData(nodes=[], edges=[])

    def _get_empty_experts(self, _topic: str) -> list[dict[str, Any]]:
        """Neo4j 미연결 시 빈 전문가 목록 반환."""
        return []

    def _get_empty_stats(self) -> dict[str, Any]:
        """Neo4j 미연결 시 빈 통계 반환."""
        return {
            "node_counts": {},
            "edge_counts": {},
            "total_nodes": 0,
            "total_edges": 0,
            "status": "disconnected",
            "message": "Neo4j 연결 필요 (환경변수: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)",
        }
