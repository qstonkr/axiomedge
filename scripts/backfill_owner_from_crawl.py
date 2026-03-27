"""Backfill owner from crawl JSON files to Qdrant chunks.

Maps page_id → creator/last_modifier from crawl_hax.json and crawl_itops.json,
then updates Qdrant chunk metadata where doc_id matches page_id.

Usage:
    uv run python scripts/backfill_owner_from_crawl.py
"""
import json
import logging
import re

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

QDRANT_URL = "http://localhost:6333"
CRAWL_DIR = "1st_jsonl"

# Owner normalization (from quality_processor.py)
SKIP_PATTERNS = [
    r"^Unknown", r"^User$", r"^admin$", r"AI센터", r"MC\s*Front",
    r"^hwlee$", r"APP$", r"팀$", r"본부$", r"부문$", r"센터$", r"^T$",
]


def normalize_owner(raw: str) -> str:
    """Extract clean Korean name from raw creator/modifier string."""
    if not raw:
        return ""
    name = raw.strip()

    # Skip known patterns
    for pat in SKIP_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return ""

    # Strip team suffix: "조현일/AX전략팀" → "조현일"
    if "/" in name:
        name = name.split("/")[0].strip()

    # Strip M suffix
    if name.endswith("M") and len(name) > 1:
        name = name[:-1]

    # Valid Korean name (2-4 chars)
    if re.match(r"^[가-힣]{2,4}$", name):
        return name

    return ""


def load_page_owners(crawl_file: str) -> dict[str, str]:
    """Load page_id → owner mapping from crawl JSON."""
    with open(crawl_file) as f:
        data = json.load(f)

    mapping = {}
    for page in data["pages"]:
        page_id = str(page.get("page_id", ""))
        if not page_id:
            continue

        # Try creator_name first, then creator, then last_modifier
        owner = ""
        for field in ("creator_name", "creator", "last_modifier"):
            candidate = normalize_owner(page.get(field, "") or "")
            if candidate:
                owner = candidate
                break

        if owner:
            mapping[page_id] = owner

    return mapping


def update_qdrant_owners(collection: str, page_owners: dict[str, str]):
    """Update owner field in Qdrant chunks matching page_id → doc_id."""
    offset = None
    updated = 0
    skipped = 0
    total = 0

    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
        data = resp.json()["result"]
        points = data["points"]
        if not points:
            break

        for point in points:
            total += 1
            doc_id = point["payload"].get("doc_id", "")
            current_owner = point["payload"].get("owner", "")

            if current_owner:
                skipped += 1
                continue

            owner = page_owners.get(doc_id, "")
            if owner:
                requests.post(
                    f"{QDRANT_URL}/collections/{collection}/points/payload",
                    json={"points": [point["id"]], "payload": {"owner": owner}},
                )
                updated += 1

        offset = data.get("next_page_offset")
        if offset is None:
            break

    return {"total": total, "updated": updated, "skipped": skipped}


if __name__ == "__main__":
    configs = [
        ("kb_hax", f"{CRAWL_DIR}/crawl_hax.json"),
        ("kb_itops_general", f"{CRAWL_DIR}/crawl_itops.json"),
    ]

    for collection, crawl_file in configs:
        logger.info(f"[{collection}] Loading owners from {crawl_file}...")
        page_owners = load_page_owners(crawl_file)
        logger.info(f"[{collection}] Found {len(page_owners)} pages with owners")

        logger.info(f"[{collection}] Updating Qdrant...")
        stats = update_qdrant_owners(collection, page_owners)
        logger.info(f"[{collection}] DONE: {stats}")

    logger.info("ALL DONE")
