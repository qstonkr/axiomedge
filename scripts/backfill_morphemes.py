"""Backfill KiwiPy morphemes to existing Qdrant chunks.

Adds 'morphemes' payload field containing extracted noun stems
for precise keyword matching in search.

Usage:
    uv run python scripts/backfill_morphemes.py              # All KBs
    uv run python scripts/backfill_morphemes.py a-ari drp    # Specific KBs
"""
import sys
import logging
import time

import requests
from kiwipiepy import Kiwi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
ALL_KBS = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]
NOUN_TAGS = {"NNG", "NNP", "SL"}


def extract_morphemes(kiwi: Kiwi, text: str) -> str:
    """Extract noun morphemes from text."""
    try:
        tokens = kiwi.tokenize(text[:2000])
        return " ".join(t.form for t in tokens if t.tag in NOUN_TAGS and len(t.form) >= 2)
    except Exception:
        return ""


def _update_point_morphemes(collection: str, point: dict, kiwi: Kiwi) -> bool:
    """Extract and update morphemes for a single point. Returns True if updated."""
    if point["payload"].get("morphemes"):
        return False

    content = point["payload"].get("content", "")
    morphs = extract_morphemes(kiwi, content)
    if not morphs:
        return False

    requests.post(
        f"{QDRANT_URL}/collections/{collection}/points/payload",
        json={"points": [point["id"]], "payload": {"morphemes": morphs}},
    )
    return True


def _scroll_page(collection: str, offset):
    """Fetch one scroll page from Qdrant. Returns (points, next_offset) or None on error."""
    body = {"limit": 50, "with_payload": ["content", "morphemes"], "with_vector": False}
    if offset:
        body["offset"] = offset
    resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
    if resp.status_code != 200:
        return None
    data = resp.json()["result"]
    return data["points"], data.get("next_page_offset")


def _tally_points(points, collection: str, kiwi: Kiwi) -> tuple[int, int]:
    """Process points and return (updated, skipped) counts."""
    updated = 0
    skipped = 0
    for p in points:
        if _update_point_morphemes(collection, p, kiwi):
            updated += 1
        elif p["payload"].get("morphemes"):
            skipped += 1
    return updated, skipped


def run_backfill(kb_id: str, kiwi: Kiwi):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Processing {collection}...")

    offset = None
    updated = 0
    skipped = 0
    total = 0
    t0 = time.time()

    while True:
        result = _scroll_page(collection, offset)
        if result is None:
            logger.error(f"[{kb_id}] Scroll failed")
            break

        points, offset = result
        if not points:
            break

        page_updated, page_skipped = _tally_points(points, collection, kiwi)
        total += len(points)
        updated += page_updated
        skipped += page_skipped

        if not offset:
            break

        if total % 5000 == 0:
            elapsed = time.time() - t0
            logger.info(f"[{kb_id}] {total} scanned, {updated} updated, {elapsed:.0f}s")

    elapsed = time.time() - t0
    logger.info(f"[{kb_id}] DONE: {total} scanned, {updated} updated, {skipped} skipped, {elapsed:.0f}s")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ALL_KBS
    kiwi = Kiwi()
    logger.info(f"KiwiPy loaded, processing {len(targets)} KBs")

    for kb_id in targets:
        run_backfill(kb_id, kiwi)

    logger.info("ALL DONE")
