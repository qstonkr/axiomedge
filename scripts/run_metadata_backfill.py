"""Batch 1: Backfill owner, L1 category, quality_score to existing chunks.

No LLM needed — runs locally using keyword matching and rule-based logic.
Updates Qdrant chunk metadata + creates Neo4j graph edges (OWNS, CATEGORIZED_AS).

Usage:
    uv run python scripts/run_metadata_backfill.py                         # All KBs (Qdrant only)
    uv run python scripts/run_metadata_backfill.py a-ari drp g-espa        # Specific KBs
    uv run python scripts/run_metadata_backfill.py --with-neo4j            # + Neo4j OWNS/CATEGORIZED_AS edges
    uv run python scripts/run_metadata_backfill.py --force a-ari           # Overwrite existing values
"""
import sys
import logging
import time
from collections import Counter

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]


def fetch_all_points(collection: str) -> list[dict]:
    """Fetch all points with payload from Qdrant."""
    points = []
    offset = None
    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
        data = resp.json()["result"]
        batch = data["points"]
        if not batch:
            break
        points.extend(batch)
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return points


def update_point_payload(collection: str, point_id: str, payload: dict):
    """Update specific payload fields on a Qdrant point."""
    requests.post(
        f"{QDRANT_URL}/collections/{collection}/points/payload",
        json={"points": [point_id], "payload": payload},
    )


def _compute_doc_metadata(payload: dict) -> dict:
    """Compute owner, L1 category, and quality score for a document."""
    from src.pipeline.ingestion import (
        extract_owner, classify_l1_category, calculate_quality_score,
        _calculate_metrics, _determine_quality_tier,
    )
    from src.core.models import RawDocument

    doc_id = payload.get("doc_id", "")
    doc_name = payload.get("document_name", "")
    author = payload.get("author", "")
    content = payload.get("content", "")

    raw = RawDocument(
        doc_id=doc_id,
        title=doc_name,
        content=content,
        source_uri=payload.get("source_uri", ""),
        author=author,
        metadata={"creator": payload.get("creator", ""), "last_modifier": payload.get("last_modifier", "")},
    )
    owner = extract_owner(raw)
    l1_category = classify_l1_category(doc_name, content)

    metrics = _calculate_metrics(content)
    tier = _determine_quality_tier(metrics)
    quality_score = calculate_quality_score(metrics, tier)

    return {"owner": owner, "l1_category": l1_category, "quality_score": quality_score}


def _build_point_update(
    payload: dict, cached: dict, force: bool, stats: dict,
    category_counts: Counter, owner_counts: Counter,
) -> dict:
    """Build the payload update dict for a single point."""
    new_payload = {}

    if force or (not payload.get("owner") and cached["owner"]):
        new_payload["owner"] = cached["owner"]
        if cached["owner"]:
            stats["owner_set"] += 1
            owner_counts[cached["owner"]] += 1

    if force or not payload.get("l1_category"):
        new_payload["l1_category"] = cached["l1_category"]
        stats["category_set"] += 1
        category_counts[cached["l1_category"]] += 1

    if force or not payload.get("quality_score"):
        new_payload["quality_score"] = cached["quality_score"]
        stats["score_set"] += 1

    return new_payload


def _process_backfill_point(
    point: dict,
    force: bool,
    doc_cache: dict[str, dict],
    stats: dict,
    category_counts: Counter,
    owner_counts: Counter,
    updates: list[tuple[str, dict]],
) -> None:
    """Process a single point for metadata backfill."""
    payload = point["payload"]
    point_id = point["id"]

    if not force and payload.get("owner") and payload.get("l1_category") and payload.get("quality_score"):
        stats["already_has"] += 1
        return

    doc_id = payload.get("doc_id", "")
    if doc_id not in doc_cache:
        doc_cache[doc_id] = _compute_doc_metadata(payload)

    new_payload = _build_point_update(payload, doc_cache[doc_id], force, stats, category_counts, owner_counts)
    if new_payload:
        updates.append((point_id, new_payload))


def run_backfill(kb_id: str, *, force: bool = False, skip_neo4j: bool = False):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Fetching points from {collection}...")
    points = fetch_all_points(collection)
    logger.info(f"[{kb_id}] Fetched {len(points)} points")

    if not points:
        return

    stats = {"total": len(points), "owner_set": 0, "category_set": 0, "score_set": 0, "already_has": 0}
    category_counts: Counter[str] = Counter()
    owner_counts: Counter[str] = Counter()
    doc_cache: dict[str, dict] = {}
    updates: list[tuple[str, dict]] = []

    for i, point in enumerate(points):
        _process_backfill_point(
            point, force, doc_cache, stats, category_counts, owner_counts, updates,
        )
        if (i + 1) % 500 == 0:
            logger.info(f"[{kb_id}] Scanned {i+1}/{len(points)}...")

    if updates:
        logger.info(f"[{kb_id}] Applying {len(updates)} metadata updates to Qdrant...")
        t0 = time.time()
        for point_id, payload_update in updates:
            update_point_payload(collection, point_id, payload_update)
        elapsed = time.time() - t0
        logger.info(f"[{kb_id}] Qdrant update done in {elapsed:.1f}s")

    if not skip_neo4j:
        _create_neo4j_edges(kb_id, doc_cache)
    else:
        logger.info(f"[{kb_id}] Skipping Neo4j edge creation (--skip-neo4j)")

    logger.info(f"[{kb_id}] DONE: {stats}")
    if category_counts:
        logger.info(f"[{kb_id}] Categories: {dict(category_counts.most_common(10))}")
    if owner_counts:
        logger.info(f"[{kb_id}] Owners: {dict(owner_counts.most_common(10))}")


def _create_neo4j_edges(kb_id: str, doc_cache: dict[str, dict]):
    """Create OWNS and CATEGORIZED_AS edges in Neo4j."""
    import os
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "password")

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

        owns_count = 0
        cat_count = 0

        with driver.session() as session:
            for doc_id, data in doc_cache.items():
                owner = data.get("owner", "")
                category = data.get("l1_category", "")

                if owner:
                    session.run(
                        "MERGE (p:Person {name: $owner}) "
                        "MERGE (d:Document {id: $doc_id}) "
                        "MERGE (p)-[:OWNS]->(d)",
                        owner=owner, doc_id=doc_id,
                    )
                    owns_count += 1

                if category and category != "기타":
                    session.run(
                        "MERGE (c:Category {name: $category}) "
                        "MERGE (d:Document {id: $doc_id}) "
                        "MERGE (d)-[:CATEGORIZED_AS]->(c)",
                        category=category, doc_id=doc_id,
                    )
                    cat_count += 1

        driver.close()
        logger.info(f"[{kb_id}] Neo4j edges: {owns_count} OWNS, {cat_count} CATEGORIZED_AS")
    except Exception as e:
        logger.warning(f"[{kb_id}] Neo4j edge creation failed (non-critical): {e}")


if __name__ == "__main__":
    flags = {"--force", "--with-neo4j"}
    force = "--force" in sys.argv
    with_neo4j = "--with-neo4j" in sys.argv
    targets = [a for a in sys.argv[1:] if a not in flags] or ALL_KBS

    for kb_id in targets:
        logger.info(f"\n{'='*60}")
        logger.info(f"[START] {kb_id}")
        logger.info(f"{'='*60}")
        run_backfill(kb_id, force=force, skip_neo4j=not with_neo4j)

    logger.info(f"\n{'='*60}")
    logger.info("ALL DONE")
