"""CLI: Ingest documents into knowledge base with incremental support.

Supports incremental ingestion: skips already-ingested documents by checking
content_hash against Qdrant metadata. Only new/changed documents are processed.

Usage:
    python -m cli.ingest --source ./docs/ --kb-id my-kb
    python -m cli.ingest --file report.pdf --kb-id my-kb
    python -m cli.ingest --crawl-dir ./crawl_results/ --kb-id confluence-kb
    python -m cli.ingest --source ./docs/ --kb-id my-kb --force  # Force re-ingest all
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class OnnxSparseEmbedder:
    """Adapter that wraps OnnxBgeEmbeddingProvider to satisfy ISparseEmbedder."""

    def __init__(self, onnx_provider: Any) -> None:
        self._provider = onnx_provider

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        output = await asyncio.to_thread(
            self._provider.encode, texts, False, True, False
        )
        return output.get("lexical_weights", [{} for _ in texts])


async def _init_services():
    """Initialize required services."""
    from src.config import get_settings
    from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider
    from src.stores.qdrant.client import QdrantConfig, QdrantClientProvider
    from src.stores.qdrant.collections import QdrantCollectionManager
    from src.stores.qdrant.store import QdrantStoreOperations

    settings = get_settings()

    # Embedding
    model_path = settings.embedding.onnx_model_path or os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "")
    embedder = OnnxBgeEmbeddingProvider(model_path=model_path)
    if not embedder.is_ready():
        logger.error("BGE-M3 ONNX model not ready. Set KNOWLEDGE_BGE_ONNX_MODEL_PATH")
        sys.exit(1)

    # Qdrant
    config = QdrantConfig.from_env()
    provider = QdrantClientProvider(config)
    await provider.ensure_client()
    cm = QdrantCollectionManager(provider)
    store = QdrantStoreOperations(provider, cm)

    # Graph (optional)
    graph_repo = None
    if settings.neo4j.enabled:
        try:
            from src.stores.neo4j.client import Neo4jClient
            from src.stores.neo4j.repository import Neo4jGraphRepository

            neo4j = Neo4jClient(uri=settings.neo4j.uri)
            await neo4j.connect()
            graph_repo = Neo4jGraphRepository(neo4j)
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j not available: %s", e)

    sparse_embedder = OnnxSparseEmbedder(embedder)
    return embedder, sparse_embedder, store, cm, graph_repo, provider


async def _get_ingested_hashes(kb_id: str, _provider) -> set[str]:
    """Get content hashes of already-ingested documents from Qdrant."""
    import httpx

    from src.config import get_settings
    qdrant_url = get_settings().qdrant.url
    collection = f"kb_{kb_id.replace('-', '_')}"
    hashes: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if collection exists
            resp = await client.get(f"{qdrant_url}/collections/{collection}")
            if resp.status_code != 200:
                return hashes

            # Scroll through all points to collect source_uri hashes
            offset = None
            while True:
                body: dict[str, Any] = {
                    "limit": 100,
                    "with_payload": {"include": ["content_hash", "source_uri"]},
                    "with_vector": False,
                }
                if offset:
                    body["offset"] = offset

                resp = await client.post(
                    f"{qdrant_url}/collections/{collection}/points/scroll",
                    json=body,
                )
                if resp.status_code != 200:
                    break

                data = resp.json().get("result", {})
                points = data.get("points", [])
                if not points:
                    break

                hashes.update(
                    h for pt in points
                    if (h := pt.get("payload", {}).get("content_hash", ""))
                )

                offset = data.get("next_page_offset")
                if not offset:
                    break

    except Exception as e:  # noqa: BLE001
        logger.warning("Could not fetch ingested hashes: %s", e)

    return hashes


async def _should_skip_file(
    fpath: str, force: bool, ingested_hashes: set[str],
) -> bool:
    """Check if file was already ingested (by content hash)."""
    if force or not ingested_hashes:
        return False
    _content = await asyncio.to_thread(Path(fpath).read_bytes)
    file_hash = hashlib.sha256(_content).hexdigest()[:32]
    return file_hash in ingested_hashes


async def _ingest_single_file(
    fpath: str, fname: str, kb_id: str, pipeline: Any,
) -> int:
    """Parse and ingest a single file. Returns chunks_stored or 0 on skip."""
    from src.core.models import RawDocument
    from src.pipeline.document_parser import parse_file_enhanced

    result = parse_file_enhanced(fpath)
    text = result.full_text if hasattr(result, 'full_text') else str(result)
    if not text:
        return 0
    raw = RawDocument(
        doc_id=RawDocument.sha256(fpath),
        title=fname,
        content=text,
        source_uri=fpath,
    )
    result = await pipeline.ingest(raw, collection_name=kb_id)
    return result.chunks_stored


async def ingest_directory(source_dir: str, kb_id: str, force: bool = False):
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.pipeline.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    # Get already-ingested hashes for incremental mode
    ingested_hashes: set[str] = set()
    if not force:
        ingested_hashes = await _get_ingested_hashes(kb_id, provider)
        if ingested_hashes:
            logger.info("Incremental mode: %d documents already ingested", len(ingested_hashes))

    total_docs = 0
    total_chunks = 0
    skipped = 0

    for root, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            fpath = os.path.join(root, fname)

            if await _should_skip_file(fpath, force, ingested_hashes):
                skipped += 1
                continue

            chunks_stored = await _ingest_single_file(fpath, fname, kb_id, pipeline)
            if chunks_stored:
                total_docs += 1
                total_chunks += chunks_stored

    mode = "FORCE" if force else "INCREMENTAL"
    logger.info(
        "[%s] Ingestion complete: %d docs ingested, %d chunks, %d skipped",
        mode, total_docs, total_chunks, skipped,
    )
    await provider.close()


async def ingest_file(file_path: str, kb_id: str):
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.core.models import RawDocument
    from src.pipeline.document_parser import parse_file_enhanced
    from src.pipeline.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    result = parse_file_enhanced(file_path)
    text = result.full_text if hasattr(result, 'full_text') else str(result)
    if not text:
        logger.error("Could not parse file: %s", file_path)
        await provider.close()
        return

    fname = os.path.basename(file_path)
    raw = RawDocument(
        doc_id=RawDocument.sha256(file_path),
        title=fname,
        content=text,
        source_uri=file_path,
    )
    result = await pipeline.ingest(raw, collection_name=kb_id)

    logger.info("File ingestion complete: %d chunks stored", result.chunks_stored)
    await provider.close()


async def ingest_crawl(crawl_dir: str, kb_id: str, force: bool = False):
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.connectors.crawl_result import CrawlResultConnector
    from src.pipeline.ingestion import IngestionPipeline

    connector = CrawlResultConnector(default_output_dir=crawl_dir)
    result = await connector.fetch({"entry_point": crawl_dir}, force=force)

    if not result.documents:
        logger.info("No documents found in %s", crawl_dir)
        await provider.close()
        return

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    # Get already-ingested hashes for incremental mode
    ingested_hashes: set[str] = set()
    if not force:
        ingested_hashes = await _get_ingested_hashes(kb_id, provider)

    total_chunks = 0
    skipped = 0
    ingested = 0
    for doc in result.documents:
        # Incremental: skip if content hash already exists
        if not force and ingested_hashes:
            doc_hash = hashlib.sha256(
                doc.content.lower().strip().encode()
            ).hexdigest()[:32]
            if doc_hash in ingested_hashes:
                skipped += 1
                continue

        r = await pipeline.ingest(doc, collection_name=kb_id)
        total_chunks += r.chunks_stored
        ingested += 1

    mode = "FORCE" if force else "INCREMENTAL"
    logger.info(
        "[%s] Crawl ingestion complete: %d/%d docs ingested, %d chunks, %d skipped",
        mode, ingested, len(result.documents), total_chunks, skipped,
    )
    await provider.close()


def main():
    parser = argparse.ArgumentParser(description="Knowledge Ingestion CLI")
    parser.add_argument("--source", help="Source directory to ingest")
    parser.add_argument("--file", help="Single file to ingest")
    parser.add_argument("--crawl-dir", help="Crawl results directory (JSON/JSONL)")
    parser.add_argument("--kb-id", default="knowledge", help="Knowledge base ID")
    parser.add_argument("--force", action="store_true", help="Force re-ingest all (skip incremental check)")

    args = parser.parse_args()

    if args.source:
        asyncio.run(ingest_directory(args.source, args.kb_id, args.force))
    elif args.file:
        asyncio.run(ingest_file(args.file, args.kb_id))
    elif args.crawl_dir:
        asyncio.run(ingest_crawl(args.crawl_dir, args.kb_id, args.force))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
