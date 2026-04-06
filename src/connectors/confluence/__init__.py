"""Confluence crawler package.

Modular Confluence REST API crawler with attachment parsing, OCR, and BFS parallelism.
Extracted from oreo-ecosystem's confluence_full_crawler.py.

Usage (programmatic):
    from src.connectors.confluence import crawl_space, CrawlerConfig

    config = CrawlerConfig(base_url=..., pat=..., output_dir=..., ...)
    result = await crawl_space(config, page_id="373865276", source_name="wiki", ...)

Usage (CLI):
    uv run python scripts/confluence_crawler.py --page-id 373865276 --full
"""

from __future__ import annotations

import logging
import signal

from .client import ConfluenceFullClient
from .config import CrawlerConfig
from .models import CrawlSpaceResult, FullPageContent, page_to_dict
from .output import save_results, save_results_from_jsonl

logger = logging.getLogger(__name__)

__all__ = [
    "CrawlerConfig",
    "ConfluenceFullClient",
    "CrawlSpaceResult",
    "FullPageContent",
    "crawl_space",
    "page_to_dict",
    "save_results",
    "save_results_from_jsonl",
]


async def crawl_space(
    config: CrawlerConfig,
    page_id: str,
    source_name: str,
    source_key: str,
    max_pages: int | None = None,
    download_attachments: bool = True,
    max_attachments_per_page: int = 20,
    resume: bool = False,
    max_concurrent: int = 3,
    kb_id: str = "",
    use_bfs: bool = True,
    register_signals: bool = False,
) -> CrawlSpaceResult:
    """High-level crawl function for programmatic and CLI use.

    Args:
        config: Crawler configuration (base_url, pat, output_dir, etc.)
        page_id: Root Confluence page ID to crawl from.
        source_name: Human-readable source name.
        source_key: Machine key for checkpoint/output files.
        max_pages: Limit number of pages to crawl (None = unlimited).
        download_attachments: Whether to download and parse attachments.
        max_attachments_per_page: Max attachments per page.
        resume: Resume from previous checkpoint.
        max_concurrent: Number of concurrent page fetches.
        kb_id: Knowledge base ID for checkpoint validation.
        use_bfs: Use BFS (queue-based) instead of DFS (recursive) for full crawls.
        register_signals: Register SIGTERM/SIGINT handlers (only in main thread).
    """
    from .attachment_parser import AttachmentParser

    AttachmentParser.configure_run(source_key)

    client = ConfluenceFullClient(
        base_url=config.base_url,
        pat=config.pat,
        output_dir=config.output_dir,
        max_concurrent=max_concurrent,
        kb_id=kb_id,
    )

    interrupted = False

    if register_signals:
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _graceful_shutdown(signum, _frame):
            nonlocal interrupted
            if interrupted:
                return
            interrupted = True
            client.request_shutdown()
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
            logger.warning("%s received — finishing current work then stopping", sig_name)

        signal.signal(signal.SIGTERM, _graceful_shutdown)
        signal.signal(signal.SIGINT, _graceful_shutdown)

    if resume:
        loaded = client.load_incremental(source_key)
        if client.load_checkpoint(source_key):
            logger.info("Resuming from checkpoint")
        elif loaded > 0:
            logger.info("Restored %d pages from incremental file, continuing", loaded)
        else:
            logger.info("No checkpoint found, starting from scratch")
    else:
        client.clear_checkpoint()
        client.clear_incremental(source_key)

    try:
        if use_bfs and not resume and max_pages is None:
            # BFS for full crawls — better parallelism
            await client.crawl_bfs(
                root_page_id=page_id,
                max_depth=10,
                max_pages=max_pages,
                download_attachments=download_attachments,
                max_attachments_per_page=max_attachments_per_page,
                source_key=source_key,
            )
        else:
            # DFS for resume, sample crawls, or explicit request
            await client.crawl_recursive(
                page_id,
                max_depth=10,
                max_pages=max_pages,
                download_attachments=download_attachments,
                max_attachments_per_page=max_attachments_per_page,
                source_key=source_key,
            )

        client.save_incremental(source_key)

        if client.shutdown_requested:
            client.save_checkpoint(source_key)
            page_dicts = client.finalize_from_incremental(source_key)
            client.write_runtime_stats()
            logger.info("Safely saved. Use --resume to continue.")
            return CrawlSpaceResult(
                pages=client.all_pages,
                page_dicts=page_dicts,
                interrupted=True,
                source_key=source_key,
            )

        page_dicts = client.finalize_from_incremental(source_key)
        client.write_runtime_stats()

        logger.info("Crawl complete: %d pages", client._total_pages_crawled)
        return CrawlSpaceResult(
            pages=client.all_pages,
            page_dicts=page_dicts,
            source_key=source_key,
        )
    finally:
        if register_signals:
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
        await client.close()
