"""Term extraction from Qdrant chunks — KiwiPy based, no LLM needed."""
import sys
import asyncio
import logging

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KB_ORDER = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]


def fetch_all_chunks(collection: str) -> list[str]:
    """Fetch all chunk texts from Qdrant."""
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
            content = p["payload"].get("content", "")
            if content:
                chunks.append(content)
        offset = data.get("next_page_offset")
        if offset is None:
            break
    return chunks


async def run_extraction(kb_id: str):
    collection = f"kb_{kb_id.replace('-', '_')}"
    logger.info(f"[{kb_id}] Fetching chunks from {collection}...")
    chunks = fetch_all_chunks(collection)
    logger.info(f"[{kb_id}] Fetched {len(chunks)} chunks")

    if not chunks:
        logger.warning(f"[{kb_id}] No chunks found")
        return

    # Import glossary repo for dedup + save
    from src.stores.postgres.repositories.glossary import GlossaryRepository
    from src.stores.postgres.init_db import DEFAULT_DATABASE_URL
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.pipelines.term_extractor import TermExtractor

    engine = create_async_engine(DEFAULT_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    glossary_repo = GlossaryRepository(session_factory)
    extractor = TermExtractor(glossary_repo=glossary_repo, min_occurrences=2)

    # Extract terms
    terms = await extractor.extract_from_chunks(chunks, kb_id=kb_id)
    logger.info(f"[{kb_id}] Extracted {len(terms)} terms")

    if terms:
        # Show top terms
        for t in sorted(terms, key=lambda x: -x.occurrences)[:20]:
            logger.info(f"  {t.occurrences:3d}회 [{t.pattern_type:15s}] {t.term}")

        # Save
        saved = await extractor.save_extracted_terms(terms, kb_id=kb_id)
        logger.info(f"[{kb_id}] Saved {saved}/{len(terms)} terms")

    # Synonym discovery
    full_text = "\n".join(chunks[:200])  # First 200 chunks for synonym scan
    glossary_terms = []
    try:
        glossary_terms = await glossary_repo.list_by_kb(
            kb_id=kb_id, status="approved", limit=500, offset=0,
        )
    except Exception:
        pass
    discoveries = await extractor.discover_synonyms(full_text, glossary_terms)
    if discoveries:
        syn_saved = await extractor.save_discovered_synonyms(
            discoveries, kb_id=kb_id,
        )
        logger.info(f"[{kb_id}] Synonyms: {syn_saved}/{len(discoveries)} saved")

    await engine.dispose()
    logger.info(f"[{kb_id}] DONE")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else KB_ORDER
    for kb_id in targets:
        logger.info(f"\n{'='*60}")
        logger.info(f"[START] {kb_id}")
        logger.info(f"{'='*60}")
        asyncio.run(run_extraction(kb_id))

    logger.info(f"\n{'='*60}")
    logger.info("ALL DONE")
