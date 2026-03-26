"""GraphRAG Extractor - 한국어 최적화 지식 그래프 추출기

위키 문서에서 노드(Person, Team, System 등)와 관계(MEMBER_OF, MANAGES 등)를 추출합니다.

Extracted from oreo-ecosystem graphrag_extractor.py.
- LLM calls use local OllamaClient instead of oreo IGraphRAGLLMClient
- Neo4j driver uses direct neo4j package
- All oreo-specific imports (feature flags, hub_search_dependencies, graph_node_registry) removed
- Core extraction logic, history-preserving relationships, schema constraints preserved exactly

Features:
- 최신성 기반 관계 업데이트: 동일 관계는 최신 문서 기준 유지
- 이력 보존: 변경된 관계는 WAS_* 타입으로 이력 보존
- 타임스탬프 추적: created_at, updated_at 자동 기록
- 카디널리티 검증: 관계 규칙 위반 감지

Usage:
    from src.pipeline.graphrag_extractor import GraphRAGExtractor

    extractor = GraphRAGExtractor()
    result = extractor.extract(document_text, source_updated_at="2026-02-11")
    extractor.save_to_neo4j(result)
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config_weights import weights as _w

logger = logging.getLogger(__name__)


# =============================================================================
# Cypher Safety (inlined from cypher_safety.py)
# =============================================================================
_SAFE_CYPHER_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_safe_cypher_label(value: str) -> bool:
    """Return True if value is a safe Cypher label/relationship identifier."""
    return bool(_SAFE_CYPHER_LABEL.match(value))


# =============================================================================
# Schema Definition
# =============================================================================
ALLOWED_NODES = [
    "Person",      # 사람
    "Team",        # 팀/부서
    "System",      # 시스템/서비스
    "Document",    # 문서
    "Policy",      # 정책
    "Logic",       # 비즈니스 로직
    "Process",     # 프로세스/절차
    "Term",        # 용어
    "Project",     # 프로젝트
    "Role",        # 역할
    "Store",       # 점포/매장
    "Location",    # 지역/위치
    "Product",     # 상품/서비스
    "Event",       # 사건/활동
]

# KB-specific schema profiles
KB_SCHEMA_PROFILES: dict[str, dict[str, list[str]]] = {
    "a-ari": {
        "nodes": ["Store", "Process", "Product", "Person", "Policy", "Term", "Location"],
        "relationships": ["OPERATES", "FOLLOWS", "SELLS", "MANAGES", "APPLIES_TO", "LOCATED_IN", "PART_OF"],
        "prompt_focus": "점포, 절차/프로세스, 상품, 정책/규정, 용어",
    },
    "g-espa": {
        "nodes": ["Store", "Person", "Process", "Event", "Product", "Location", "Team", "Term"],
        "relationships": ["MANAGES", "OPERATES", "PARTICIPATES_IN", "LOCATED_IN", "RESPONSIBLE_FOR", "RELATED_TO", "SELLS", "PART_OF"],
        "prompt_focus": "점포(GS25/CU), 경영주/OFC(사람), ESPA활동/개선활동, 상품카테고리, 지역/상권, 매출성과, 경쟁점",
    },
    "drp": {
        "nodes": ["Store", "Person", "Policy", "Event", "Location", "Team"],
        "relationships": ["MANAGES", "APPLIES_TO", "PARTICIPATES_IN", "LOCATED_IN", "RESPONSIBLE_FOR", "RELATED_TO"],
        "prompt_focus": "점포, 당사자(사람), 정책/규정, 분쟁사건, 지역",
    },
    "hax": {
        "nodes": ["System", "Team", "Person", "Process", "Project", "Term", "Document"],
        "relationships": ["MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR", "DEFINES", "PART_OF"],
        "prompt_focus": "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어",
    },
    "itops_general": {
        "nodes": ["System", "Team", "Person", "Process", "Project", "Term", "Document", "Policy", "Logic"],
        "relationships": ["MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR", "DEFINES", "PART_OF", "FOLLOWS", "APPLIES_TO"],
        "prompt_focus": "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어, 정책/규정, 비즈니스로직, 업무절차",
    },
    "partnertalk": {
        "nodes": ["Person", "Product", "Store", "Process", "Term", "Event"],
        "relationships": ["SELLS", "MANAGES", "APPLIES_TO", "RELATED_TO", "FOLLOWS"],
        "prompt_focus": "협력사(사람/회사), 상품, 점포, 문의절차, 용어",
    },
}

ALLOWED_RELATIONSHIPS = [
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
    "LOCATED_IN",       # 위치
    "OPERATES",         # 운영
    "FOLLOWS",          # 절차 순서
    "APPLIES_TO",       # 적용 대상
    "SELLS",            # 판매
]

# 이력 관계 매핑 (현재 -> 과거)
HISTORY_RELATIONSHIP_MAP = {
    "MEMBER_OF": "WAS_MEMBER_OF",
    "MANAGES": "PREVIOUSLY_MANAGED",
    "OWNS": "PREVIOUSLY_OWNED",
    "RESPONSIBLE_FOR": "WAS_RESPONSIBLE_FOR",
    "PARTICIPATES_IN": "PREVIOUSLY_PARTICIPATED_IN",
    "DEFINES": "PREVIOUSLY_DEFINED",
    "IMPLEMENTS": "PREVIOUSLY_IMPLEMENTED",
    "PART_OF": "WAS_PART_OF",
}


# =============================================================================
# Korean Optimized Prompt (Simple & Effective)
# =============================================================================
KOREAN_EXTRACTION_PROMPT = """다음 문서에서 엔티티와 관계를 추출하세요.
문서에 명시된 정보만 추출하고, 추측하지 마세요.

추출 대상:
- Person(사람), Team(팀/부서), System(시스템)
- Store(점포/매장), Location(지역), Process(절차/프로세스)
- Product(상품), Event(활동/사건), Policy(정책/규정)

문서: {document}

아래 JSON 형식으로만 출력하세요:
{{"nodes":[{{"id":"이름","type":"Person"}},{{"id":"팀명","type":"Team"}},{{"id":"시스템명","type":"System"}},{{"id":"점포명","type":"Store"}},{{"id":"절차명","type":"Process"}}],"relationships":[{{"source":"사람","type":"MEMBER_OF","target":"팀"}},{{"source":"점포","type":"PART_OF","target":"지역"}},{{"source":"사람","type":"MANAGES","target":"점포"}}]}}

JSON:"""

# Default schema for unknown KBs
DEFAULT_SCHEMA_PROFILE = {
    "nodes": ALLOWED_NODES,
    "relationships": ALLOWED_RELATIONSHIPS,
    "prompt_focus": "사람, 팀, 시스템, 점포, 절차, 지역, 상품, 정책",
}


def get_kb_schema(kb_id: str) -> dict[str, Any]:
    """Get schema profile for a KB."""
    return KB_SCHEMA_PROFILES.get(kb_id, DEFAULT_SCHEMA_PROFILE)


def build_extraction_prompt(document: str, kb_id: str | None = None) -> str:
    """Build KB-specific extraction prompt."""
    schema = get_kb_schema(kb_id) if kb_id else DEFAULT_SCHEMA_PROFILE
    focus = schema.get("prompt_focus", "사람, 팀, 시스템")
    nodes = schema.get("nodes", ALLOWED_NODES)

    # Build example nodes for prompt
    # NOTE: Result must be .format(document=...) compatible.
    # Use doubled braces {{}} for literal braces in the output.
    examples = []
    for n in nodes[:5]:
        label_map = {
            "Person": ("이름", "Person"),
            "Team": ("팀명", "Team"),
            "System": ("시스템명", "System"),
            "Store": ("점포명", "Store"),
            "Process": ("절차명", "Process"),
            "Location": ("지역명", "Location"),
            "Product": ("상품명", "Product"),
            "Event": ("활동명", "Event"),
            "Policy": ("정책명", "Policy"),
            "Term": ("용어명", "Term"),
            "Project": ("프로젝트명", "Project"),
        }
        id_label, type_label = label_map.get(n, ("이름", n))
        examples.append(f'{{{{"id":"{id_label}","type":"{type_label}"}}}}')

    nodes_example = ",".join(examples)

    # Use string concatenation to keep .format() compatibility
    return (
        "다음 문서에서 엔티티와 관계를 추출하세요.\n"
        "문서에 명시된 정보만 추출하고, 추측하지 마세요.\n\n"
        f"추출 대상: {focus}\n\n"
        "문서: {document}\n\n"
        "아래 JSON 형식으로만 출력하세요:\n"
        f'{{{{"nodes":[{nodes_example}],"relationships":[{{{{"source":"엔티티A","type":"RELATED_TO","target":"엔티티B"}}}}]}}}}\n\n'
        "JSON:"
    )


# =============================================================================
# Data Classes
# =============================================================================
@dataclass
class GraphNode:
    """그래프 노드"""
    id: str
    type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, **self.properties}


@dataclass
class GraphRelationship:
    """그래프 관계"""
    source: str
    target: str
    type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            **self.properties
        }


@dataclass
class ExtractionResult:
    """추출 결과"""
    nodes: list[GraphNode] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    source_document: str | None = None
    source_page_id: str | None = None
    source_updated_at: str | None = None  # ISO 형식 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    raw_response: str | None = None

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "relationships": [r.to_dict() for r in self.relationships],
            "source_document": self.source_document,
            "source_page_id": self.source_page_id,
            "source_updated_at": self.source_updated_at,
        }


# Module-level shared executor for sync-in-async bridging (P2-5 perf fix)
_SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)


class _OllamaLLMClient:
    """Local Ollama LLM adapter for GraphRAG extraction.

    Uses the OllamaClient from src.llm for LLM calls.
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from ..llm.ollama_client import OllamaClient, OllamaConfig
            self._client = OllamaClient(
                OllamaConfig(
                    base_url=self._base_url,
                    model=self._model,
                    temperature=_w.llm.graphrag_temperature,
                )
            )
        return self._client

    def invoke(self, *, document: str, prompt_template: str) -> str:
        """Synchronous invoke using a fresh event loop per call.

        asyncio.run() closes the loop after each call, causing 'Event loop is
        closed' on subsequent calls. Instead, create a new loop each time.
        """
        import asyncio

        prompt = prompt_template.format(document=document)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # Create fresh client each call to avoid event loop binding issues
        from src.llm.ollama_client import OllamaClient, OllamaConfig
        fresh_client = OllamaClient(OllamaConfig(
            base_url=self._base_url,
            model=self._model,
            temperature=_w.llm.graphrag_temperature,
        ))

        if loop and loop.is_running():
            future = _SHARED_EXECUTOR.submit(
                self._run_in_new_loop,
                fresh_client.generate(prompt, temperature=_w.llm.graphrag_temperature),
            )
            return future.result()
        else:
            return self._run_in_new_loop(
                fresh_client.generate(prompt, temperature=_w.llm.graphrag_temperature),
            )

    @staticmethod
    def _run_in_new_loop(coro):
        """Run a coroutine in a fresh event loop (avoids 'Event loop is closed')."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# =============================================================================
# GraphRAG Extractor
# =============================================================================
class GraphRAGExtractor:
    """한국어 최적화 GraphRAG 추출기

    Features:
    - 관계 이력: 변경 시 WAS_* 타입으로 보존
    - 최신성: 수정일(updated_at) 기준 충돌 해결
    """

    def __init__(
        self,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        llm_client: Any | None = None,
        neo4j_driver: Any | None = None,
    ) -> None:
        self.ollama_base_url = ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
        self.neo4j_uri = neo4j_uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = neo4j_user or os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD", "password")

        self._llm = llm_client
        self._neo4j_driver = neo4j_driver

    def _get_llm(self):
        """LLM client 가져오기 (lazy loading)."""
        if self._llm is None:
            self._llm = _OllamaLLMClient(
                base_url=self.ollama_base_url,
                model=self.ollama_model,
            )
        return self._llm

    def _get_neo4j_driver(self):
        """Neo4j driver 가져오기 (lazy loading)."""
        if self._neo4j_driver is None:
            from neo4j import GraphDatabase

            self._neo4j_driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password),
            )
        return self._neo4j_driver

    def extract(
        self,
        document: str,
        source_title: str | None = None,
        source_page_id: str | None = None,
        source_updated_at: str | None = None,
        max_length: int = _w.chunking.graphrag_max_document_length,
        kb_id: str | None = None,
    ) -> ExtractionResult:
        """문서에서 지식 그래프 추출

        Args:
            document: 문서 텍스트
            source_title: 문서 제목
            source_page_id: 문서 ID
            source_updated_at: 문서 수정일 (ISO 형식, 최신성 판단용)
            max_length: 최대 처리 길이
            kb_id: KB ID (KB별 스키마 적용)

        Returns:
            ExtractionResult: 추출 결과
        """
        # 문서 길이 제한
        doc_text = document[:max_length] if len(document) > max_length else document

        # KB별 프롬프트 선택
        prompt = build_extraction_prompt(doc_text, kb_id) if kb_id else KOREAN_EXTRACTION_PROMPT

        # LLM 호출
        try:
            raw_content = self._get_llm().invoke(
                document=doc_text,
                prompt_template=prompt,
            )
        except Exception as e:
            logger.error(f"LLM 호출 실패: {e}")
            return ExtractionResult(
                source_document=source_title,
                source_page_id=source_page_id,
                source_updated_at=source_updated_at,
                raw_response=str(e),
            )

        # JSON 파싱
        result = self._parse_response(raw_content)
        result.source_document = source_title
        result.source_page_id = source_page_id
        result.source_updated_at = source_updated_at
        result.raw_response = raw_content

        logger.info(f"추출 완료: {result.node_count} nodes, {result.relationship_count} relationships")

        return result

    def _parse_response(self, content: str) -> ExtractionResult:
        """LLM 응답 파싱"""
        result = ExtractionResult()

        try:
            # ```json 블록 제거
            if '```' in content:
                import re as _re
                _match = _re.search(r"```(?:json)?\s*\n?(.*?)```", content, _re.DOTALL)
                if _match:
                    content = _match.group(1).strip()

            # JSON 추출
            start = content.find('{')
            end = content.rfind('}') + 1

            if start < 0 or end <= start:
                logger.warning("JSON을 찾을 수 없음")
                return result

            json_str = content[start:end]
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                try:
                    from json_repair import repair_json
                    data = json.loads(repair_json(json_str))
                    logger.warning("GraphRAG JSON repaired for document")
                except Exception:
                    raise

            # 노드 파싱
            for node_data in data.get('nodes', []):
                node_id = node_data.get('id', '')
                node_type = node_data.get('type', 'Unknown')

                # 유효한 타입만 허용 (Cypher Injection 방지)
                if node_type not in ALLOWED_NODES:
                    logger.warning(f"허용되지 않은 노드 타입 무시: {node_type} (id={node_id})")
                    continue

                if node_id:  # 빈 ID 제외
                    node = GraphNode(
                        id=node_id,
                        type=node_type,
                        properties={k: v for k, v in node_data.items() if k not in ('id', 'type')}
                    )
                    result.nodes.append(node)

            # 노드 ID 집합 (관계 검증용)
            node_ids = {n.id for n in result.nodes}

            # 관계 파싱
            for rel_data in data.get('relationships', []):
                source = rel_data.get('source', '')
                target = rel_data.get('target', '')
                rel_type = rel_data.get('type', 'RELATED_TO')

                # 유효한 관계 타입만 허용
                if rel_type not in ALLOWED_RELATIONSHIPS:
                    rel_type = 'RELATED_TO'

                # source와 target이 모두 있는 유효한 관계만 추가
                if source and target:
                    # Dangling reference 경고
                    if source not in node_ids or target not in node_ids:
                        logger.warning(
                            f"Dangling reference: {source}-[{rel_type}]->{target} "
                            f"(추출된 노드에 없는 엔티티 참조)"
                        )
                    rel = GraphRelationship(
                        source=source,
                        target=target,
                        type=rel_type,
                        properties={k: v for k, v in rel_data.items() if k not in ('source', 'target', 'type')}
                    )
                    result.relationships.append(rel)

        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}")
        except Exception as e:
            logger.error(f"파싱 오류: {e}")

        return result

    def save_to_neo4j(self, result: ExtractionResult) -> dict[str, int]:
        """추출 결과를 Neo4j에 저장 (이력 보존 + 최신성 기반 업데이트)

        동작 방식:
        1. 노드: MERGE로 생성/업데이트, updated_at 타임스탬프 기록
        2. 관계: 기존 관계 확인 후 최신성 비교
           - 새 문서가 최신 -> 기존 관계를 이력(WAS_*)으로 이동, 새 관계 생성
           - 기존이 최신 -> Skip (새 관계 무시)
           - 동일 target -> 타임스탬프만 업데이트

        Args:
            result: 추출 결과

        Returns:
            저장된 노드/관계 수
        """
        driver = self._get_neo4j_driver()
        now = datetime.now(UTC).isoformat()
        source_updated = result.source_updated_at or now

        stats = {
            "nodes_created": 0,
            "nodes_updated": 0,
            "relationships_created": 0,
            "relationships_updated": 0,
            "relationships_archived": 0,
            "relationships_skipped": 0,
        }

        with driver.session() as session:
            # 노드 생성/업데이트 (batched by type for efficiency)
            nodes_by_type: dict[str, list[dict]] = {}
            for node in result.nodes:
                if not _is_safe_cypher_label(node.type):
                    logger.error(f"안전하지 않은 노드 타입 스킵: {node.type}")
                    continue
                properties = {"id": node.id, **node.properties}
                if result.source_page_id:
                    properties["source_page_id"] = result.source_page_id
                if result.source_document:
                    properties["source_document"] = result.source_document
                nodes_by_type.setdefault(node.type, []).append(properties)

            for node_type, node_params in nodes_by_type.items():
                try:
                    batch_query = f"""
                        UNWIND $nodes AS props
                        MERGE (n:{node_type} {{id: props.id}})
                        ON CREATE SET
                            n.created_at = $now,
                            n.updated_at = $now,
                            n += props
                        ON MATCH SET
                            n.updated_at = $now,
                            n += props
                        SET n:__Entity__
                        RETURN n.created_at = $now AS is_new
                    """
                    records = session.run(batch_query, nodes=node_params, now=now)
                    for rec in records:
                        if rec and rec["is_new"]:
                            stats["nodes_created"] += 1
                        else:
                            stats["nodes_updated"] += 1
                except Exception as e:
                    logger.error(f"노드 배치 생성 실패 (type={node_type}): {e}")

            # 관계 생성 (이력 보존 + 최신성 기반)
            # Simple relationships (non-history) are batched; history-aware ones use individual queries
            for rel in result.relationships:
                try:
                    # 관계 타입 검증 (Cypher Injection 방지)
                    if not _is_safe_cypher_label(rel.type):
                        logger.error(f"안전하지 않은 관계 타입 스킵: {rel.type}")
                        continue

                    rel_stats = self._save_relationship_with_history(
                        session, rel, result, source_updated, now
                    )
                    stats["relationships_created"] += rel_stats.get("created", 0)
                    stats["relationships_updated"] += rel_stats.get("updated", 0)
                    stats["relationships_archived"] += rel_stats.get("archived", 0)
                    stats["relationships_skipped"] += rel_stats.get("skipped", 0)
                except Exception as e:
                    logger.error(f"관계 생성 실패 ({rel.source}->{rel.target}): {e}")

        logger.info(f"Neo4j 저장 완료: {stats}")
        return stats

    def _save_relationship_with_history(
        self,
        session,
        rel: GraphRelationship,
        result: ExtractionResult,
        source_updated: str,
        now: str,
    ) -> dict[str, int]:
        """관계 저장 (이력 보존 + 최신성 기반 업데이트)

        로직:
        1. 동일 source + 동일 type의 기존 관계 조회
        2. 기존 관계가 없으면 -> 새로 생성
        3. 기존 관계가 있고 target이 같으면 -> 타임스탬프 업데이트
        4. 기존 관계가 있고 target이 다르면:
           - 새 문서가 최신 -> 기존 관계를 WAS_* 이력으로 변환, 새 관계 생성
           - 기존이 최신 -> Skip
        """
        stats = {"created": 0, "updated": 0, "archived": 0, "skipped": 0}

        # 1. 동일 source + type의 기존 관계 조회
        check_query = f"""
            MATCH (a {{id: $source}})-[r:{rel.type}]->(b)
            RETURN b.id AS target, r.updated_at AS updated_at, r.source_page_id AS source_page_id
        """
        existing = list(session.run(check_query, source=rel.source))

        if not existing:
            # 기존 관계 없음 -> 새로 생성
            self._create_relationship(session, rel, result, source_updated, now)
            stats["created"] += 1
        else:
            # 기존 관계 있음
            new_rel_created = False
            for record in existing:
                existing_target = record["target"]
                existing_updated = record["updated_at"] or "1970-01-01"

                if existing_target == rel.target:
                    # 동일 target -> 타임스탬프만 업데이트
                    update_query = f"""
                        MATCH (a {{id: $source}})-[r:{rel.type}]->(b {{id: $target}})
                        SET r.updated_at = $now,
                            r.source_page_id = $source_page_id,
                            r.source_document = $source_document
                        RETURN r
                    """
                    session.run(
                        update_query,
                        source=rel.source,
                        target=rel.target,
                        now=now,
                        source_page_id=result.source_page_id,
                        source_document=result.source_document,
                    )
                    stats["updated"] += 1
                else:
                    # 다른 target -> 최신성 비교
                    if self._is_newer(source_updated, existing_updated):
                        # 새 문서가 최신 -> 기존 관계를 이력으로 이동
                        self._archive_relationship(session, rel.source, rel.type, existing_target, now)
                        stats["archived"] += 1
                        if not new_rel_created:
                            self._create_relationship(session, rel, result, source_updated, now)
                            stats["created"] += 1
                            new_rel_created = True
                    else:
                        # 기존이 최신 -> Skip
                        logger.info(
                            f"Skip: {rel.source}-[{rel.type}]->{rel.target} "
                            f"(기존 {existing_target}이 더 최신)"
                        )
                        stats["skipped"] += 1

        return stats

    def _create_relationship(
        self,
        session,
        rel: GraphRelationship,
        result: ExtractionResult,
        source_updated: str,
        now: str,
    ) -> None:
        """새 관계 생성"""
        query = f"""
            MATCH (a {{id: $source}})
            MATCH (b {{id: $target}})
            CREATE (a)-[r:{rel.type}]->(b)
            SET r.created_at = $now,
                r.updated_at = $now,
                r.source_updated_at = $source_updated,
                r.source_page_id = $source_page_id,
                r.source_document = $source_document
            SET r += $properties
            RETURN r
        """
        session.run(
            query,
            source=rel.source,
            target=rel.target,
            now=now,
            source_updated=source_updated,
            source_page_id=result.source_page_id,
            source_document=result.source_document,
            properties=rel.properties,
        )

    def _archive_relationship(
        self,
        session,
        source: str,
        rel_type: str,
        target: str,
        now: str,
    ) -> None:
        """기존 관계를 이력 관계(WAS_*)로 변환"""
        history_type = HISTORY_RELATIONSHIP_MAP.get(rel_type, f"WAS_{rel_type}")
        if not _is_safe_cypher_label(history_type):
            logger.warning("Unsafe Cypher label for history type: %s", history_type)
            return

        query = f"""
            MATCH (a {{id: $source}})-[r:{rel_type}]->(b {{id: $target}})
            CREATE (a)-[h:{history_type}]->(b)
            SET h = properties(r),
                h.archived_at = $now,
                h.original_type = $rel_type
            DELETE r
            RETURN h
        """
        session.run(query, source=source, target=target, now=now, rel_type=rel_type)
        logger.info(f"Archived: {source}-[{rel_type}]->{target} -> [{history_type}]")

    def _is_newer(self, date1: str, date2: str) -> bool:
        """date1이 date2보다 최신인지 확인"""
        try:
            # ISO 형식 파싱 (YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS)
            d1 = datetime.fromisoformat(date1.replace("Z", "+00:00"))
            d2 = datetime.fromisoformat(date2.replace("Z", "+00:00"))
            return d1 > d2
        except (ValueError, AttributeError):
            logger.warning(f"날짜 비교 실패 (date1={date1}, date2={date2}), 새 문서 우선 처리")
            return True

    def get_relationship_history(
        self,
        entity_id: str,
        rel_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """엔티티의 관계 이력 조회

        Args:
            entity_id: 엔티티 ID (예: "김철수")
            rel_type: 관계 타입 (예: "MEMBER_OF") - None이면 전체 이력

        Returns:
            현재 관계 + 과거 관계 목록
        """
        driver = self._get_neo4j_driver()
        history: list[dict[str, Any]] = []

        if rel_type and not _is_safe_cypher_label(rel_type):
            logger.error(f"안전하지 않은 관계 타입: {rel_type}")
            return history

        with driver.session() as session:
            # 현재 관계 조회
            if rel_type:
                current_query = f"""
                    MATCH (a {{id: $id}})-[r:{rel_type}]->(b)
                    RETURN b.id AS target, r.updated_at AS updated_at,
                           r.source_document AS source_document, 'current' AS status
                """
            else:
                current_query = """
                    MATCH (a {id: $id})-[r]->(b)
                    WHERE NOT type(r) STARTS WITH 'WAS_' AND NOT type(r) STARTS WITH 'PREVIOUSLY_'
                    RETURN type(r) AS rel_type, b.id AS target, r.updated_at AS updated_at,
                           r.source_document AS source_document, 'current' AS status
                """
            for record in session.run(current_query, id=entity_id):
                history.append(dict(record))

            # 과거 관계 조회 (WAS_*, PREVIOUSLY_*)
            if rel_type:
                history_type = HISTORY_RELATIONSHIP_MAP.get(rel_type, f"WAS_{rel_type}")
                history_query = f"""
                    MATCH (a {{id: $id}})-[r:{history_type}]->(b)
                    RETURN b.id AS target, r.archived_at AS archived_at,
                           r.source_document AS source_document, 'archived' AS status,
                           r.original_type AS original_type
                    ORDER BY r.archived_at DESC
                """
            else:
                history_query = """
                    MATCH (a {id: $id})-[r]->(b)
                    WHERE type(r) STARTS WITH 'WAS_' OR type(r) STARTS WITH 'PREVIOUSLY_'
                    RETURN type(r) AS rel_type, b.id AS target, r.archived_at AS archived_at,
                           r.source_document AS source_document, 'archived' AS status,
                           r.original_type AS original_type
                    ORDER BY r.archived_at DESC
                """
            for record in session.run(history_query, id=entity_id):
                history.append(dict(record))

        return history

    def query_at_point_in_time(
        self,
        entity_id: str,
        rel_type: str,
        as_of: str,
    ) -> dict[str, Any] | None:
        """특정 시점의 관계 조회

        Args:
            entity_id: 엔티티 ID
            rel_type: 관계 타입
            as_of: 조회 시점 (ISO 형식)

        Returns:
            해당 시점에 유효했던 관계 정보
        """
        if not _is_safe_cypher_label(rel_type):
            logger.error(f"안전하지 않은 관계 타입: {rel_type}")
            return None

        driver = self._get_neo4j_driver()
        history_type = HISTORY_RELATIONSHIP_MAP.get(rel_type, f"WAS_{rel_type}")

        with driver.session() as session:
            # 현재 관계가 해당 시점에 유효한지 확인
            current_query = f"""
                MATCH (a {{id: $id}})-[r:{rel_type}]->(b)
                WHERE r.created_at <= $as_of
                RETURN b.id AS target, r.created_at AS valid_from,
                       r.source_document AS source_document
            """
            record = session.run(current_query, id=entity_id, as_of=as_of).single()
            if record:
                return {
                    "target": record["target"],
                    "valid_from": record["valid_from"],
                    "source_document": record["source_document"],
                    "status": "current",
                }

            # 과거 이력에서 해당 시점에 유효했던 관계 찾기
            history_query = f"""
                MATCH (a {{id: $id}})-[r:{history_type}]->(b)
                WHERE r.created_at <= $as_of AND (r.archived_at IS NULL OR r.archived_at > $as_of)
                RETURN b.id AS target, r.created_at AS valid_from, r.archived_at AS valid_until,
                       r.source_document AS source_document
                ORDER BY r.created_at DESC
                LIMIT 1
            """
            record = session.run(history_query, id=entity_id, as_of=as_of).single()
            if record:
                return {
                    "target": record["target"],
                    "valid_from": record["valid_from"],
                    "valid_until": record["valid_until"],
                    "source_document": record["source_document"],
                    "status": "archived",
                }

        return None

    def query_recent_entities(self, limit: int = 500) -> list[dict]:
        """Neo4j에서 최근 저장된 엔티티 조회."""
        try:
            driver = self._get_neo4j_driver()
        except Exception:
            return []

        try:
            query = """
            MATCH (n:__Entity__)
            WHERE n.updated_at IS NOT NULL
            RETURN [l IN labels(n) WHERE l <> '__Entity__'][0] as type,
                   n.id as id, properties(n) as properties
            ORDER BY n.updated_at DESC
            LIMIT $limit
            """
            with driver.session() as session:
                result = session.run(query, limit=limit)
                return [dict(record) for record in result]
        except Exception as e:
            logger.warning(f"최근 엔티티 조회 실패: {e}")
            return []

    def close(self):
        """리소스 정리"""
        if self._neo4j_driver:
            self._neo4j_driver.close()
            self._neo4j_driver = None


# =============================================================================
# Batch Processor
# =============================================================================
class GraphRAGBatchProcessor:
    """배치 처리기 - 여러 문서를 순차적으로 처리"""

    def __init__(self, extractor: GraphRAGExtractor | None = None):
        self.extractor = extractor or GraphRAGExtractor()
        self.results: list[ExtractionResult] = []

    def process_documents(
        self,
        documents: list[dict[str, Any]],
        save_to_neo4j: bool = True,
        kb_id: str | None = None,
    ) -> dict[str, Any]:
        """여러 문서 처리

        Args:
            documents: [{"content": "...", "title": "...", "page_id": "...", "updated_at": "..."}]
            save_to_neo4j: Neo4j에 저장 여부

        Returns:
            처리 통계
        """
        stats = {
            "total": len(documents),
            "success": 0,
            "failed": 0,
            "total_nodes": 0,
            "total_relationships": 0,
            "relationships_archived": 0,
        }

        for i, doc in enumerate(documents):
            logger.info(f"Processing [{i+1}/{len(documents)}]: {doc.get('title', 'Unknown')}")

            try:
                result = self.extractor.extract(
                    document=doc.get("content", ""),
                    source_title=doc.get("title"),
                    source_page_id=doc.get("page_id"),
                    source_updated_at=doc.get("updated_at"),
                    kb_id=kb_id,
                )

                self.results.append(result)
                stats["total_nodes"] += result.node_count
                stats["total_relationships"] += result.relationship_count

                if save_to_neo4j and (result.node_count > 0 or result.relationship_count > 0):
                    save_stats = self.extractor.save_to_neo4j(result)
                    stats["relationships_archived"] += save_stats.get("relationships_archived", 0)

                stats["success"] += 1

            except Exception as e:
                logger.error(f"문서 처리 실패: {e}")
                stats["failed"] += 1

        return stats

    def get_all_nodes(self) -> list[GraphNode]:
        """모든 추출된 노드 반환"""
        nodes = []
        for result in self.results:
            nodes.extend(result.nodes)
        return nodes

    def get_all_relationships(self) -> list[GraphRelationship]:
        """모든 추출된 관계 반환"""
        rels = []
        for result in self.results:
            rels.extend(result.relationships)
        return rels
