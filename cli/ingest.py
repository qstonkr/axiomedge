"""CLI: Ingest documents into knowledge base.

Usage:
    python -m cli.ingest --source ./docs/ --kb-id my-kb
    python -m cli.ingest --file report.pdf --kb-id my-kb
    python -m cli.ingest --crawl-dir ./crawl_results/ --kb-id confluence-kb
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
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
    from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider
    from src.vectordb.client import QdrantConfig, QdrantClientProvider
    from src.vectordb.collections import QdrantCollectionManager
    from src.vectordb.store import QdrantStoreOperations

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
            from src.graph.client import Neo4jClient
            from src.graph.repository import Neo4jGraphRepository

            neo4j = Neo4jClient(uri=settings.neo4j.uri)
            await neo4j.connect()
            graph_repo = Neo4jGraphRepository(neo4j)
        except Exception as e:
            logger.warning("Neo4j not available: %s", e)

    sparse_embedder = OnnxSparseEmbedder(embedder)
    return embedder, sparse_embedder, store, cm, graph_repo, provider


async def ingest_directory(source_dir: str, kb_id: str, force: bool = False):
    embedder, sparse_embedder, store, cm, graph_repo, provider = await _init_services()

    from src.domain.models import RawDocument
    from src.pipeline.document_parser import parse_file
    from src.pipeline.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(embedder=embedder, sparse_embedder=sparse_embedder, vector_store=store, graph_store=graph_repo)

    total_docs = 0
    total_chunks = 0
    for root, _dirs, files in os.walk(source_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            text = parse_file(fpath)
            if not text:
                continue
            raw = RawDocument(
                doc_id=RawDocument.sha256(fpath),
                title=fname,
                content=text,
                source_uri=fpath,
            )
            result = await pipeline.ingest(raw, collection_name=kb_id)
            total_docs += 1
            total_chunks += result.chunks_stored

    logger.info("Ingestion complete: %d docs, %d chunks", total_docs, total_chunks)
    await provider.close()


async def ingest_file(file_path: str, kb_id: str):
    embedder, sparse_embedder, store, cm, graph_repo, provider = await _init_services()

    from src.domain.models import RawDocument
    from src.pipeline.document_parser import parse_file
    from src.pipeline.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(embedder=embedder, sparse_embedder=sparse_embedder, vector_store=store, graph_store=graph_repo)

    text = parse_file(file_path)
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


async def ingest_crawl(crawl_dir: str, kb_id: str):
    embedder, sparse_embedder, store, cm, graph_repo, provider = await _init_services()

    from src.connectors.crawl_result import CrawlResultConnector
    from src.pipeline.ingestion import IngestionPipeline

    connector = CrawlResultConnector(crawl_dir=crawl_dir)
    result = await connector.fetch()

    if not result.documents:
        logger.info("No documents found in %s", crawl_dir)
        await provider.close()
        return

    pipeline = IngestionPipeline(embedder=embedder, sparse_embedder=sparse_embedder, vector_store=store, graph_store=graph_repo)

    total_chunks = 0
    for doc in result.documents:
        r = await pipeline.ingest(doc, collection_name=kb_id)
        total_chunks += r.chunks_stored

    logger.info("Crawl ingestion complete: %d docs, %d chunks", len(result.documents), total_chunks)
    await provider.close()


def main():
    parser = argparse.ArgumentParser(description="Knowledge Ingestion CLI")
    parser.add_argument("--source", help="Source directory to ingest")
    parser.add_argument("--file", help="Single file to ingest")
    parser.add_argument("--crawl-dir", help="Crawl results directory (JSON/JSONL)")
    parser.add_argument("--kb-id", default="knowledge", help="Knowledge base ID")
    parser.add_argument("--force", action="store_true", help="Force rebuild")

    args = parser.parse_args()

    if args.source:
        asyncio.run(ingest_directory(args.source, args.kb_id, args.force))
    elif args.file:
        asyncio.run(ingest_file(args.file, args.kb_id))
    elif args.crawl_dir:
        asyncio.run(ingest_crawl(args.crawl_dir, args.kb_id))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
