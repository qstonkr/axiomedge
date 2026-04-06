"""Background data source sync: crawl → CrawlResultConnector → IngestionPipeline.

Triggered by POST /api/v1/admin/data-sources/{source_id}/trigger.
Optionally starts/stops a dedicated PaddleOCR EC2 instance for attachment OCR.
Runs confluence_full_crawler.py as subprocess, then ingests via existing pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class _OnnxSparseEmbedder:
    """Adapter that wraps OnnxBgeEmbeddingProvider to satisfy ISparseEmbedder."""

    def __init__(self, onnx_provider: Any) -> None:
        self._provider = onnx_provider

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        output = await asyncio.to_thread(
            self._provider.encode, texts, False, True, False
        )
        return output.get("lexical_weights", [{} for _ in texts])


# Crawl output base directory
_CRAWL_OUTPUT_DIR = Path(
    os.getenv("CRAWL_OUTPUT_DIR", str(Path.home() / ".knowledge-local" / "crawl"))
)
_CRAWL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Confluence PAT from environment
_CONFLUENCE_PAT = os.getenv(
    "CONFLUENCE_PAT",
    os.getenv("CONF_PAT", ""),
)

# PaddleOCR EC2 instance (on-demand start/stop)
_PADDLEOCR_INSTANCE_ID = os.getenv("PADDLEOCR_INSTANCE_ID", "")
_PADDLEOCR_API_URL = os.getenv("PADDLEOCR_API_URL", "")
_AWS_REGION = os.getenv("SAGEMAKER_REGION", "ap-northeast-2")


# ---------------------------------------------------------------------------
# PaddleOCR EC2 lifecycle helpers
# ---------------------------------------------------------------------------

async def _get_instance_state(instance_id: str) -> str:
    """Get EC2 instance state (running, stopped, etc.)."""
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 describe-instances --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].State.Name' "
        f"--output text --region {_AWS_REGION}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def _get_instance_ip(instance_id: str) -> str | None:
    """Get EC2 instance public IP (may change after stop/start)."""
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 describe-instances --instance-ids {instance_id} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' "
        f"--output text --region {_AWS_REGION}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    ip = stdout.decode().strip()
    return ip if ip and ip != "None" else None


async def _start_ocr_instance() -> str | None:
    """Start PaddleOCR EC2 instance and wait for health check.

    Returns the API base URL (with potentially new IP) or None if not configured.
    """
    if not _PADDLEOCR_INSTANCE_ID:
        return _PADDLEOCR_API_URL or None

    instance_state = await _get_instance_state(_PADDLEOCR_INSTANCE_ID)
    logger.info("PaddleOCR instance %s state: %s", _PADDLEOCR_INSTANCE_ID, instance_state)

    if instance_state == "running":
        # Already running — just resolve current IP
        ip = await _get_instance_ip(_PADDLEOCR_INSTANCE_ID)
        if ip:
            url = f"http://{ip}:8866"
            if await _wait_for_health(url, timeout=60):
                return url
        return _PADDLEOCR_API_URL or None

    if instance_state not in ("stopped", "stopping"):
        logger.warning("PaddleOCR instance in unexpected state: %s", instance_state)
        return _PADDLEOCR_API_URL or None

    # Wait for "stopped" if currently "stopping"
    if instance_state == "stopping":
        logger.info("Waiting for instance to fully stop...")
        for _ in range(30):
            await asyncio.sleep(5)
            if await _get_instance_state(_PADDLEOCR_INSTANCE_ID) == "stopped":
                break

    # Start instance
    logger.info("Starting PaddleOCR instance %s", _PADDLEOCR_INSTANCE_ID)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 start-instances --instance-ids {_PADDLEOCR_INSTANCE_ID} "
        f"--region {_AWS_REGION}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Wait for running state
    for _ in range(60):
        await asyncio.sleep(5)
        state = await _get_instance_state(_PADDLEOCR_INSTANCE_ID)
        if state == "running":
            break
    else:
        logger.error("PaddleOCR instance did not reach running state")
        return None

    # Get new public IP (changes after stop/start without Elastic IP)
    ip = await _get_instance_ip(_PADDLEOCR_INSTANCE_ID)
    if not ip:
        logger.error("PaddleOCR instance has no public IP")
        return None

    url = f"http://{ip}:8866"
    logger.info("PaddleOCR instance started, waiting for health at %s", url)

    if await _wait_for_health(url, timeout=180):
        return url

    logger.error("PaddleOCR health check timed out at %s", url)
    return None


async def _wait_for_health(url: str, timeout: int = 180) -> bool:
    """Poll health endpoint until ready or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    logger.info("PaddleOCR healthy: %s", url)
                    return True
            except Exception:
                pass
            await asyncio.sleep(10)
    return False


async def _stop_ocr_instance() -> None:
    """Stop PaddleOCR EC2 instance to save costs."""
    if not _PADDLEOCR_INSTANCE_ID:
        return

    logger.info("Stopping PaddleOCR instance %s", _PADDLEOCR_INSTANCE_ID)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 stop-instances --instance-ids {_PADDLEOCR_INSTANCE_ID} "
        f"--region {_AWS_REGION}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    logger.info("PaddleOCR instance stop requested")


def _extract_page_id_from_url(url: str) -> str | None:
    """Extract Confluence page ID from URL."""
    # Pattern: /pages/12345/...
    m = re.search(r"/pages/(\d+)", url)
    if m:
        return m.group(1)
    # Pattern: pageId=12345
    m = re.search(r"pageId=(\d+)", url)
    if m:
        return m.group(1)
    return None


async def run_data_source_sync(
    source: dict[str, Any],
    state: Any,
    sync_mode: str = "full",
) -> None:
    """Run crawl + ingest pipeline for a data source in background.

    Steps:
    1. Extract page_id from data source config/metadata
    2. Run confluence_full_crawler.py subprocess → JSONL output
    3. CrawlResultConnector reads JSONL → RawDocument list
    4. IngestionPipeline ingests each document
    5. Update data source status with results
    """
    source_id = source["id"]
    kb_id = source.get("kb_id", "knowledge")
    source_name = source.get("name", "unknown")
    metadata = source.get("metadata") or {}

    ds_repo = state.get("data_source_repo")
    run_repo = state.get("ingestion_run_repo")
    run_id = str(uuid.uuid4())
    ocr_started = False

    try:
        # --- Step 1: Determine page_id ---
        page_id = (
            metadata.get("root_page_id")
            or source.get("crawl_config", {}).get("root_page_id")
        )
        if not page_id and metadata.get("url"):
            page_id = _extract_page_id_from_url(metadata["url"])

        if not page_id:
            raise ValueError("No page_id found in data source config or URL")

        # Resolve PAT
        pat = _CONFLUENCE_PAT
        if not pat:
            raise ValueError(
                "CONFLUENCE_PAT not configured. "
                "Set CONFLUENCE_PAT or CONF_PAT environment variable."
            )

        logger.info(
            "Starting sync for source %s (kb=%s, page_id=%s)",
            source_name, kb_id, page_id,
        )

        # --- Step 0: Start PaddleOCR EC2 if configured ---
        ocr_url = await _start_ocr_instance()
        ocr_started = ocr_url is not None and _PADDLEOCR_INSTANCE_ID
        if ocr_url:
            logger.info("PaddleOCR available at %s", ocr_url)
        else:
            logger.info("PaddleOCR not available — attachments will skip OCR")

        # Create ingestion run record
        if run_repo:
            try:
                await run_repo.create({
                    "id": run_id,
                    "kb_id": kb_id,
                    "source_type": "crawl_result",
                    "source_name": source_name,
                    "status": "running",
                    "started_at": datetime.now(UTC),
                })
            except Exception as e:
                logger.warning("Failed to create ingestion run record: %s", e)

        # --- Step 2: Run crawler (direct call, no subprocess) ---
        crawl_output_dir = _CRAWL_OUTPUT_DIR / kb_id
        crawl_output_dir.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^\w]", "_", source_name)

        # Set OCR URL for attachment parsing if available
        if ocr_url:
            os.environ["PADDLEOCR_API_URL"] = ocr_url

        from src.connectors.confluence import CrawlerConfig, crawl_space, save_results

        crawler_config = CrawlerConfig(
            base_url=os.getenv("CONFLUENCE_BASE_URL", "https://wiki.gsretail.com"),
            pat=pat,
            output_dir=crawl_output_dir,
            attachments_dir=crawl_output_dir / "attachments",
            knowledge_sources={},
        )

        max_pages = None if sync_mode == "full" else 50
        download_attachments = True  # Always download; parser handles OCR availability

        logger.info("Running crawler: page_id=%s, output=%s", page_id, crawl_output_dir)

        crawl_result = await crawl_space(
            config=crawler_config,
            page_id=page_id,
            source_name=source_name,
            source_key=safe_name,
            max_pages=max_pages,
            download_attachments=download_attachments,
            max_concurrent=3,
            kb_id=kb_id,
            use_bfs=True,
            register_signals=False,
        )

        # Save crawl output for CrawlResultConnector
        output_path = crawl_output_dir / f"crawl_{safe_name}.json"
        save_results(
            crawl_result.pages, output_path,
            source_info={"page_id": page_id, "name": source_name, "key": safe_name},
            page_dicts=crawl_result.page_dicts,
        )

        logger.info("Crawler completed for %s", source_name)

        # --- Step 3: Ingest crawl output via CrawlResultConnector ---
        from src.connectors.crawl_result import CrawlResultConnector

        connector = CrawlResultConnector(default_output_dir=str(crawl_output_dir))
        connector_config = {
            "entry_point": str(crawl_output_dir),
            "source": safe_name,
            "name": source_name,
        }
        result = await connector.fetch(connector_config, force=True)

        if not result.success:
            raise RuntimeError(f"CrawlResultConnector failed: {result.error}")

        documents = result.documents
        if not documents:
            logger.warning("No documents found from crawl output")
            if ds_repo:
                await ds_repo.complete_sync(
                    source_id, "active",
                    sync_result={"documents_synced": 0, "chunks_stored": 0},
                )
            return

        logger.info("Connector produced %d documents, starting ingestion", len(documents))

        # --- Step 4: Ingest documents ---
        store = state.get("qdrant_store")
        embedder = state.get("embedder")
        if not store or not embedder:
            raise RuntimeError("Ingestion services not initialized (qdrant_store/embedder)")

        # Ensure collection exists
        collections = state.get("qdrant_collections")
        if collections:
            await collections.ensure_collection(kb_id)

        from src.pipeline.ingestion import IngestionPipeline

        sparse_embedder = _OnnxSparseEmbedder(embedder)
        pipeline = IngestionPipeline(
            embedder=embedder,
            sparse_embedder=sparse_embedder,
            vector_store=store,
            graph_store=state.get("graph_repo"),
            dedup_cache=state.get("dedup_cache"),
            dedup_pipeline=state.get("dedup_pipeline"),
            enable_ingestion_gate=True,
            enable_term_extraction=True,
            enable_graphrag=True,
            term_extractor=state.get("term_extractor"),
            graphrag_extractor=state.get("graphrag_extractor"),
        )

        semaphore = asyncio.Semaphore(4)
        total_chunks = 0
        docs_ingested = 0
        errors: list[str] = []

        async def _ingest_one(doc):
            nonlocal total_chunks, docs_ingested
            async with semaphore:
                try:
                    r = await pipeline.ingest(doc, collection_name=kb_id)
                    if r.chunks_stored > 0:
                        total_chunks += r.chunks_stored
                        docs_ingested += 1
                except Exception as e:
                    errors.append(f"{doc.title}: {e}")

        tasks = [_ingest_one(doc) for doc in documents]
        await asyncio.gather(*tasks)

        logger.info(
            "Ingestion complete: %d docs, %d chunks, %d errors",
            docs_ingested, total_chunks, len(errors),
        )

        # --- Step 5: Update KB counts and data source status ---
        kb_registry = state.get("kb_registry")
        if kb_registry and docs_ingested > 0:
            try:
                # Ensure KB exists in registry (auto-create if missing)
                existing_kb = await kb_registry.get_kb(kb_id)
                if not existing_kb:
                    await kb_registry.create_kb({
                        "id": kb_id,
                        "name": source_name,
                        "description": metadata.get("description", ""),
                        "tier": "global",
                        "data_classification": "internal",
                        "dataset_ids_by_env": {},
                        "storage_backend": "qdrant",
                        "sync_sources": [],
                        "status": "active",
                        "settings": {},
                        "document_count": 0,
                        "chunk_count": 0,
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                    })
                    logger.info("Auto-created KB '%s' in registry", kb_id)
                await kb_registry.update_counts(kb_id, docs_ingested, total_chunks)
            except Exception as e:
                logger.warning("KB registry update failed: %s", e)

        sync_result = {
            "documents_synced": docs_ingested,
            "documents_total": len(documents),
            "chunks_stored": total_chunks,
            "errors": errors[:10],
            "completed_at": datetime.now(UTC).isoformat(),
        }

        if ds_repo:
            await ds_repo.complete_sync(source_id, "active", sync_result=sync_result)

        if run_repo:
            try:
                await run_repo.complete(run_id, {
                    "status": "completed",
                    "documents_ingested": docs_ingested,
                    "documents_fetched": len(documents),
                    "chunks_stored": total_chunks,
                    "errors": errors[:10],
                    "completed_at": datetime.now(UTC),
                })
            except Exception as e:
                logger.warning("Failed to complete ingestion run record: %s", e)

    except Exception as exc:
        logger.error("Data source sync failed for %s: %s", source_id, exc)
        if ds_repo:
            try:
                await ds_repo.complete_sync(
                    source_id, "error",
                    error_message=str(exc)[:500],
                )
            except Exception:
                pass
        if run_repo:
            try:
                await run_repo.complete(run_id, {
                    "status": "failed",
                    "errors": [str(exc)[:500]],
                    "completed_at": datetime.now(UTC),
                })
            except Exception:
                pass
    finally:
        # Stop PaddleOCR EC2 to save costs
        if ocr_started:
            try:
                await _stop_ocr_instance()
            except Exception as e:
                logger.warning("Failed to stop PaddleOCR instance: %s", e)
