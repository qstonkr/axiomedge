"""Admin API endpoints - graph stats, graph operations, Qdrant collections, config weights."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from src.api.app import _get_state
from src.config_weights import weights

logger = logging.getLogger(__name__)

_GRAPH_INTEGRITY_FAILED = "Graph integrity check failed: %s"
router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# ============================================================================
# Graph - Stats
# ============================================================================

@router.get("/graph/stats")
async def graph_stats():
    """Get knowledge graph statistics."""
    state = _get_state()
    graph = state.get("graph_repo")
    if not graph:
        return {"nodes": 0, "edges": 0, "node_types": {}, "edge_types": {}}

    try:
        stats = await graph.get_stats()
        return stats
    except Exception as e:
        logger.warning("Graph stats failed: %s", e)
        return {"nodes": 0, "edges": 0, "error": str(e)}


# ============================================================================
# Graph - Search (POST - dashboard sends POST with body)
# ============================================================================

@router.post("/graph/search")
async def graph_search(body: dict[str, Any]):
    """Search the knowledge graph."""
    state = _get_state()
    graph = state.get("graph_repo")
    query = str(body.get("query", ""))[:500]  # limit query length
    _max_hops = min(int(body.get("max_hops", 2)), 5)  # cap at 5 hops
    max_nodes = min(int(body.get("max_nodes", 50)), 200)  # cap at 200

    if not graph:
        return {"query": query, "nodes": [], "edges": [], "total": 0}

    try:
        keywords = [k.strip() for k in query.split() if k.strip()]
        raw_results = await graph.search_entities(keywords, max_facts=max_nodes)

        # Group flat results into entity-centric format for dashboard
        entities_map: dict[str, dict[str, Any]] = {}
        for r in raw_results:
            name = r.get("name") or r.get("entity_id") or ""
            if not name:
                continue
            node_type = r.get("node_type", "CONCEPT")
            if name not in entities_map:
                entities_map[name] = {
                    "name": name,
                    "type": node_type,
                    "entity_id": r.get("entity_id", name),
                    "score": r.get("score", 0),
                    "relationships": [],
                }
            rel_type = r.get("rel_type")
            connected = r.get("connected_name")
            connected_type = r.get("connected_type", "")
            if rel_type and connected:
                entities_map[name]["relationships"].append({
                    "type": rel_type,
                    "target": connected,
                    "target_type": connected_type,
                })

        entities = sorted(entities_map.values(), key=lambda x: x.get("score", 0), reverse=True)
        return {"query": query, "entities": entities, "total": len(entities)}
    except Exception as e:
        logger.warning("Graph search failed: %s", e)
        return {"query": query, "entities": [], "total": 0, "error": str(e)}


# ============================================================================
# Graph - Experts (POST - dashboard sends POST with body)
# ============================================================================

@router.get("/graph/experts")
async def find_experts(
    topic: Annotated[str, Query()] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """Find experts for a topic."""
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {"topic": topic, "experts": []}

    try:
        experts = await graph.find_experts(topic)
        return {"topic": topic, "experts": experts[:limit]}
    except Exception as e:
        logger.warning("Expert search failed: %s", e)
        return {"topic": topic, "experts": [], "error": str(e)}


# ============================================================================
# Graph - Expand
# ============================================================================

@router.post("/graph/expand")
async def graph_expand(body: dict[str, Any]):
    """Expand a graph node to show neighbors."""
    state = _get_state()
    graph = state.get("graph_repo")
    node_id = str(body.get("node_id", ""))[:200]

    if not graph:
        return {"node_id": node_id, "neighbors": [], "edges": []}

    try:
        max_neighbors = body.get("max_neighbors", 30)
        # Try expand if available, else return empty
        if hasattr(graph, "expand_node"):
            result = await graph.expand_node(node_id, max_neighbors=max_neighbors)
            return result
        return {"node_id": node_id, "neighbors": [], "edges": []}
    except Exception as e:
        logger.warning("Graph expand failed: %s", e)
        return {"node_id": node_id, "neighbors": [], "edges": [], "error": str(e)}


# ============================================================================
# Graph - Integrity Check
# ============================================================================

@router.post("/graph/integrity/check")
async def graph_integrity_check():
    """Check graph data integrity: orphan nodes, missing relationships, inconsistencies."""
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {
            "total_nodes": 0, "total_edges": 0,
            "orphan_count": 0, "missing_relationships": 0, "inconsistencies": 0,
            "details": [],
        }

    try:
        client = graph._client
        issues: list[dict[str, Any]] = []

        # Total counts
        node_result = await client.execute_query("MATCH (n) RETURN count(n) AS cnt", {})
        total_nodes = node_result[0]["cnt"] if node_result else 0

        edge_result = await client.execute_query("MATCH ()-[r]->() RETURN count(r) AS cnt", {})
        total_edges = edge_result[0]["cnt"] if edge_result else 0

        # Orphan nodes (no relationships at all)
        orphan_result = await client.execute_query(
            "MATCH (n) WHERE NOT (n)--() RETURN count(n) AS cnt", {}
        )
        orphan_count = orphan_result[0]["cnt"] if orphan_result else 0

        if orphan_count > 0:
            # Get sample orphans
            orphan_samples = await client.execute_query(
                "MATCH (n) WHERE NOT (n)--() "
                "RETURN [l IN labels(n) WHERE l <> '__Entity__'][0] AS type, "
                "COALESCE(n.name, n.id, n.title) AS name LIMIT 10", {}
            )
            for s in orphan_samples:
                issues.append({
                    "type": "ORPHAN_NODE",
                    "severity": "LOW",
                    "description": f"[{s.get('type', '?')}] {s.get('name', '?')} — 관계 없음",
                })

        # Documents without category
        no_cat_result = await client.execute_query(
            "MATCH (d:Document) WHERE NOT (d)-[:CATEGORIZED_AS]->() "
            "RETURN count(d) AS cnt", {}
        )
        no_cat = no_cat_result[0]["cnt"] if no_cat_result else 0

        if no_cat > 0:
            issues.append({
                "type": "MISSING_CATEGORY",
                "severity": "MEDIUM",
                "description": f"{no_cat}개 문서에 카테고리 미할당",
            })

        # Documents without owner
        no_owner_result = await client.execute_query(
            "MATCH (d:Document) WHERE NOT (d)<-[:OWNS]-() AND NOT (d)<-[:AUTHORED]-() "
            "RETURN count(d) AS cnt", {}
        )
        no_owner = no_owner_result[0]["cnt"] if no_owner_result else 0

        if no_owner > 0:
            issues.append({
                "type": "MISSING_OWNER",
                "severity": "MEDIUM",
                "description": f"{no_owner}개 문서에 담당자 미할당",
            })

        # Dangling references (entities pointing to non-existent targets)
        dangling_result = await client.execute_query(
            "MATCH (a)-[r]->(b) WHERE b.id IS NULL AND b.name IS NULL "
            "RETURN count(r) AS cnt", {}
        )
        dangling = dangling_result[0]["cnt"] if dangling_result else 0

        missing_rels = no_cat + no_owner
        inconsistencies = dangling

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "orphan_count": orphan_count,
            "missing_relationships": missing_rels,
            "inconsistencies": inconsistencies,
            "details": issues,
        }
    except Exception as e:
        logger.warning(_GRAPH_INTEGRITY_FAILED, e)
        return {
            "total_nodes": 0, "total_edges": 0,
            "orphan_count": 0, "missing_relationships": 0, "inconsistencies": 0,
            "details": [], "error": str(e),
        }


# ============================================================================
# Graph - Shortest Path
# ============================================================================

@router.post("/graph/path")
async def graph_path(body: dict[str, Any]):
    """Find shortest path between two nodes."""
    from_id = body.get("from_node_id", "")
    to_id = body.get("to_node_id", "")
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {"from_node_id": from_id, "to_node_id": to_id, "path": [], "length": 0}

    try:
        if hasattr(graph, "shortest_path"):
            result = await graph.shortest_path(from_id, to_id)
            return result
        return {"from_node_id": from_id, "to_node_id": to_id, "path": [], "length": 0}
    except Exception as e:
        logger.warning("Graph path failed: %s", e)
        return {"from_node_id": from_id, "to_node_id": to_id, "path": [], "error": str(e)}


# ============================================================================
# Graph - Communities
# ============================================================================

@router.get("/graph/communities")
async def graph_communities():
    """Get graph communities."""
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {"communities": [], "total": 0}

    try:
        if hasattr(graph, "get_communities"):
            result = await graph.get_communities()
            return result
        return {"communities": [], "total": 0}
    except Exception as e:
        logger.warning("Graph communities failed: %s", e)
        return {"communities": [], "total": 0, "error": str(e)}


# ============================================================================
# Graph - Integrity
# ============================================================================

@router.get("/graph/integrity")
async def graph_integrity():
    """Get graph integrity report."""
    state = _get_state()
    checker = state.get("graph_integrity")

    if not checker:
        return {
            "status": "ok",
            "orphan_nodes": 0,
            "dangling_edges": 0,
            "missing_relationships": 0,
            "total_issues": 0,
            "issues": [],
            "last_check": None,
        }

    try:
        report = await checker.check_integrity()
        result = report.to_dict()
        result["last_check"] = None  # Could be stored if needed
        return result
    except Exception as e:
        logger.warning(_GRAPH_INTEGRITY_FAILED, e)
        return {
            "status": "error",
            "orphan_nodes": 0,
            "dangling_edges": 0,
            "missing_relationships": 0,
            "total_issues": 0,
            "issues": [],
            "error": str(e),
        }


@router.post("/graph/integrity/run")
async def run_graph_integrity_check(body: dict[str, Any] | None = None):
    """Run a graph integrity check with optional KB scope."""
    state = _get_state()
    checker = state.get("graph_integrity")
    kb_id = (body or {}).get("kb_id") if body else None

    if not checker:
        return {
            "success": True,
            "status": "ok",
            "orphan_nodes": 0,
            "dangling_edges": 0,
            "missing_relationships": 0,
            "total_issues": 0,
            "issues": [],
        }

    try:
        report = await checker.check_integrity(kb_id=kb_id)
        result = report.to_dict()
        result["success"] = True
        return result
    except Exception as e:
        logger.warning(_GRAPH_INTEGRITY_FAILED, e)
        return {
            "success": False,
            "status": "error",
            "orphan_nodes": 0,
            "dangling_edges": 0,
            "missing_relationships": 0,
            "total_issues": 0,
            "issues": [],
            "error": str(e),
        }


# ============================================================================
# Graph - Impact Analysis
# ============================================================================

@router.post("/graph/impact")
async def graph_impact(body: dict[str, Any]):
    """Analyze impact of a node using multi-hop search."""
    node_id = body.get("node_id", "")
    max_hops = body.get("max_hops", 2)
    state = _get_state()
    searcher = state.get("multi_hop_searcher")

    if not searcher:
        return {
            "node_id": node_id,
            "impacted_nodes": [],
            "total_impacted": 0,
            "max_hops": max_hops,
        }

    try:
        related = await searcher.search_related(
            doc_id=node_id,
            max_hops=max_hops,
            limit=50,
        )
        impacted = [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "distance": r.distance,
                "relation_types": r.relation_types,
                "relevance_score": r.relevance_score,
            }
            for r in related
        ]
        return {
            "node_id": node_id,
            "impacted_nodes": impacted,
            "total_impacted": len(impacted),
            "max_hops": max_hops,
        }
    except Exception as e:
        logger.warning("Graph impact analysis failed: %s", e)
        return {
            "node_id": node_id,
            "impacted_nodes": [],
            "total_impacted": 0,
            "max_hops": max_hops,
            "error": str(e),
        }


# ============================================================================
# Graph - Health
# ============================================================================

@router.get("/graph/health")
async def graph_health():
    """Get graph health."""
    state = _get_state()
    graph = state.get("graph_repo")
    connected = graph is not None

    if connected:
        try:
            stats = await graph.get_stats()
            return {
                "status": "healthy",
                "connected": True,
                "nodes": stats.get("nodes", 0),
                "edges": stats.get("edges", 0),
            }
        except Exception as e:
            return {"status": "degraded", "connected": True, "error": str(e)}

    return {"status": "disconnected", "connected": False}


# ============================================================================
# Graph - Timeline
# ============================================================================

@router.post("/graph/timeline")
async def graph_timeline(body: dict[str, Any]):
    """Get timeline for a node."""
    node_id = body.get("node_id", "")
    return {
        "node_id": node_id,
        "events": [],
        "total": 0,
    }


# ============================================================================
# Qdrant Collections
# ============================================================================

@router.get("/qdrant/collections")
async def list_collections():
    """List Qdrant collections."""
    state = _get_state()
    collections = state.get("qdrant_collections")
    if not collections:
        return {"collections": []}

    try:
        names = await collections.get_existing_collection_names()
        return {"collections": names}
    except Exception as e:
        return {"collections": [], "error": str(e)}


@router.get("/qdrant/collection/{name}/stats", responses={503: {"description": "Store not initialized"}, 500: {"description": "Internal error"}})
async def collection_stats(name: str):
    """Get collection statistics."""
    state = _get_state()
    store = state.get("qdrant_store")
    if not store:
        raise HTTPException(status_code=503, detail="Store not initialized")

    try:
        count = await store.count(name)
        return {"collection": name, "point_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Graph - Cleanup
# ============================================================================

@router.post("/graph/cleanup")
async def graph_cleanup(body: dict[str, Any] | None = None):
    """Run graph quality cleanup: remove placeholders, reclassify mismatches, etc.

    Body (all optional):
        apply (bool): False = dry run (default), True = apply fixes
        kb_id (str): Filter to a single KB
    """
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {
            "success": False,
            "error": "Graph repository not available",
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }

    body = body or {}
    apply = body.get("apply", False)
    kb_id = body.get("kb_id")

    try:
        from scripts.graph_cleanup import run_cleanup

        results = await asyncio.to_thread(run_cleanup, apply=apply, kb_id=kb_id)

        total_found = sum(r.get("found", 0) for r in results)
        total_fixed = sum(r.get("fixed", 0) for r in results)

        return {
            "success": True,
            "mode": "apply" if apply else "dry_run",
            "kb_id": kb_id,
            "tasks": results,
            "total_found": total_found,
            "total_fixed": total_fixed,
        }
    except Exception as e:
        logger.warning("Graph cleanup failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }


@router.post("/graph/cleanup/analyze")
async def graph_cleanup_analyze(body: dict[str, Any] | None = None):
    """Analyze graph quality issues without applying fixes (always dry run)."""

    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {
            "success": False,
            "error": "Graph repository not available",
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }

    kb_id = (body or {}).get("kb_id")

    try:
        from scripts.graph_cleanup import run_cleanup

        results = await asyncio.to_thread(run_cleanup, apply=False, kb_id=kb_id)

        total_found = sum(r.get("found", 0) for r in results)

        return {
            "success": True,
            "mode": "dry_run",
            "kb_id": kb_id,
            "tasks": results,
            "total_found": total_found,
            "total_fixed": 0,
        }
    except Exception as e:
        logger.warning("Graph cleanup analysis failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "tasks": [],
            "total_found": 0,
            "total_fixed": 0,
        }


# ============================================================================
# Graph - AI Classification (LLM-based entity reclassification)
# ============================================================================

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


def _fetch_ai_classify_candidates(
    kb_id: str | None, limit: int,
) -> list[dict[str, Any]]:
    """Fetch misclassified/ambiguous nodes from Neo4j (sync, for asyncio.to_thread)."""
    import os

    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
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

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
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
    except Exception as e:
        logger.warning("AI classify apply error for %s: %s", eid, e)
        stats["errors"] += 1


def _parse_llm_json_response(text: str) -> list[dict[str, Any]]:
    """Extract JSON array from LLM response text.

    3단계 fallback: direct parse → markdown code block → regex array extract.
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
        from src.llm.sagemaker_client import SageMakerLLMClient
        logger.info("AI classify: using SageMaker LLM (fallback)")
        return SageMakerLLMClient()
    except Exception as e:
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


@router.post("/graph/cleanup/ai-classify")
async def graph_ai_classify(body: dict[str, Any] | None = None):
    """LLM-based entity reclassification using SageMaker EXAONE.

    Body (all optional):
        limit (int): Max nodes to process (default 200)
        apply (bool): False = dry run (default), True = apply reclassification
        kb_id (str): Filter to a single KB
    """
    llm = _resolve_llm_client(_get_state())
    if not llm:
        return {
            "success": False,
            "error": "LLM client not available. Set USE_SAGEMAKER_LLM=true or start Ollama.",
            "candidates": 0,
            "classifications": [],
            "stats": {},
        }

    body = body or {}
    limit = body.get("limit", 200)
    if limit != 0:
        limit = min(max(limit, 10), 10000)
    apply = body.get("apply", False)
    kb_id = body.get("kb_id")

    try:
        candidates = await asyncio.to_thread(
            _fetch_ai_classify_candidates, kb_id, limit,
        )
        if not candidates:
            return {
                "success": True,
                "mode": "apply" if apply else "dry_run",
                "candidates": 0,
                "classifications": [],
                "stats": {"relabeled": 0, "deleted": 0, "skipped": 0},
            }

        batch_size = 30
        all_classifications: list[dict[str, Any]] = []
        for i in range(0, len(candidates), batch_size):
            try:
                batch_result = await _classify_batch(llm, candidates[i : i + batch_size])
                all_classifications.extend(batch_result)
            except Exception as e:
                logger.warning("AI classify LLM batch %d failed: %s", i // batch_size, e)

        stats: dict[str, int] = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        if apply and all_classifications:
            stats = await asyncio.to_thread(
                _apply_ai_classifications, all_classifications,
            )

        return {
            "success": True,
            "mode": "apply" if apply else "dry_run",
            "candidates": len(candidates),
            "classifications": [
                {
                    "name": c["name"],
                    "current_label": c["current_label"],
                    "new_type": c["type"],
                    "reason": c["reason"],
                    "kb_id": c.get("kb_id"),
                }
                for c in all_classifications
            ],
            "stats": stats,
        }
    except Exception as e:
        logger.warning("AI classify failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "candidates": 0,
            "classifications": [],
            "stats": {},
        }


# ============================================================================
# Config Weights - Hot Reload
# ============================================================================

@router.post("/config/weights")
async def get_config_weights():
    """Return current config weights."""
    return weights.to_dict()


@router.put("/config/weights", responses={400: {"description": "Empty body or no valid weight fields matched"}})
async def update_config_weights(body: dict[str, Any]):
    """Update specific weight values (partial update).

    Accepts either flat keys ``{"section.field": value}``
    or nested ``{"section": {"field": value}}``.
    """
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    applied = weights.update_from_dict(body)
    if not applied:
        raise HTTPException(
            status_code=400,
            detail="No valid weight fields matched. Use 'section.field' or {'section': {'field': value}}.",
        )
    logger.info("Config weights updated: %s", applied)
    return {"applied": applied, "current": weights.to_dict()}


@router.post("/config/weights/reset")
async def reset_config_weights():
    """Reset all config weights to their defaults."""
    weights.reset()
    logger.info("Config weights reset to defaults")
    return {"status": "reset", "current": weights.to_dict()}
