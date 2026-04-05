"""Batch clean OCR artifacts in existing Qdrant chunks.

Applies deterministic OCR cleaning (spacing, numbers, dedup, domain dict)
to all chunks in specified KBs without re-embedding.

Usage:
    uv run python scripts/batch_clean_chunks.py              # all KBs
    uv run python scripts/batch_clean_chunks.py g-espa       # single KB
    uv run python scripts/batch_clean_chunks.py --dry-run    # preview only
"""

import sys
import logging
import httpx

from src.pipeline.ocr_corrector import clean_chunk_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]
BATCH_SIZE = 50


def get_collection_name(kb_id: str) -> str:
    return f"kb_{kb_id.replace('-', '_')}"


def scroll_all_points(client: httpx.Client, collection: str):
    """Yield all points from a collection using scroll pagination."""
    offset = None
    while True:
        body = {
            "limit": BATCH_SIZE,
            "with_payload": {"include": ["content", "document_name"]},
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset

        resp = client.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
        if resp.status_code != 200:
            logger.error(f"Scroll failed: {resp.status_code} {resp.text[:200]}")
            break

        data = resp.json().get("result", {})
        points = data.get("points", [])
        if not points:
            break

        yield from points
        offset = data.get("next_page_offset")
        if not offset:
            break


def update_payload(client: httpx.Client, collection: str, point_id: str, new_content: str):
    """Update only the content field of a point's payload."""
    resp = client.post(
        f"{QDRANT_URL}/collections/{collection}/points/payload",
        json={
            "payload": {"content": new_content},
            "points": [point_id],
        },
    )
    if resp.status_code != 200:
        logger.warning(f"Update failed for {point_id}: {resp.status_code}")
        return False
    return True


def process_kb(kb_id: str, dry_run: bool = False):
    """Process all chunks in a KB."""
    collection = get_collection_name(kb_id)
    client = httpx.Client(timeout=30.0)

    # Check collection exists
    resp = client.get(f"{QDRANT_URL}/collections/{collection}")
    if resp.status_code != 200:
        logger.warning(f"Collection {collection} not found, skipping")
        return 0, 0

    total = 0
    cleaned = 0
    errors = 0

    logger.info(f"Processing {kb_id} ({collection})...")

    for point in scroll_all_points(client, collection):
        total += 1
        pid = str(point["id"])
        content = point.get("payload", {}).get("content", "")
        doc_name = point.get("payload", {}).get("document_name", "")

        if not content:
            continue

        # Apply cleaning
        new_content = clean_chunk_text(content)

        if new_content != content:
            cleaned += 1
            reduction = len(content) - len(new_content)

            if dry_run:
                if cleaned <= 5:  # Show first 5 examples
                    logger.info(f"[DRY] {doc_name[:30]} | -{reduction} chars")
                    logger.info(f"  BEFORE: {content[:100]}")
                    logger.info(f"  AFTER:  {new_content[:100]}")
            else:
                if not update_payload(client, collection, pid, new_content):
                    errors += 1

        if total % 500 == 0:
            logger.info(f"  ... {total} chunks processed, {cleaned} cleaned")

    client.close()
    logger.info(
        f"{kb_id}: {total} total, {cleaned} cleaned ({cleaned/max(total,1)*100:.1f}%), "
        f"{errors} errors"
    )
    return total, cleaned


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    kbs = args if args else ALL_KBS

    if dry_run:
        logger.info("=== DRY RUN MODE (no changes will be made) ===")

    grand_total = 0
    grand_cleaned = 0

    for kb in kbs:
        total, cleaned = process_kb(kb, dry_run=dry_run)
        grand_total += total
        grand_cleaned += cleaned

    logger.info(f"\n{'='*50}")
    logger.info(f"TOTAL: {grand_total} chunks, {grand_cleaned} cleaned "
                f"({grand_cleaned/max(grand_total,1)*100:.1f}%)")
    if dry_run:
        logger.info("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
