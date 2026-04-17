"""Admin route helpers — AI classification, graph cleanup, and shared utilities.

Extracted from admin.py to keep route handlers thin.
All public names are re-exported from admin.py for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_GRAPH_INTEGRITY_FAILED = "Graph integrity check failed: %s"

AI_CLASSIFY_PROMPT = """다음 엔티티 목록의 올바른 타입을 분류하세요.
가능한 타입: Person, Store, System, Location, Team, Process, Product, Policy, Role, Event, Term, DELETE(삭제대상)

각 엔티티에 대해 JSON 배열로 응답하세요:
[{{"name": "엔티티명", "type": "올바른타입", "reason": "이유"}}]

엔티티 목록:
{entities}
"""

_KOREAN_NAME_RE = re.compile(r"^[가-힣]{2,4}$")
_VALID_LABELS = {
    "Person", "Store", "System", "Location", "Team",
    "Process", "Product", "Policy", "Role", "Event", "Term",
}


# ── AI Classification helpers ────────────────────────────────────────────────


def _fetch_ai_classify_candidates(
    kb_id: str | None, limit: int,
) -> list[dict[str, Any]]:
    """Fetch misclassified/ambiguous nodes from Neo4j (sync, for asyncio.to_thread)."""
    import os

    from neo4j import GraphDatabase

    uri = get_settings().neo4j.uri
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    auth = (user, password) if password else None
    driver = GraphDatabase.driver(uri, auth=auth)

    kb_clause = " AND n.kb_id = $kb_id" if kb_id else ""
    params: dict[str, Any] = {}
    if kb_id:
        params["kb_id"] = kb_id
    candidates: list[dict[str, Any]] = []
    # limit=0 means fetch all
    limit_clause1 = f"LIMIT {limit // 2}" if limit > 0 else ""
    limit_clause2 = f"LIMIT {limit - limit // 2}" if limit > 0 else ""

    try:
        with driver.session(database=database) as session:
            # 1. Person nodes that don't match Korean name pattern
            q1 = (
                "MATCH (n:Person) "
                f"WHERE NOT n.name =~ '^[가-힣]{{2,4}}$'{kb_clause} "
                "RETURN elementId(n) AS eid, n.name AS name, "
                f"'Person' AS current_label, n.kb_id AS kb_id {limit_clause1}"
            )
            result1 = session.run(q1, params)
            for record in result1:
                candidates.append(dict(record))

            # 2. __Entity__-only nodes (no type label)
            q2 = (
                "MATCH (n:__Entity__) "
                "WHERE size([l IN labels(n) WHERE l <> '__Entity__']) = 0 "
                f"AND n.name IS NOT NULL{kb_clause} "
                "RETURN elementId(n) AS eid, n.name AS name, "
                f"'__Entity__' AS current_label, n.kb_id AS kb_id {limit_clause2}"
            )
            result2 = session.run(q2, params)
            for record in result2:
                candidates.append(dict(record))
    finally:
        driver.close()

    return candidates[:limit] if limit > 0 else candidates


def _apply_ai_classifications(
    classifications: list[dict[str, Any]],
) -> dict[str, int]:
    """Apply LLM classification results to Neo4j (sync, for asyncio.to_thread)."""
    import os

    from neo4j import GraphDatabase

    uri = get_settings().neo4j.uri
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    auth = (user, password) if password else None
    driver = GraphDatabase.driver(uri, auth=auth)

    stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}

    try:
        with driver.session(database=database) as session:
            for item in classifications:
                _apply_single_classification(session, item, stats)
    finally:
        driver.close()

    return stats


def _apply_single_classification(
    session: Any,
    item: dict[str, Any],
    stats: dict[str, int],
) -> None:
    """Apply a single classification result to Neo4j."""
    eid = item.get("eid")
    new_type = item.get("type", "").strip()
    old_label = item.get("current_label", "")

    if not eid or not new_type:
        stats["skipped"] += 1
        return

    try:
        if new_type == "DELETE":
            session.run(
                "MATCH (n) WHERE elementId(n) = $eid DETACH DELETE n",
                parameters={"eid": eid},
            )
            stats["deleted"] += 1
        elif new_type not in _VALID_LABELS or new_type == old_label:
            stats["skipped"] += 1
        else:
            # Remove old label (if not __Entity__) and set new label
            remove_clause = f"REMOVE n:{old_label} " if old_label and old_label != "__Entity__" else ""
            session.run(
                f"MATCH (n) WHERE elementId(n) = $eid {remove_clause}SET n:{new_type}",
                parameters={"eid": eid},
            )
            stats["relabeled"] += 1
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("AI classify apply error for %s: %s", eid, e)
        stats["errors"] += 1


def _parse_llm_json_response(text: str) -> list[dict[str, Any]]:
    """Extract JSON array from LLM response text.

    3단계 fallback: direct parse -> markdown code block -> regex array extract.
    모든 단계 실패 시 빈 리스트를 반환하지만, 실패 이유는 debug 로그로 남겨
    LLM 출력 포맷 변화/편차를 추적 가능하게 함.
    """
    text = text.strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError as e:
        logger.debug("LLM JSON parse (direct) failed: %s", e)

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError as e:
            logger.debug("LLM JSON parse (markdown block) failed: %s", e)

    # Try finding array in text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError as e:
            logger.debug("LLM JSON parse (regex array) failed: %s", e)

    logger.warning(
        "Failed to parse LLM JSON response after 3 attempts (preview: %r)",
        text[:200],
    )
    return []


def _resolve_llm_client(state: dict[str, Any]) -> Any | None:
    """Resolve LLM client from state or SageMaker fallback."""
    llm = state.get("llm_client")
    if llm:
        return llm
    import os
    if os.getenv("USE_SAGEMAKER_LLM", "false").lower() not in ("true", "1"):
        return None
    try:
        from src.nlp.llm.sagemaker_client import SageMakerLLMClient
        logger.info("AI classify: using SageMaker LLM (fallback)")
        return SageMakerLLMClient()
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("SageMaker LLM init failed: %s", e)
        return None


async def _classify_batch(
    llm: Any, batch: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify a single batch of candidates using LLM."""
    entity_lines = "\n".join(
        f"- {c['name']} (현재: {c['current_label']})" for c in batch
    )
    prompt = AI_CLASSIFY_PROMPT.format(entities=entity_lines)
    response = await llm.generate(prompt, temperature=0.1, max_tokens=4096)
    parsed = _parse_llm_json_response(response)

    name_to_candidate = {c["name"]: c for c in batch}
    results: list[dict[str, Any]] = []
    for item in parsed:
        name = item.get("name", "")
        candidate = name_to_candidate.get(name)
        if candidate:
            results.append({
                "eid": candidate["eid"],
                "name": name,
                "current_label": candidate["current_label"],
                "type": item.get("type", ""),
                "reason": item.get("reason", ""),
                "kb_id": candidate.get("kb_id"),
            })
    return results
