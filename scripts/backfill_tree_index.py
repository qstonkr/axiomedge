"""Backfill tree index: 기존 Qdrant 청크의 heading_path → Neo4j 트리 일괄 생성.

재인제스트 없이 기존 문서에 트리 인덱스를 추가한다.
Qdrant에서 heading_path 메타데이터를 읽어 Neo4j에 TreeRoot/TreeSection/TreePage를 생성.

Usage:
    uv run python scripts/backfill_tree_index.py                    # 모든 KB
    uv run python scripts/backfill_tree_index.py g-espa itops       # 특정 KB
    uv run python scripts/backfill_tree_index.py --dry-run          # 미리보기
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

ALL_KBS = [
    "kb_a_ari", "kb_drp", "kb_g_espa", "kb_partnertalk", "kb_hax", "kb_itops_general",
]


def _list_collections() -> list[str]:
    """Qdrant에서 컬렉션 목록 조회."""
    try:
        resp = requests.get(f"{QDRANT_URL}/collections", timeout=5)
        resp.raise_for_status()
        return [c["name"] for c in resp.json()["result"]["collections"]]
    except Exception as e:
        logger.error("Failed to list Qdrant collections: %s", e)
        return []


def _scroll_all_chunks(collection: str) -> dict[str, list[dict]]:
    """컬렉션의 모든 청크를 document_id별로 그룹화하여 반환.

    Returns:
        {document_id: [{"chunk_id", "heading_path", "chunk_index"}]}
    """
    docs: dict[str, list[dict]] = {}
    offset = None
    total = 0

    while True:
        body = {
            "limit": 100,
            "with_payload": [
                "document_id", "doc_id", "heading_path", "ancestor_path",
                "chunk_index", "chunk_type",
            ],
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset

        try:
            resp = requests.post(
                f"{QDRANT_URL}/collections/{collection}/points/scroll",
                json=body, timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("Scroll failed for %s: %s", collection, resp.status_code)
                break
            data = resp.json()["result"]
        except Exception as e:
            logger.warning("Scroll error for %s: %s", collection, e)
            break

        points = data["points"]
        if not points:
            break

        for pt in points:
            payload = pt.get("payload", {})
            if payload.get("chunk_type") == "title":
                continue
            doc_id = payload.get("document_id") or payload.get("doc_id", "")
            if not doc_id:
                continue
            # heading_path 또는 ancestor_path (Confluence 위키)
            heading = payload.get("heading_path") or payload.get("ancestor_path") or ""
            docs.setdefault(doc_id, []).append({
                "chunk_id": str(pt["id"]),
                "heading_path": heading,
                "chunk_index": payload.get("chunk_index", 0),
            })
            total += 1

        offset = data.get("next_page_offset")
        if not offset:
            break

    logger.info("Scrolled %s: %d chunks, %d documents", collection, total, len(docs))
    return docs


async def _build_and_persist(
    kb_id: str,
    doc_chunks: dict[str, list[dict]],
    dry_run: bool = False,
) -> dict[str, int]:
    """문서별 트리를 구축하고 Neo4j에 저장."""
    from src.pipeline.tree_index_builder import build_tree_from_chunks, persist_tree_to_neo4j

    stats = {"documents": 0, "sections": 0, "pages": 0, "errors": 0}

    if dry_run:
        for doc_id, chunks in doc_chunks.items():
            tree = build_tree_from_chunks(kb_id, doc_id, chunks)
            stats["documents"] += 1
            stats["sections"] += len(tree["sections"])
            stats["pages"] += len(tree["pages"])
        return stats

    from src.graph.client import Neo4jClient
    from src.graph.repository import Neo4jGraphRepository

    client = Neo4jClient(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    repo = Neo4jGraphRepository(client)

    try:
        for doc_id, chunks in doc_chunks.items():
            try:
                tree = build_tree_from_chunks(kb_id, doc_id, chunks)
                await persist_tree_to_neo4j(repo, tree)
                stats["documents"] += 1
                stats["sections"] += len(tree["sections"])
                stats["pages"] += len(tree["pages"])
            except Exception as e:
                logger.warning("Tree build failed for %s/%s: %s", kb_id, doc_id, e)
                stats["errors"] += 1
    finally:
        await client.close()

    return stats


async def _clean_tree_nodes() -> int:
    """기존 트리 노드 전체 삭제 (TreeRoot, TreeSection, TreePage + 관련 엣지)."""
    from src.graph.client import Neo4jClient

    client = Neo4jClient(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    total_deleted = 0
    try:
        for label in ("TreePage", "TreeSection", "TreeRoot"):
            result = await client.execute_query(
                f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) as cnt"
            )
            cnt = result[0]["cnt"] if result else 0
            total_deleted += cnt
            logger.info("Cleaned %d %s nodes", cnt, label)
    finally:
        await client.close()
    return total_deleted


async def async_main(kb_ids: list[str], dry_run: bool = False, clean: bool = False):
    """메인 백필 로직."""
    if clean and not dry_run:
        logger.info("--- Cleaning existing tree nodes ---")
        deleted = await _clean_tree_nodes()
        logger.info("Cleaned %d tree nodes total", deleted)

    available = _list_collections()
    if not available:
        logger.error("No Qdrant collections found")
        return

    targets = [kb for kb in kb_ids if kb in available] if kb_ids else [
        kb for kb in ALL_KBS if kb in available
    ]

    if not targets:
        logger.error("No matching collections. Available: %s", available)
        return

    logger.info("=== Tree Index Backfill %s===", "(DRY RUN) " if dry_run else "")
    logger.info("Target KBs: %s", targets)

    total_stats = {"documents": 0, "sections": 0, "pages": 0, "errors": 0}
    start = time.time()

    for kb_id in targets:
        logger.info("--- Processing KB: %s ---", kb_id)
        doc_chunks = _scroll_all_chunks(kb_id)
        if not doc_chunks:
            logger.info("No documents found in %s, skipping", kb_id)
            continue

        stats = await _build_and_persist(kb_id, doc_chunks, dry_run=dry_run)
        for k in total_stats:
            total_stats[k] += stats[k]

        logger.info(
            "KB %s: %d docs, %d sections, %d pages, %d errors",
            kb_id, stats["documents"], stats["sections"], stats["pages"], stats["errors"],
        )

    elapsed = time.time() - start
    logger.info("=== BACKFILL COMPLETE (%.1fs) ===", elapsed)
    logger.info(
        "Total: %d documents, %d sections, %d pages, %d errors",
        total_stats["documents"], total_stats["sections"],
        total_stats["pages"], total_stats["errors"],
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    clean = "--clean" in sys.argv
    asyncio.run(async_main(args, dry_run=dry_run, clean=clean))
