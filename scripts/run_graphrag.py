"""GraphRAG batch runner — process chunks from Qdrant for a single KB."""
import sys
import logging

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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


def run_graphrag(kb_id: str):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"Fetching chunks from {collection}...")
    chunks = fetch_all_chunks(collection)
    logger.info(f"Fetched {len(chunks)} chunks from {collection}")

    if not chunks:
        logger.warning(f"No chunks found for {kb_id}")
        return

    from src.pipeline.graphrag_extractor import GraphRAGExtractor
    extractor = GraphRAGExtractor()

    stats = {"total": len(chunks), "success": 0, "failed": 0, "nodes": 0, "rels": 0}

    for i, chunk in enumerate(chunks):
        try:
            result = extractor.extract(
                document=chunk["content"],
                source_title=chunk["title"],
                source_page_id=chunk["page_id"],
                kb_id=kb_id,
            )
            if result.node_count > 0 or result.relationship_count > 0:
                extractor.save_to_neo4j(result)
                stats["nodes"] += result.node_count
                stats["rels"] += result.relationship_count
            stats["success"] += 1

            if (i + 1) % 50 == 0:
                logger.info(
                    f"[{kb_id}] Progress: {i+1}/{len(chunks)} "
                    f"— {stats['nodes']} nodes, {stats['rels']} rels"
                )
        except Exception as e:
            stats["failed"] += 1
            logger.error(f"[{kb_id}] Chunk {i+1} failed: {e}")

    logger.info(f"[{kb_id}] DONE: {stats}")
    extractor.close()


if __name__ == "__main__":
    kb_id = sys.argv[1] if len(sys.argv) > 1 else "a-ari"
    run_graphrag(kb_id)
