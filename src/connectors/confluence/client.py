"""Confluence full-content crawling client.

Provides :class:`ConfluenceFullClient`, an async HTTP client that crawls
Confluence page trees (recursive DFS, flat, or BFS), extracts rich content
(body, tables, mentions, attachments with OCR), and persists results via
JSONL checkpointing for crash-safe incremental crawling.

This module is the **facade** that composes functionality from:

- :mod:`._checkpoint` — checkpoint & incremental JSONL persistence
- :mod:`._http` — HTTP retry transport
- :mod:`._content` — page content extraction & metadata
- :mod:`._attachments` — attachment download & content parsing
- :mod:`._crawler` — crawl strategies (DFS, BFS, flat, CQL)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .attachment_parser import AttachmentParser
from .models import FullPageContent

# --- Mixin imports (private) ---
from ._attachments import AttachmentMixin
from ._checkpoint import CheckpointMixin
from ._content import ContentMixin
from ._crawler import CrawlerMixin
from ._http import HttpMixin


class ConfluenceFullClient(
    CheckpointMixin,
    HttpMixin,
    ContentMixin,
    AttachmentMixin,
    CrawlerMixin,
):
    """Async Confluence crawling client with incremental JSONL checkpointing.

    All public methods are inherited from the mixin classes listed above.
    This class owns only ``__init__`` (wiring) and class-level constants.
    """

    CHECKPOINT_INTERVAL = 10  # Save checkpoint every N pages

    def __init__(
        self,
        base_url: str = "",
        pat: str = "",
        output_dir: Path | None = None,
        max_concurrent: int = 1,
        kb_id: str = "",
    ) -> None:
        from src.config import get_settings

        self.base_url = (
            base_url
            or os.getenv("CONFLUENCE_BASE_URL")
            or get_settings().confluence.base_url
        )
        _pat = pat or os.getenv("CONFLUENCE_PAT", "")
        self.headers = {
            "Authorization": f"Bearer {_pat}",
            "Accept": "application/json",
        }
        _timeout = float(os.getenv("CONFLUENCE_CRAWL_TIMEOUT", "30"))
        _verify_ssl = os.getenv("CONFLUENCE_VERIFY_SSL", "false").lower() in (
            "true", "1", "yes",
        )
        self.client = httpx.AsyncClient(
            timeout=_timeout,
            verify=_verify_ssl,
            headers=self.headers,  # NOSONAR — SSL configurable via env
        )
        self.all_pages: list[FullPageContent] = []
        self.visited_pages: set[str] = set()
        # PR-5 (B) — fetch/parse 가 실패한 page_id 를 visited 와 별도로 추적.
        # checkpoint 에 저장되어 다음 run 또는 ``--retry-confluence-failed`` 로
        # 재시도 가능. visited 는 영구 skip, failed 는 재시도 후보.
        self.failed_pages: set[str] = set()
        self.kb_id = kb_id

        # Parallel crawling settings
        self._max_concurrent = max(1, max_concurrent)
        self._page_sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(self._max_concurrent)
            if self._max_concurrent > 1
            else None
        )

        # Checkpoint settings
        if output_dir is not None:
            self.output_dir = output_dir
        else:
            from .config import _resolve_output_dir

            self.output_dir = _resolve_output_dir()

        self.attachments_dir = self.output_dir / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

        self.checkpoint_dir = self.output_dir
        self.checkpoint_file = self.checkpoint_dir / "checkpoint.json"
        self._pages_since_checkpoint = 0
        self._incremental_saved_count = 0
        self._total_pages_crawled = 0
        self._shutdown_requested = False
        self._started_at = time.monotonic()
        self._runtime_stats: dict[str, Any] = {
            "pages_total": 0,
            "attachments_total": 0,
            "attachments_ocr_applied": 0,
            "attachments_ocr_skipped": 0,
            "attachments_ocr_deferred": 0,
            "pdf_pages_ocr_attempted": 0,
            "pdf_pages_ocr_deferred": 0,
            "ppt_slides_ocr_attempted": 0,
            "ppt_slides_ocr_deferred": 0,
            "native_text_chars_total": 0,
            "ocr_text_chars_total": 0,
            "attachment_ocr_mode": AttachmentParser.current_policy().attachment_ocr_mode,
        }
