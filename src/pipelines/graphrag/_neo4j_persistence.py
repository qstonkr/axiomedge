# pyright: reportAttributeAccessIssue=false
"""GraphRAG Neo4j persistence methods — mixin for GraphRAGExtractor.

Extracted from extractor.py for SRP.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.stores.neo4j.errors import NEO4J_FAILURE

from .models import ExtractionResult, GraphRelationship
from .prompts import HISTORY_RELATIONSHIP_MAP, _is_safe_cypher_label

logger = logging.getLogger(__name__)


class Neo4jPersistenceMixin:
    """Neo4j save/query methods. Host must have _get_neo4j_driver()."""

    def save_to_neo4j(self, result: ExtractionResult) -> dict[str, int]:
        """추출 결과를 Neo4j에 저장 (이력 보존 + 최신성 기반 업데이트)"""
        driver = self._get_neo4j_driver()
        now = datetime.now(UTC).isoformat()
        source_updated = result.source_updated_at or now

        stats = {
            "nodes_created": 0, "nodes_updated": 0,
            "relationships_created": 0, "relationships_updated": 0,
            "relationships_archived": 0, "relationships_skipped": 0,
        }

        referenced_ids = {rel.source for rel in result.relationships} | {
            rel.target for rel in result.relationships
        }

        nodes_by_type, skipped_orphan = self._prepare_node_batches(result, referenced_ids)
        if skipped_orphan:
            logger.info(f"고아노드 방지: 관계 없는 노드 {skipped_orphan}개 skip")

        with driver.session() as session:
            created, updated = self._upsert_node_batches(session, nodes_by_type, now)
            stats["nodes_created"] = created
            stats["nodes_updated"] = updated

            node_type_map: dict[str, str] = {n.id: n.type for n in result.nodes}

            for rel in result.relationships:
                try:
                    if not _is_safe_cypher_label(rel.type):
                        logger.error(f"안전하지 않은 관계 타입 스킵: {rel.type}")
                        continue
                    rel_stats = self._save_relationship_with_history(
                        session, rel, result, source_updated, now,
                        node_type_map=node_type_map,
                    )
                    stats["relationships_created"] += rel_stats.get("created", 0)
                    stats["relationships_updated"] += rel_stats.get("updated", 0)
                    stats["relationships_archived"] += rel_stats.get("archived", 0)
                    stats["relationships_skipped"] += rel_stats.get("skipped", 0)
                except NEO4J_FAILURE as e:
                    # 과거: (OSError, RuntimeError, ValueError) 로 narrow —
                    # CypherSyntaxError/ClientError/ServiceUnavailable 모두 놓쳐
                    # 단일 관계 실패가 배치 전체 abort. NEO4J_FAILURE 로 확장해
                    # per-relationship 로그 후 다음 관계로 진행.
                    logger.error(f"관계 생성 실패 ({rel.source}->{rel.target}): {e}")

        logger.info(f"Neo4j 저장 완료: {stats}")
        return stats

    def _resolve_node_labels(
        self, rel: GraphRelationship, node_type_map: dict[str, str] | None,
    ) -> tuple[str, str]:
        """Resolve Cypher-safe node type labels for source and target."""
        if not node_type_map:
            return "", ""
        src_label = ""
        tgt_label = ""
        src_type = node_type_map.get(rel.source)
        tgt_type = node_type_map.get(rel.target)
        if src_type and _is_safe_cypher_label(src_type):
            src_label = f":{src_type}"
        if tgt_type and _is_safe_cypher_label(tgt_type):
            tgt_label = f":{tgt_type}"
        return src_label, tgt_label

    def _handle_existing_records(
        self,
        session,
        existing: list,
        rel: GraphRelationship,
        result: ExtractionResult,
        source_updated: str,
        now: str,
        src_label: str,
        tgt_label: str,
    ) -> dict[str, int]:
        """Process existing relationship records for history-aware update."""
        stats = {"created": 0, "updated": 0, "archived": 0, "skipped": 0}
        new_rel_created = False

        for record in existing:
            existing_target = record["target"]
            existing_updated = record["updated_at"] or "1970-01-01"

            if existing_target == rel.target:
                update_query = f"""
                    MATCH (a{src_label} {{id: $source}})-[r:{rel.type}]->(b{tgt_label} {{id: $target}})
                    SET r.updated_at = $now,
                        r.source_page_id = $source_page_id,
                        r.source_document = $source_document
                    RETURN r
                """
                session.run(
                    update_query,
                    source=rel.source, target=rel.target, now=now,
                    source_page_id=result.source_page_id,
                    source_document=result.source_document,
                )
                stats["updated"] += 1
                continue

            if not self._is_newer(source_updated, existing_updated):
                logger.info(
                    f"Skip: {rel.source}-[{rel.type}]->{rel.target} "
                    f"(기존 {existing_target}이 더 최신)"
                )
                stats["skipped"] += 1
                continue

            self._archive_relationship(session, rel.source, rel.type, existing_target, now)
            stats["archived"] += 1
            if not new_rel_created:
                self._create_relationship(
                    session, rel, result, source_updated, now,
                    src_label=src_label, tgt_label=tgt_label,
                )
                stats["created"] += 1
                new_rel_created = True

        return stats

    def _save_relationship_with_history(
        self,
        session,
        rel: GraphRelationship,
        result: ExtractionResult,
        source_updated: str,
        now: str,
        node_type_map: dict[str, str] | None = None,
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
        src_label, tgt_label = self._resolve_node_labels(rel, node_type_map)

        check_query = f"""
            MATCH (a{src_label} {{id: $source}})-[r:{rel.type}]->(b)
            RETURN b.id AS target, r.updated_at AS updated_at, r.source_page_id AS source_page_id
        """
        existing = list(session.run(check_query, source=rel.source))

        if not existing:
            self._create_relationship(
                session, rel, result, source_updated, now,
                src_label=src_label, tgt_label=tgt_label,
            )
            return {"created": 1, "updated": 0, "archived": 0, "skipped": 0}

        return self._handle_existing_records(
            session, existing, rel, result, source_updated, now,
            src_label, tgt_label,
        )

    def _create_relationship(
        self,
        session,
        rel: GraphRelationship,
        result: ExtractionResult,
        source_updated: str,
        now: str,
        src_label: str = "",
        tgt_label: str = "",
    ) -> None:
        """새 관계 생성"""
        query = f"""
            MATCH (a{src_label} {{id: $source}})
            MATCH (b{tgt_label} {{id: $target}})
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
            properties={
                k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in rel.properties.items()
                if v is not None
            },
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
        except NEO4J_FAILURE:
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
        except NEO4J_FAILURE as e:
            logger.warning(f"최근 엔티티 조회 실패: {e}")
            return []

    def close(self) -> None:
        """리소스 정리"""
        if self._neo4j_driver:
            self._neo4j_driver.close()
            self._neo4j_driver = None


# =============================================================================
# Batch Processor
# =============================================================================
