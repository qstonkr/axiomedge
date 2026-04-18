"""GraphRAG Extractor - Main extraction and Neo4j persistence classes."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re as _re_mod
from typing import Any

from src.config.weights import weights as _w
from .models import ExtractionResult, GraphNode, GraphRelationship
from .prompts import (
    ALLOWED_NODES,
    ALLOWED_RELATIONSHIPS,
    KOREAN_EXTRACTION_PROMPT,
    _is_safe_cypher_label,
    build_extraction_prompt,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Entity Validation Constants
# =============================================================================
# LLM 추출 결과를 필터링하는 규칙들. 필터링 없이 저장하면 그래프에
# "unknown", "담당자", OCR 깨짐 등 노이즈 노드가 대량 생성되어
# graph expansion 검색 품질이 급락한다.
#
# 필터링 파이프라인:
#   1. _is_corrupted_entity() — 플레이스홀더·OCR 깨짐 제거
#   2. _is_invalid_person()   — 회사/지역/역할을 Person 으로 잘못 분류한 것 차단
#   3. _reclassify_person()   — Person→Store/Location/System/Team 재분류
#   4. _validate_entity()     — Store 내 플랫폼→System, 상품 노이즈 제거
#   5. _parse_nodes()         — ALLOWED_NODES 화이트리스트 + orphan 제거
# =============================================================================

# Placeholder / sentinel names that LLMs generate for unknown entities
# LLM이 정보를 모를 때 출력하는 센티널 값. 실체 없는 노드를 제거.
_PLACEHOLDER_VALUES = frozenset({
    '명시되지 않음', '미상', 'unknown', 'unnamed', '이름없음',
    '알 수 없음', 'none', 'n/a', '-', '?', '기타',
    '정보없음', '불명', '미기재', '미입력', '없음',
    '문서 작성자', '담당자', '해당 없음',
})

# LLM이 Person 으로 잘못 분류하는 비인명 엔티티.
# 회사명, 지명, 추상 개념이 Person 으로 추출되면 graph 쿼리 오염.
_NON_PERSON_BLOCKLIST = frozenset({
    '피그마', '구글', '깃허브', '아마존', '마이크로소프트', '네이버', '카카오',
    '신월동', '강남', '서울', '부산', '제주', '논산', '수지',
    '리소스', '개인정보', '데이터', '시스템', '서버', '프로젝트',
    '휴가', '요청', '권한', '설정', '보안', '인증',
    '매출', '정산', '비용', '수수료', '계약', '점포',
})

# Company/brand suffixes that should be Store, not Person
_COMPANY_SUFFIXES = ('카드', '생명', '보험', '은행', '증권', '캐피탈', '파트너스', '자산운용')

# Location suffix pattern: 2-4 Korean chars ending with geographic suffix
_LOCATION_SUFFIX_RE = _re_mod.compile(r'^[가-힣]{2,4}[동구시도읍면로]$')

# Team/org suffixes
_TEAM_SUFFIXES = ('팀', '본부', '실', '부서', '센터', '사업부', '그룹', '파트')

# Tech/tool names that should be System
_SYSTEM_NAMES = frozenset({
    '블록체인', '레디스', '카카오톡', '셀러툴', '라인웍스', '피그마',
    '마이쇼핑', '티비허브', '암복화', '랜섬웨어', '팝빌', '인프라',
    '클라우드', '방화벽', '백업', '모니터링', '포스', '포스기',
    '네트워크', '와이파이',
})

# Platforms that should be System, not Store
_PLATFORM_NAMES = frozenset({
    'G마켓', '11번가', '쿠팡', '위메프', '티몬', '옥션',
    '네이버쇼핑', '카카오커머스', 'SSG닷컴',
})

# OCR 깨짐 탐지: 낱자모(ㄱ~ㅎ)가 포함되면 스캔 문서 노이즈.
_LONE_JAMO_RE = _re_mod.compile(r'[\u3131-\u3163]')
# OCR 깨짐 탐지: 동일 글자 3회 이상 반복 (예: "가가가") → 인식 오류.
_REPEATED_SYLLABLE_RE = _re_mod.compile(r'(.)\1{2,}')
# Person name: digits or underscores
_DIGIT_UNDERSCORE_RE = _re_mod.compile(r'[\d_]')
# Product-like patterns: contains numbers+unit or specific food items
_PRODUCT_PATTERN_RE = _re_mod.compile(r'\d+[GgMmLl]|김밥|라면|도시락|샌드위치|음료')


def _is_corrupted_entity(node_id: str) -> bool:
    """Check if entity ID is a placeholder or OCR corruption."""
    stripped = node_id.strip()
    if stripped.lower() in _PLACEHOLDER_VALUES or stripped in _PLACEHOLDER_VALUES:
        return True
    if _LONE_JAMO_RE.search(node_id):
        return True
    if _REPEATED_SYLLABLE_RE.search(node_id):
        return True
    return False


_ROLE_SUFFIXES = ("담당자", "관리자", "엔지니어", "개발자", "운영자", "리더", "매니저", "담당")
_PLACEHOLDER_NAMES = ("미기재", "미명시", "미상", "이름 없", "확인 불가")


def _is_invalid_person(node_id: str, name: str) -> bool:
    """Return True if the person entity should be rejected."""
    if node_id in _NON_PERSON_BLOCKLIST:
        return True
    if len(node_id) > 15:
        return True
    if _DIGIT_UNDERSCORE_RE.search(node_id):
        return True
    if len(name) <= 2:
        return True
    if name.endswith(_ROLE_SUFFIXES):
        return True
    if name.startswith("[") or name.startswith("("):
        return True
    if "(주)" in name or "(사)" in name:
        return True
    if any(ph in name for ph in _PLACEHOLDER_NAMES):
        return True
    return False


def _reclassify_person(node_id: str) -> tuple[str | None, str | None]:
    """Reclassify a Person entity to a more specific type, or reject it.

    Returns:
        (corrected_id, corrected_type) — corrected_type is None if no reclassification,
        corrected_id is None if entity should be skipped.
    """
    name = node_id.strip()

    # Reclassify to other types
    if any(name.endswith(s) for s in _COMPANY_SUFFIXES):
        return node_id, "Store"
    if _LOCATION_SUFFIX_RE.match(name):
        return node_id, "Location"
    if name in _SYSTEM_NAMES:
        return node_id, "System"
    if name.endswith(_TEAM_SUFFIXES):
        return node_id, "Team"

    # Reject invalid persons
    if _is_invalid_person(node_id, name):
        return None, None

    # Clean parenthetical — extract name before parenthesis
    if "(" in name:
        _paren_match = _re_mod.match(r"^([가-힣]{2,4})[A-Z]?\s*\(", name)
        if _paren_match:
            return _paren_match.group(1), None

    return node_id, None  # Valid person, no reclassification


def _validate_entity(node_id: str, node_type: str) -> tuple[str | None, str]:
    """Validate and possibly reclassify an entity.

    Returns:
        (corrected_id, corrected_type) — corrected_id is None if entity should be skipped.
    """
    if _is_corrupted_entity(node_id):
        return None, node_type

    if node_type == "Person":
        corrected_id, new_type = _reclassify_person(node_id)
        if corrected_id is None:
            return None, node_type
        return corrected_id, new_type if new_type else node_type

    if node_type == "Store":
        if node_id in _PLATFORM_NAMES:
            return node_id, "System"
        if _PRODUCT_PATTERN_RE.search(node_id):
            return None, node_type

    return node_id, node_type

# Module-level shared executor for sync-in-async bridging (P2-5 perf fix)
_SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)


class _SageMakerLLMClient:
    """AWS SageMaker LLM adapter for GraphRAG extraction.

    Uses boto3 sagemaker-runtime to invoke a deployed EXAONE endpoint.
    Env vars: SAGEMAKER_ENDPOINT_NAME, SAGEMAKER_REGION, AWS_PROFILE.
    """

    def __init__(self) -> None:
        self._endpoint = os.getenv("SAGEMAKER_ENDPOINT_NAME", "")
        self._region = os.getenv("SAGEMAKER_REGION", "ap-northeast-2")
        self._profile = os.getenv("AWS_PROFILE", "")
        self._client = None
        if not self._endpoint:
            raise RuntimeError(
                "SAGEMAKER_ENDPOINT_NAME env var is required when using SageMaker LLM."
            )

    def _get_client(self) -> Any:
        # SSO token renewal workaround: recreate client each call
        # until IAM key migration, then switch to cached client
        # return self._client
        import boto3
        session = boto3.Session(profile_name=self._profile, region_name=self._region)
        return session.client("sagemaker-runtime")

    def invoke(self, *, document: str, prompt_template: str) -> str:
        import json as _json
        prompt = prompt_template.format(document=document)
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": _w.llm.graphrag_temperature,
        }
        resp = self._get_client().invoke_endpoint(
            EndpointName=self._endpoint,
            ContentType="application/json",
            Body=_json.dumps(body),
        )
        result = _json.loads(resp["Body"].read())
        return result["choices"][0]["message"]["content"]


class _OllamaLLMClient:
    """Local Ollama LLM adapter for GraphRAG extraction.

    Uses the OllamaClient from src.nlp.llm for LLM calls.
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url
        self._model = model
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig
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
        from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig
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
    def _run_in_new_loop(coro) -> Any:
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
from ._neo4j_persistence import Neo4jPersistenceMixin  # noqa: E402


class GraphRAGExtractor(Neo4jPersistenceMixin):
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
        from src.config import DEFAULT_LLM_MODEL, get_settings
        _s = get_settings()
        self.ollama_base_url = ollama_base_url or _s.ollama.base_url
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", DEFAULT_LLM_MODEL)
        self.neo4j_uri = neo4j_uri or _s.neo4j.uri
        self.neo4j_user = neo4j_user or _s.neo4j.user
        self.neo4j_password = neo4j_password or _s.neo4j.password
        if not self.neo4j_password:
            logger.warning("NEO4J_PASSWORD is empty — connection may fail")

        self._llm = llm_client
        self._neo4j_driver = neo4j_driver

    def _get_llm(self) -> Any:
        """LLM client 가져오기 (lazy loading).

        GRAPHRAG_USE_SAGEMAKER=true 이면 SageMaker 엔드포인트 사용,
        아니면 로컬 Ollama 사용.
        """
        if self._llm is None:
            use_sagemaker = os.getenv("GRAPHRAG_USE_SAGEMAKER", "false").lower() == "true"
            if use_sagemaker:
                self._llm = _SageMakerLLMClient()
                logger.info("GraphRAG LLM: SageMaker (%s)", self._llm._endpoint)
            else:
                self._llm = _OllamaLLMClient(
                    base_url=self.ollama_base_url,
                    model=self.ollama_model,
                )
                logger.info("GraphRAG LLM: Ollama (%s)", self.ollama_model)
        return self._llm

    def _get_neo4j_driver(self) -> Any:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
        result.kb_id = kb_id
        result.raw_response = raw_content

        logger.info(f"추출 완료: {result.node_count} nodes, {result.relationship_count} relationships")

        return result

    @staticmethod
    def _extract_json_str(content: str) -> str | None:
        """Extract JSON string from LLM response, stripping code fences."""
        if '```' in content:
            import re as _re
            _match = _re.search(r"```(?:json)?\s*\n?(.*?)```", content, _re.DOTALL)
            if _match:
                content = _match.group(1).strip()

        start = content.find('{')
        end = content.rfind('}') + 1
        if start < 0 or end <= start:
            return None
        return content[start:end]

    @staticmethod
    def _parse_json_with_repair(json_str: str) -> dict:
        """Parse JSON string, trying repair on failure."""
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            from json_repair import repair_json
            data = json.loads(repair_json(json_str))
            logger.warning("GraphRAG JSON repaired for document")
            return data

    @staticmethod
    def _parse_nodes(data: dict) -> list[GraphNode]:
        """Parse and validate nodes from extracted data."""
        nodes = []
        for node_data in data.get('nodes', []):
            node_id = node_data.get('id', '')
            node_type = node_data.get('type', 'Unknown')

            if node_type not in ALLOWED_NODES:
                logger.warning(f"허용되지 않은 노드 타입 무시: {node_type} (id={node_id})")
                continue
            if not node_id:
                continue

            validated_id, validated_type = _validate_entity(node_id, node_type)
            if validated_id is None:
                logger.debug("Entity filtered: id=%s, type=%s", node_id, node_type)
                continue
            if validated_type != node_type:
                logger.info("Entity reclassified: %s %s → %s", node_id, node_type, validated_type)

            nodes.append(GraphNode(
                id=validated_id,
                type=validated_type,
                properties={k: v for k, v in node_data.items() if k not in ('id', 'type')},
            ))
        return nodes

    @staticmethod
    def _parse_relationships(data: dict, node_ids: set[str]) -> list[GraphRelationship]:
        """Parse and validate relationships from extracted data."""
        relationships = []
        for rel_data in data.get('relationships', []):
            source = rel_data.get('source', '')
            target = rel_data.get('target', '')
            rel_type = rel_data.get('type', 'RELATED_TO')

            if rel_type not in ALLOWED_RELATIONSHIPS:
                rel_type = 'RELATED_TO'

            if not source or not target:
                continue

            if source not in node_ids or target not in node_ids:
                logger.warning(
                    f"Dangling reference: {source}-[{rel_type}]->{target} "
                    f"(추출된 노드에 없는 엔티티 참조)"
                )

            relationships.append(GraphRelationship(
                source=source, target=target, type=rel_type,
                properties={k: v for k, v in rel_data.items() if k not in ('source', 'target', 'type')},
            ))
        return relationships

    def _parse_response(self, content: str) -> ExtractionResult:
        """LLM 응답 파싱"""
        result = ExtractionResult()

        try:
            json_str = self._extract_json_str(content)
            if json_str is None:
                logger.warning("JSON을 찾을 수 없음")
                return result

            data = self._parse_json_with_repair(json_str)
            result.nodes = self._parse_nodes(data)
            node_ids = {n.id for n in result.nodes}
            result.relationships = self._parse_relationships(data, node_ids)

        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error(f"파싱 오류: {e}")

        return result

    @staticmethod
    def _build_node_properties(node, result: ExtractionResult) -> dict:
        """Build Neo4j properties dict for a single node."""
        properties = {"id": node.id, "name": node.id}
        for k, v in node.properties.items():
            if isinstance(v, (dict, list)):
                properties[k] = json.dumps(v, ensure_ascii=False)
            elif v is not None:
                properties[k] = v
        if result.source_page_id:
            properties["source_page_id"] = result.source_page_id
        if result.source_document:
            properties["source_document"] = result.source_document
        if result.kb_id:
            properties["kb_id"] = result.kb_id
        return properties

    def _prepare_node_batches(
        self, result: ExtractionResult, referenced_ids: set[str],
    ) -> tuple[dict[str, list[dict]], int]:
        """Group nodes by type for batch upsert, skipping orphans and unsafe labels.

        Returns:
            (nodes_by_type, skipped_orphan_count)
        """
        nodes_by_type: dict[str, list[dict]] = {}
        skipped_orphan = 0

        for node in result.nodes:
            if not _is_safe_cypher_label(node.type):
                logger.error(f"안전하지 않은 노드 타입 스킵: {node.type}")
                continue
            if node.id not in referenced_ids:
                skipped_orphan += 1
                continue

            properties = self._build_node_properties(node, result)
            nodes_by_type.setdefault(node.type, []).append(properties)

        return nodes_by_type, skipped_orphan

    def _upsert_node_batches(
        self, session, nodes_by_type: dict[str, list[dict]], now: str,
    ) -> tuple[int, int]:
        """Upsert node batches to Neo4j. Returns (created, updated) counts."""
        created = 0
        updated = 0
        for node_type, node_params in nodes_by_type.items():
            try:
                batch_query = f"""
                    UNWIND $nodes AS props
                    MERGE (n:{node_type} {{id: props.id}})
                    ON CREATE SET n.created_at = $now, n.updated_at = $now, n += props
                    ON MATCH SET n.updated_at = $now, n += props
                    SET n:__Entity__
                    RETURN n.created_at = $now AS is_new
                """
                records = session.run(batch_query, nodes=node_params, now=now)
                for rec in records:
                    if rec and rec["is_new"]:
                        created += 1
                    else:
                        updated += 1
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.error(f"노드 배치 생성 실패 (type={node_type}): {e}")
        return created, updated

    # Neo4j persistence → _neo4j_persistence.py (Neo4jPersistenceMixin)

class GraphRAGBatchProcessor:
    """배치 처리기 - 여러 문서를 순차적으로 처리"""

    def __init__(self, extractor: GraphRAGExtractor | None = None) -> None:
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

            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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
