"""Parallel GraphRAG batch runner — concurrent SageMaker + Neo4j processing.

Uses ThreadPoolExecutor to parallelize LLM extraction calls.
Neo4j saves are serialized via a lock to avoid write conflicts.

Usage:
    GRAPHRAG_USE_SAGEMAKER=true AWS_PROFILE=jeongbeomkim \
        uv run python scripts/run_graphrag_parallel.py drp g-espa partnertalk hax itops_general

    # Custom worker count (default: 4)
    GRAPHRAG_WORKERS=6 uv run python scripts/run_graphrag_parallel.py itops_general
"""
import os
import sys
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger(__name__)

WORKERS = int(os.getenv("GRAPHRAG_WORKERS", "4"))


def fetch_all_chunks(collection: str) -> list[dict]:
    """Scroll through all chunks in Qdrant collection."""
    chunks = []
    offset = None
    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        resp = requests.post(
            f"http://localhost:6333/collections/{collection}/points/scroll",
            json=body,
        )
        data = resp.json()["result"]
        points = data["points"]
        if not points:
            break
        for p in points:
            pay = p["payload"]
            chunks.append({
                "content": pay.get("content", ""),
                "title": pay.get("document_name", pay.get("source_title", "")),
                "page_id": str(p["id"]),
            })
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return chunks


def process_chunk(
    extractor,
    chunk: dict,
    kb_id: str,
    neo4j_lock: threading.Lock,
    stats: dict,
    stats_lock: threading.Lock,
):
    """Process a single chunk: extract (parallel) + save (serialized)."""
    try:
        result = extractor.extract(
            document=chunk["content"],
            source_title=chunk["title"],
            source_page_id=chunk["page_id"],
            kb_id=kb_id,
        )

        if result.node_count > 0 or result.relationship_count > 0:
            # Serialize Neo4j writes to avoid conflicts
            with neo4j_lock:
                extractor.save_to_neo4j(result)

            with stats_lock:
                stats["nodes"] += result.node_count
                stats["rels"] += result.relationship_count

        with stats_lock:
            stats["success"] += 1

        return True
    except Exception as e:
        with stats_lock:
            stats["failed"] += 1
        logger.error(f"Chunk failed: {e}")
        return False


def run_graphrag_parallel(kb_id: str):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Fetching chunks from {collection}...")
    chunks = fetch_all_chunks(collection)
    logger.info(f"[{kb_id}] Fetched {len(chunks)} chunks — {WORKERS} workers")

    if not chunks:
        logger.warning(f"[{kb_id}] No chunks found")
        return

    from src.pipeline.graphrag_extractor import GraphRAGExtractor

    extractor = GraphRAGExtractor()
    neo4j_lock = threading.Lock()
    stats_lock = threading.Lock()
    stats = {"total": len(chunks), "success": 0, "failed": 0, "nodes": 0, "rels": 0}
    failed_chunks: list[dict] = []  # Track failed chunks for retry
    failed_lock = threading.Lock()

    def _process_and_track(chunk, chunk_idx):
        ok = process_chunk(extractor, chunk, kb_id, neo4j_lock, stats, stats_lock)
        if not ok:
            with failed_lock:
                failed_chunks.append(chunk)

    start_time = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="graphrag") as pool:
        futures = {
            pool.submit(_process_and_track, chunk, i): i
            for i, chunk in enumerate(chunks)
        }

        for future in as_completed(futures):
            completed += 1
            future.result()

            if completed % 50 == 0:
                elapsed = time.time() - start_time
                rate = completed / elapsed
                remaining = (len(chunks) - completed) / rate if rate > 0 else 0
                with stats_lock:
                    logger.info(
                        f"[{kb_id}] Progress: {completed}/{len(chunks)} "
                        f"— {stats['nodes']} nodes, {stats['rels']} rels "
                        f"— {rate:.1f} chunks/s, ~{remaining/60:.0f}min left"
                    )

    # Retry failed chunks (once)
    if failed_chunks:
        logger.info(f"[{kb_id}] Retrying {len(failed_chunks)} failed chunks...")
        retry_success = 0
        for chunk in failed_chunks:
            ok = process_chunk(extractor, chunk, kb_id, neo4j_lock, stats, stats_lock)
            if ok:
                retry_success += 1
                with stats_lock:
                    stats["failed"] -= 1
        logger.info(f"[{kb_id}] Retry: {retry_success}/{len(failed_chunks)} recovered")

    # Save remaining failed chunk IDs to file for manual retry
    still_failed = [c["page_id"] for c in failed_chunks if c.get("page_id")]
    if still_failed:
        failed_file = f"/tmp/graphrag_failed_{kb_id}.json"
        import json as _json
        with open(failed_file, "w") as f:
            _json.dump(still_failed, f)
        logger.warning(f"[{kb_id}] {len(still_failed)} chunks still failed → {failed_file}")

    elapsed = time.time() - start_time
    logger.info(
        f"[{kb_id}] DONE in {elapsed/60:.1f}min: {stats} "
        f"— avg {elapsed/len(chunks):.1f}s/chunk"
    )
    extractor.close()


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["drp", "g-espa"]

    for kb_id in targets:
        logger.info(f"\n{'='*60}")
        logger.info(f"[START] {kb_id} — workers={WORKERS}")
        logger.info(f"{'='*60}")
        run_graphrag_parallel(kb_id)

    logger.info(f"\n{'='*60}")
    logger.info("ALL DONE")
