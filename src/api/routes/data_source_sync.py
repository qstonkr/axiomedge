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


async def _wait_for_instance_stopped(instance_id: str, retries: int = 30) -> None:
    """Poll until instance reaches 'stopped' state."""
    logger.info("Waiting for instance to fully stop...")
    for _ in range(retries):
        await asyncio.sleep(5)
        if await _get_instance_state(instance_id) == "stopped":
            return


async def _wait_for_instance_running(instance_id: str, retries: int = 60) -> bool:
    """Poll until instance reaches 'running' state. Returns True if reached."""
    for _ in range(retries):
        await asyncio.sleep(5)
        if await _get_instance_state(instance_id) == "running":
            return True
    return False


async def _resolve_running_instance_url(instance_id: str) -> str | None:
    """Get health-checked URL from an already-running instance."""
    ip = await _get_instance_ip(instance_id)
    if ip:
        url = f"http://{ip}:8866"
        if await _wait_for_health(url, max_wait=60):
            return url
    return _PADDLEOCR_API_URL or None


async def _boot_and_resolve_url(instance_id: str) -> str | None:
    """Start the EC2 instance, wait for running, and return health-checked URL."""
    logger.info("Starting PaddleOCR instance %s", instance_id)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 start-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    if not await _wait_for_instance_running(instance_id):
        logger.error("PaddleOCR instance did not reach running state")
        return None

    ip = await _get_instance_ip(instance_id)
    if not ip:
        logger.error("PaddleOCR instance has no public IP")
        return None

    url = f"http://{ip}:8866"
    logger.info("PaddleOCR instance started, waiting for health at %s", url)

    if await _wait_for_health(url, max_wait=180):
        return url

    logger.error("PaddleOCR health check timed out at %s", url)
    return None


async def _start_ocr_instance() -> str | None:
    """Start PaddleOCR EC2 instance and wait for health check.

    Returns the API base URL (with potentially new IP) or None if not configured.
    """
    if not _PADDLEOCR_INSTANCE_ID:
        return _PADDLEOCR_API_URL or None

    instance_state = await _get_instance_state(_PADDLEOCR_INSTANCE_ID)
    logger.info("PaddleOCR instance %s state: %s", _PADDLEOCR_INSTANCE_ID, instance_state)

    if instance_state == "running":
        return await _resolve_running_instance_url(_PADDLEOCR_INSTANCE_ID)

    if instance_state not in ("stopped", "stopping"):
        logger.warning("PaddleOCR instance in unexpected state: %s", instance_state)
        return _PADDLEOCR_API_URL or None

    if instance_state == "stopping":
        await _wait_for_instance_stopped(_PADDLEOCR_INSTANCE_ID)

    return await _boot_and_resolve_url(_PADDLEOCR_INSTANCE_ID)


async def _wait_for_health(url: str, max_wait: int = 180) -> bool:
    """Poll health endpoint until ready or max_wait seconds."""
    deadline = asyncio.get_event_loop().time() + max_wait
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:  # NOSONAR
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


def _resolve_page_id(source: dict[str, Any]) -> str:
    """Extract page_id from data source config or URL. Raises ValueError if missing."""
    metadata = source.get("metadata") or {}
    page_id = (
        metadata.get("root_page_id")
        or source.get("crawl_config", {}).get("root_page_id")
    )
    if not page_id and metadata.get("url"):
        page_id = _extract_page_id_from_url(metadata["url"])
    if not page_id:
        raise ValueError("No page_id found in data source config or URL")
    return page_id


def _resolve_pat() -> str:
    """Resolve Confluence PAT from env. Raises ValueError if missing."""
    pat = _CONFLUENCE_PAT
    if not pat:
        raise ValueError(
            "CONFLUENCE_PAT not configured. "
            "Set CONFLUENCE_PAT or CONF_PAT environment variable."
        )
    return pat


async def _run_crawl_pipeline(
    *,
    page_id: str,
    source_name: str,
    safe_name: str,
    pat: str,
    kb_id: str,
    sync_mode: str,
    ocr_url: str | None,
) -> Path:
    """Run the Confluence crawler and save results. Returns output path."""
    crawl_output_dir = _CRAWL_OUTPUT_DIR / kb_id
    crawl_output_dir.mkdir(parents=True, exist_ok=True)

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
    logger.info("Running crawler: page_id=%s, output=%s", page_id, crawl_output_dir)

    crawl_result = await crawl_space(
        config=crawler_config,
        page_id=page_id,
        source_name=source_name,
        source_key=safe_name,
        max_pages=max_pages,
        download_attachments=True,
        max_concurrent=3,
        kb_id=kb_id,
        use_bfs=True,
        register_signals=False,
    )

    output_path = crawl_output_dir / f"crawl_{safe_name}.json"
    save_results(
        crawl_result.pages, output_path,
        source_info={"page_id": page_id, "name": source_name, "key": safe_name},
        page_dicts=crawl_result.page_dicts,
    )
    logger.info("Crawler completed for %s", source_name)
    return crawl_output_dir


async def _fetch_documents(
    crawl_output_dir: Path, safe_name: str, source_name: str,
) -> list[Any]:
    """Read crawl output via CrawlResultConnector. Returns document list."""
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
    return result.documents or []


async def _run_ingestion(
    state: Any, documents: list[Any], kb_id: str,
) -> tuple[int, int, list[str]]:
    """Ingest documents via IngestionPipeline. Returns (docs_ingested, total_chunks, errors)."""
    store = state.get("qdrant_store")
    embedder = state.get("embedder")
    if not store or not embedder:
        raise RuntimeError("Ingestion services not initialized (qdrant_store/embedder)")

    collections = state.get("qdrant_collections")
    if collections:
        await collections.ensure_collection(kb_id)

    from src.pipeline.ingestion import IngestionPipeline

    legal_graph_extractor = state.get("legal_graph_extractor")
    if legal_graph_extractor is None:
        # Rule-based extractor is cheap to instantiate and reuses the
        # existing Neo4j driver via its parent class. We wire it lazily
        # so non-legal KBs aren't impacted if Neo4j is unreachable.
        try:
            from src.pipeline.legal_graph import LegalGraphExtractor

            legal_graph_extractor = LegalGraphExtractor()
        except Exception as e:  # noqa: BLE001
            logger.warning("LegalGraphExtractor init failed: %s", e)

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
        legal_graph_extractor=legal_graph_extractor,
    )

    # Size-based dual semaphore: small docs run 8-wide to saturate TEI's
    # parallel slots (max_batch_requests=8), but large docs (>80KB) bottleneck
    # TEI CPU when run 8-wide — a single 300KB statute can take 60-150s on
    # BGE-M3 CPU, and 8 of them in flight at once pushes each httpx call past
    # the 180s timeout, cascading into ReadTimeout failures. Giving large
    # docs their own 2-wide lane keeps their embeddings inside the timeout
    # budget while letting the bulk of the corpus (mostly < 80KB) still run
    # at full concurrency. Threshold 80KB keeps the slow lane ~25% of docs.
    LARGE_DOC_BYTES = 80_000
    semaphore_small = asyncio.Semaphore(8)
    semaphore_large = asyncio.Semaphore(2)
    total_chunks = 0
    docs_ingested = 0
    errors: list[str] = []

    async def _ingest_one(doc: Any) -> None:
        nonlocal total_chunks, docs_ingested
        size = (doc.metadata or {}).get("file_size_bytes", 0)
        sem = semaphore_large if size >= LARGE_DOC_BYTES else semaphore_small
        async with sem:
            try:
                r = await pipeline.ingest(doc, collection_name=kb_id)
                if r.chunks_stored > 0:
                    total_chunks += r.chunks_stored
                    docs_ingested += 1
            except Exception as e:
                errors.append(f"{doc.title}: {e}")

    await asyncio.gather(*[_ingest_one(doc) for doc in documents])

    logger.info(
        "Ingestion complete: %d docs, %d chunks, %d errors",
        docs_ingested, total_chunks, len(errors),
    )
    return docs_ingested, total_chunks, errors


async def _ensure_kb_and_update_counts(
    state: Any, kb_id: str, source_name: str, metadata: dict,
    docs_ingested: int, total_chunks: int,
) -> None:
    """Ensure KB exists in registry and update document/chunk counts."""
    kb_registry = state.get("kb_registry")
    if not kb_registry or docs_ingested <= 0:
        return
    try:
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


async def _update_sync_status(
    ds_repo: Any, run_repo: Any, source_id: str, run_id: str,
    docs_ingested: int, documents_total: int, total_chunks: int,
    errors: list[str],
) -> None:
    """Update data source and ingestion run records on success."""
    sync_result = {
        "documents_synced": docs_ingested,
        "documents_total": documents_total,
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
                "documents_fetched": documents_total,
                "chunks_stored": total_chunks,
                "errors": errors[:10],
                "completed_at": datetime.now(UTC),
            })
        except Exception as e:
            logger.warning("Failed to complete ingestion run record: %s", e)


async def _report_sync_failure(
    ds_repo: Any, run_repo: Any, source_id: str, run_id: str, exc: Exception,
) -> None:
    """Update data source and ingestion run records on failure."""
    logger.error("Data source sync failed for %s: %s", source_id, exc)
    if ds_repo:
        try:
            await ds_repo.complete_sync(
                source_id, "error", error_message=str(exc)[:500],
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


async def run_data_source_sync(
    source: dict[str, Any],
    state: Any,
    sync_mode: str = "full",
) -> None:
    """Dispatch a data source sync to the connector matching its source_type."""
    source_type = str(source.get("source_type") or "").strip().lower()
    if source_type == "git":
        await _run_git_source_sync(source, state)
        return
    await _run_confluence_source_sync(source, state, sync_mode=sync_mode)


async def _run_confluence_source_sync(
    source: dict[str, Any],
    state: Any,
    sync_mode: str = "full",
) -> None:
    """Run Confluence crawl + ingest pipeline for a data source in background.

    Steps:
    1. Extract page_id from data source config/metadata
    2. Run confluence crawler → save output
    3. CrawlResultConnector reads output → RawDocument list
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
        page_id = _resolve_page_id(source)
        pat = _resolve_pat()

        logger.info(
            "Starting sync for source %s (kb=%s, page_id=%s)",
            source_name, kb_id, page_id,
        )

        # Start PaddleOCR EC2 if configured
        ocr_url = await _start_ocr_instance()
        ocr_started = ocr_url is not None and bool(_PADDLEOCR_INSTANCE_ID)
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

        safe_name = re.sub(r"[^\w]", "_", source_name)

        # Run crawler
        crawl_output_dir = await _run_crawl_pipeline(
            page_id=page_id, source_name=source_name, safe_name=safe_name,
            pat=pat, kb_id=kb_id, sync_mode=sync_mode, ocr_url=ocr_url,
        )

        # Fetch documents from crawl output
        documents = await _fetch_documents(crawl_output_dir, safe_name, source_name)
        if not documents:
            logger.warning("No documents found from crawl output")
            if ds_repo:
                await ds_repo.complete_sync(
                    source_id, "active",
                    sync_result={"documents_synced": 0, "chunks_stored": 0},
                )
            return

        logger.info("Connector produced %d documents, starting ingestion", len(documents))

        # Ingest documents
        docs_ingested, total_chunks, errors = await _run_ingestion(
            state, documents, kb_id,
        )

        # Update KB registry
        await _ensure_kb_and_update_counts(
            state, kb_id, source_name, metadata, docs_ingested, total_chunks,
        )

        # Update sync status
        await _update_sync_status(
            ds_repo, run_repo, source_id, run_id,
            docs_ingested, len(documents), total_chunks, errors,
        )

    except Exception as exc:
        await _report_sync_failure(ds_repo, run_repo, source_id, run_id, exc)
    finally:
        if ocr_started:
            try:
                await _stop_ocr_instance()
            except Exception as e:
                logger.warning("Failed to stop PaddleOCR instance: %s", e)


# ---------------------------------------------------------------------------
# Git source sync path
# ---------------------------------------------------------------------------

async def _run_git_source_sync(source: dict[str, Any], state: Any) -> None:
    """Clone/pull a git repo via GitConnector, then run the ingestion pipeline."""
    source_id = source["id"]
    kb_id = source.get("kb_id", "knowledge")
    source_name = source.get("name", "unknown")
    metadata = source.get("metadata") or {}

    ds_repo = state.get("data_source_repo")
    run_repo = state.get("ingestion_run_repo")
    run_id = str(uuid.uuid4())

    last_fingerprint = (source.get("last_sync_result") or {}).get("version_fingerprint")

    try:
        logger.info(
            "Starting git sync for source %s (kb=%s, repo=%s)",
            source_name, kb_id, (source.get("crawl_config") or {}).get("repo_url"),
        )

        if run_repo:
            try:
                await run_repo.create({
                    "id": run_id, "kb_id": kb_id,
                    "source_type": "git", "source_name": source_name,
                    "status": "running", "started_at": datetime.now(UTC),
                })
            except Exception as e:
                logger.warning("Failed to create ingestion run record: %s", e)

        from src.connectors.git import GitConnector

        connector = GitConnector()
        connector_config = dict(source.get("crawl_config") or {})
        connector_config.setdefault("name", source_name)
        connector_config.setdefault("id", source_id)

        result = await connector.fetch(
            connector_config, force=False, last_fingerprint=last_fingerprint,
        )
        if not result.success:
            raise RuntimeError(result.error or "git connector failed")

        if result.skipped:
            logger.info("git source %s unchanged (commit %s), skipping ingest",
                        source_name, result.metadata.get("commit_sha", "")[:8])
            if ds_repo:
                await ds_repo.complete_sync(
                    source_id, "active",
                    sync_result={
                        "documents_synced": 0,
                        "chunks_stored": 0,
                        "skipped": True,
                        "version_fingerprint": result.version_fingerprint,
                        "commit_sha": result.metadata.get("commit_sha", ""),
                        "completed_at": datetime.now(UTC).isoformat(),
                    },
                )
            if run_repo:
                try:
                    await run_repo.complete(run_id, {
                        "status": "completed", "documents_ingested": 0,
                        "documents_fetched": 0, "chunks_stored": 0,
                        "errors": [], "completed_at": datetime.now(UTC),
                    })
                except Exception as e:
                    logger.warning("Failed to complete ingestion run record: %s", e)
            return

        documents = result.documents or []
        if not documents:
            logger.warning("git source %s produced 0 documents", source_name)
            if ds_repo:
                await ds_repo.complete_sync(
                    source_id, "active",
                    sync_result={
                        "documents_synced": 0, "chunks_stored": 0,
                        "version_fingerprint": result.version_fingerprint,
                    },
                )
            return

        logger.info("git connector produced %d documents, starting ingestion",
                    len(documents))

        docs_ingested, total_chunks, errors = await _run_ingestion(
            state, documents, kb_id,
        )

        await _ensure_kb_and_update_counts(
            state, kb_id, source_name, metadata, docs_ingested, total_chunks,
        )

        sync_result = {
            "documents_synced": docs_ingested,
            "documents_total": len(documents),
            "chunks_stored": total_chunks,
            "errors": errors[:10],
            "version_fingerprint": result.version_fingerprint,
            "commit_sha": result.metadata.get("commit_sha", ""),
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
        await _report_sync_failure(ds_repo, run_repo, source_id, run_id, exc)
