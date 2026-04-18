"""Admin graph route handlers — extracted from admin.py.

Contains: graph stats, search, experts, expand, path, communities,
integrity, impact, health, timeline.
Cleanup + AI classify routes are in ``_admin_cleanup.py``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query

from src.api.routes.admin_helpers import _GRAPH_INTEGRITY_FAILED


def _get_state() -> Any:  # AppState (dict-compatible)
    """Late-bound accessor — keeps ``patch.object(admin, '_get_state')`` in tests working.

    Uses the parent ``admin`` module's binding so unit tests that
    ``patch.object(admin_mod, '_get_state')`` affect this code too.
    """
    import src.api.routes.admin as _admin
    return _admin._get_state()
from src.api.routes._admin_cleanup import router as _cleanup_router  # noqa: F401
from src.api.routes._admin_cleanup import (  # noqa: F401
    graph_cleanup,
    graph_cleanup_analyze,
    graph_ai_classify,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# ============================================================================
# Graph - Stats
# ============================================================================

@router.get("/graph/stats")
async def graph_stats() -> dict[str, Any]:
    """Get knowledge graph statistics."""
    state = _get_state()
    graph = state.get("graph_repo")
    if not graph:
        return {"nodes": 0, "edges": 0, "node_types": {}, "edge_types": {}}

    try:
        stats = await graph.get_stats()
        return stats
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Graph stats failed: %s", e)
        return {"nodes": 0, "edges": 0, "error": str(e)}


# ============================================================================
# Graph - Search (POST - dashboard sends POST with body)
# ============================================================================

@router.post("/graph/search")
async def graph_search(body: dict[str, Any]) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Graph search failed: %s", e)
        return {"query": query, "entities": [], "total": 0, "error": str(e)}


# ============================================================================
# Graph - Experts
# ============================================================================

@router.get("/graph/experts")
async def find_experts(
    topic: Annotated[str, Query()] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Find experts for a topic."""
    state = _get_state()
    graph = state.get("graph_repo")

    if not graph:
        return {"topic": topic, "experts": []}

    try:
        experts = await graph.find_experts(topic)
        return {"topic": topic, "experts": experts[:limit]}
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Expert search failed: %s", e)
        return {"topic": topic, "experts": [], "error": str(e)}


# ============================================================================
# Graph - Expand
# ============================================================================

@router.post("/graph/expand")
async def graph_expand(body: dict[str, Any]) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Graph expand failed: %s", e)
        return {"node_id": node_id, "neighbors": [], "edges": [], "error": str(e)}


# ============================================================================
# Graph - Integrity Check
# ============================================================================

@router.post("/graph/integrity/check")
async def graph_integrity_check() -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
async def graph_path(body: dict[str, Any]) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Graph path failed: %s", e)
        return {"from_node_id": from_id, "to_node_id": to_id, "path": [], "error": str(e)}


# ============================================================================
# Graph - Communities
# ============================================================================

@router.get("/graph/communities")
async def graph_communities() -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Graph communities failed: %s", e)
        return {"communities": [], "total": 0, "error": str(e)}


# ============================================================================
# Graph - Integrity
# ============================================================================

@router.get("/graph/integrity")
async def graph_integrity() -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
async def run_graph_integrity_check(body: dict[str, Any] | None = None) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
async def graph_impact(body: dict[str, Any]) -> dict[str, Any]:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
async def graph_health() -> dict[str, Any]:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            return {"status": "degraded", "connected": True, "error": str(e)}

    return {"status": "disconnected", "connected": False}


# ============================================================================
# Graph - Timeline
# ============================================================================

@router.post("/graph/timeline")
async def graph_timeline(body: dict[str, Any]) -> dict[str, Any]:
    """Get timeline for a node."""
    node_id = body.get("node_id", "")
    return {
        "node_id": node_id,
        "events": [],
        "total": 0,
    }


# Include cleanup sub-router routes
for route in _cleanup_router.routes:
    router.routes.append(route)
