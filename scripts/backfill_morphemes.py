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


def run_backfill(kb_id: str, kiwi: Kiwi):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Processing {collection}...")

    offset = None
    updated = 0
    skipped = 0
    total = 0
    t0 = time.time()

    while True:
        body = {"limit": 50, "with_payload": ["content", "morphemes"], "with_vector": False}
        if offset:
            body["offset"] = offset
        resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
        if resp.status_code != 200:
            logger.error(f"[{kb_id}] Scroll failed: {resp.status_code}")
            break

        data = resp.json()["result"]
        points = data["points"]
        if not points:
            break

        for p in points:
            total += 1
            # Skip if already has morphemes
            if p["payload"].get("morphemes"):
                skipped += 1
                continue

            content = p["payload"].get("content", "")
            morphs = extract_morphemes(kiwi, content)
            if morphs:
                requests.post(
                    f"{QDRANT_URL}/collections/{collection}/points/payload",
                    json={"points": [p["id"]], "payload": {"morphemes": morphs}},
                )
                updated += 1

        offset = data.get("next_page_offset")
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
