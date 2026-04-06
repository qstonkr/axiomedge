"""Confluence full-content crawling client.

Provides :class:`ConfluenceFullClient`, an async HTTP client that crawls
Confluence page trees (recursive DFS, flat, or BFS), extracts rich content
(body, tables, mentions, attachments with OCR), and persists results via
JSONL checkpointing for crash-safe incremental crawling.

This module is a refactored extraction of the ``ConfluenceFullClient`` class
originally defined in ``scripts/confluence_full_crawler.py``.  All S3 backup
logic has been removed; checkpoint persistence is local-only.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .attachment_parser import AttachmentParser
from .html_parsers import (
    CodeBlockExtractor,
    EmailExtractor,
    LinkExtractor,
    MacroExtractor,
    MentionExtractor,
    PlainTextExtractor,
    SectionExtractor,
    TableExtractor,
)
from .models import (
    AttachmentContent,
    AttachmentParseResult,
    ExtractedComment,
    ExtractedLabel,
    ExtractedRestriction,
    FullPageContent,
    page_to_dict,
)
from .structured_ir import extract_creator_info, generate_structured_ir

logger = logging.getLogger(__name__)


class ConfluenceFullClient:
    """Async Confluence crawling client with incremental JSONL checkpointing."""

    CHECKPOINT_INTERVAL = 10  # Save checkpoint every N pages

    def __init__(
        self,
        base_url: str = "",
        pat: str = "",
        output_dir: Path | None = None,
        max_concurrent: int = 1,
        kb_id: str = "",
    ):
        self.base_url = base_url or os.getenv(
            "CONFLUENCE_BASE_URL", "https://wiki.gsretail.com"
        )
        _pat = pat or os.getenv("CONFLUENCE_PAT", "")
        self.headers = {
            "Authorization": f"Bearer {_pat}",
            "Accept": "application/json",
        }
        _timeout = float(os.getenv("CONFLUENCE_CRAWL_TIMEOUT", "30"))
        _verify_ssl = os.getenv("CONFLUENCE_VERIFY_SSL", "false").lower() in ("true", "1", "yes")
        self.client = httpx.AsyncClient(
            timeout=_timeout, verify=_verify_ssl, headers=self.headers  # NOSONAR — SSL configurable via env
        )
        self.all_pages: list[FullPageContent] = []
        self.visited_pages: set[str] = set()
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

    # ------------------------------------------------------------------
    # Runtime stats helpers
    # ------------------------------------------------------------------

    def runtime_stats_path(self) -> Path:
        return self.checkpoint_dir / "crawl_runtime_stats.json"

    def _record_attachment_stats(self, attachment: AttachmentContent) -> None:
        stats = self._runtime_stats
        stats["attachments_total"] += 1
        stats["native_text_chars_total"] += attachment.native_text_chars or 0
        stats["ocr_text_chars_total"] += attachment.ocr_text_chars or 0

        if attachment.ocr_applied:
            stats["attachments_ocr_applied"] += 1
        elif attachment.ocr_units_deferred > 0:
            stats["attachments_ocr_deferred"] += 1
        elif attachment.ocr_skip_reason and attachment.ocr_skip_reason not in {
            "ocr_failed",
            "parse_error",
        }:
            stats["attachments_ocr_skipped"] += 1

        media_type = (attachment.media_type or "").lower()
        if "pdf" in media_type:
            stats["pdf_pages_ocr_attempted"] += attachment.ocr_units_attempted or 0
            stats["pdf_pages_ocr_deferred"] += attachment.ocr_units_deferred or 0
        elif "presentation" in media_type or "powerpoint" in media_type:
            stats["ppt_slides_ocr_attempted"] += attachment.ocr_units_attempted or 0
            stats["ppt_slides_ocr_deferred"] += attachment.ocr_units_deferred or 0

    def write_runtime_stats(self) -> None:
        payload = {
            **self._runtime_stats,
            "pages_total": self._total_pages_crawled,
            "elapsed_seconds": round(time.monotonic() - self._started_at, 2),
        }
        with open(self.runtime_stats_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Checkpoint persistence (local only)
    # ------------------------------------------------------------------

    def save_checkpoint(self, source_key: str) -> None:
        """Save current crawl progress to a local checkpoint file."""
        checkpoint_data = {
            "source_key": source_key,
            "kb_id": self.kb_id,
            "visited_pages": list(self.visited_pages),
            "pages_count": self._total_pages_crawled,
            "last_page_id": self.all_pages[-1].page_id if self.all_pages else None,
            "last_page_title": self.all_pages[-1].title if self.all_pages else None,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        # Atomic write via temp file
        temp_file = self.checkpoint_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        temp_file.rename(self.checkpoint_file)

        logger.info("Checkpoint saved: %d pages", len(self.visited_pages))

    def load_checkpoint(self, source_key: str) -> bool:
        """Restore crawl state from a local checkpoint file."""
        checkpoint_data = None

        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint_data = json.load(f)
            except Exception:
                checkpoint_data = None

        if checkpoint_data is None:
            return False

        try:
            # Verify same source
            if checkpoint_data.get("source_key") != source_key:
                logger.warning(
                    "Checkpoint belongs to a different source (%s). Ignoring.",
                    checkpoint_data.get("source_key"),
                )
                return False

            # KB ID mismatch guard
            saved_kb_id = checkpoint_data.get("kb_id", "")
            if saved_kb_id and self.kb_id and saved_kb_id != self.kb_id:
                logger.warning(
                    "Checkpoint KB mismatch (%s != %s). Starting fresh.",
                    saved_kb_id,
                    self.kb_id,
                )
                self.visited_pages.clear()
                return False

            # Restore visited pages (merge with already-loaded incremental data)
            checkpoint_visited = set(checkpoint_data.get("visited_pages", []))
            self.visited_pages = self.visited_pages | checkpoint_visited
            self._total_pages_crawled = checkpoint_data.get("pages_count", 0)

            saved_at = checkpoint_data.get("saved_at", "unknown")
            last_title = checkpoint_data.get("last_page_title", "unknown")
            pages_count = checkpoint_data.get("pages_count", 0)

            logger.info(
                "Resuming from checkpoint: saved_at=%s, pages=%d, last_page=%s, "
                "skip=%d",
                saved_at,
                pages_count,
                last_title,
                len(self.visited_pages),
            )

            return True
        except Exception as e:
            logger.error("Failed to load checkpoint: %s", e)
            return False

    def clear_checkpoint(self) -> None:
        """Delete the checkpoint file."""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
            logger.info("Checkpoint deleted")

    # ------------------------------------------------------------------
    # Incremental JSONL persistence
    # ------------------------------------------------------------------

    def _get_incremental_path(self, source_key: str) -> Path:
        safe_key = re.sub(r"[^\w]", "_", source_key)
        return self.checkpoint_dir / f"incremental_{safe_key}.jsonl"

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        """Set the shutdown flag to gracefully stop crawling."""
        self._shutdown_requested = True

    def _truncate_partial_jsonl_tail(self, jsonl_path: Path) -> None:
        """Remove the last incomplete line from a JSONL file after a write failure."""
        if not jsonl_path.exists():
            return

        try:
            with open(jsonl_path, "rb+") as f:
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                if file_size == 0:
                    return

                f.seek(file_size - 1)
                if f.read(1) == b"\n":
                    return

                window_size = min(file_size, 8192)
                f.seek(file_size - window_size)
                tail = f.read(window_size)
                last_newline = tail.rfind(b"\n")

                if last_newline == -1:
                    f.seek(0)
                    f.truncate(0)
                else:
                    truncate_pos = file_size - window_size + last_newline + 1
                    f.seek(truncate_pos)
                    f.truncate()

            logger.warning("Repaired incomplete trailing line in incremental file.")
        except Exception as repair_error:
            logger.warning("Incremental file repair failed: %s", repair_error)

    def save_incremental(self, source_key: str) -> None:
        """Append newly crawled pages to a JSONL file and free memory."""
        new_pages = self.all_pages[self._incremental_saved_count:]
        if not new_pages:
            return

        jsonl_path = self._get_incremental_path(source_key)
        payload_lines = [
            json.dumps(page_to_dict(page), ensure_ascii=False) + "\n"
            for page in new_pages
        ]

        try:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.writelines(payload_lines)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self._truncate_partial_jsonl_tail(jsonl_path)
            raise

        # Free memory: all pages have been flushed to JSONL on disk.
        self.all_pages.clear()
        self._incremental_saved_count = 0
        self.write_runtime_stats()
        logger.info(
            "Incremental save: %d pages added (total %d, memory freed)",
            len(new_pages),
            self._total_pages_crawled,
        )

    def load_incremental(self, source_key: str) -> int:
        """Load previously saved pages from a JSONL file.

        Only pages with non-empty content_text are marked as visited.
        Pages with empty content (e.g., index/TOC pages, failed extractions)
        are left unvisited so they get re-crawled on resume.
        """
        jsonl_path = self._get_incremental_path(source_key)
        if not jsonl_path.exists():
            return 0

        loaded = 0
        skipped_empty = 0
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    page_data = json.loads(line)
                    page_id = page_data.get("page_id", "")
                    if not page_id:
                        continue
                    content_text = page_data.get("content_text", "")
                    if content_text and len(content_text.strip()) > 0:
                        self.visited_pages.add(page_id)
                        loaded += 1
                    else:
                        skipped_empty += 1
        except Exception as e:
            logger.warning("Error loading incremental file: %s", e)

        if loaded > 0 or skipped_empty > 0:
            logger.info(
                "Incremental file: %d pages marked visited, %d pages to re-crawl",
                loaded,
                skipped_empty,
            )
            self._incremental_saved_count = 0
        return loaded

    def clear_incremental(self, source_key: str) -> None:
        """Delete the incremental JSONL file."""
        jsonl_path = self._get_incremental_path(source_key)
        if jsonl_path.exists():
            jsonl_path.unlink()

    def finalize_from_incremental(self, source_key: str) -> list[dict]:
        """Merge incremental JSONL and in-memory pages into a final list of dicts."""
        all_page_dicts: list[dict] = []
        seen_ids: set[str] = set()

        # 1. Load from JSONL
        jsonl_path = self._get_incremental_path(source_key)
        if jsonl_path.exists():
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    page_data = json.loads(line)
                    pid = page_data.get("page_id", "")
                    if pid and pid not in seen_ids:
                        all_page_dicts.append(page_data)
                        seen_ids.add(pid)

        # 2. Append unsaved in-memory pages
        for p in self.all_pages:
            if p.page_id not in seen_ids:
                all_page_dicts.append(page_to_dict(p))
                seen_ids.add(p.page_id)

        return all_page_dicts

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self.client.aclose()

    _RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    @staticmethod
    async def _backoff_and_log(
        error: Exception, attempt: int, max_retries: int, url: str,
        base_delay: int, max_delay: int,
    ) -> None:
        """Log a retry warning and sleep with exponential backoff."""
        if attempt >= max_retries - 1:
            return
        wait = min(2**attempt * base_delay, max_delay)
        label = (
            f"HTTP {error.response.status_code}"  # type: ignore[union-attr]
            if isinstance(error, httpx.HTTPStatusError)
            else type(error).__name__
        )
        logger.warning("%s retry %d/%d: %s", label, attempt + 1, max_retries, url[:80])
        await asyncio.sleep(wait)

    async def _http_get_with_retry(
        self,
        url: str,
        params: dict | None = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """HTTP GET with exponential backoff for transient errors."""
        last_error: Exception | None = None
        for attempt in range(max_retries):
            if self.shutdown_requested:
                raise RuntimeError("Shutdown requested during HTTP retry")
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.PoolTimeout,
            ) as e:
                last_error = e
                await self._backoff_and_log(e, attempt, max_retries, url, 2, 30)
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in self._RETRYABLE_STATUS_CODES:
                    raise
                last_error = e
                await self._backoff_and_log(e, attempt, max_retries, url, 5, 60)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unreachable: HTTP retry exhausted without error")

    # ------------------------------------------------------------------
    # Confluence API wrappers
    # ------------------------------------------------------------------

    async def get_user_details(self, account_id: str) -> dict | None:
        """Fetch user details (email, display name) by account ID."""
        if not account_id:
            return None

        url = f"{self.base_url}/rest/api/user"
        params = {"accountId": account_id}

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return {
                "account_id": account_id,
                "display_name": data.get("displayName"),
                "email": data.get("email"),
                "profile_picture": data.get("profilePicture", {}).get("path"),
            }
        except Exception:
            return None

    async def get_comments(self, page_id: str) -> list[ExtractedComment]:
        """Fetch page comments."""
        url = f"{self.base_url}/rest/api/content/{page_id}/child/comment"
        params = {
            "expand": "body.storage,history.createdBy",
            "limit": 100,
        }
        comments: list[ExtractedComment] = []

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            for comment in data.get("results", []):
                comment_id = comment.get("id", "")
                history = comment.get("history", {})
                created_by = history.get("createdBy", {})

                body_html = (
                    comment.get("body", {}).get("storage", {}).get("value", "")
                )
                text_extractor = PlainTextExtractor()
                text_extractor.feed(body_html)
                content = text_extractor.get_text()

                comments.append(
                    ExtractedComment(
                        comment_id=comment_id,
                        author=created_by.get("displayName", "Unknown"),
                        author_email=created_by.get("email"),
                        content=content,
                        created_at=history.get("createdDate", ""),
                        parent_id=(
                            comment.get("ancestors", [{}])[0].get("id")
                            if comment.get("ancestors")
                            else None
                        ),
                    )
                )

        except Exception as e:
            logger.warning("Could not fetch comments of %s: %s", page_id, e)

        return comments

    async def get_labels(self, page_id: str) -> list[ExtractedLabel]:
        """Fetch page labels (tags)."""
        url = f"{self.base_url}/rest/api/content/{page_id}/label"
        labels: list[ExtractedLabel] = []

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()

            for label in data.get("results", []):
                labels.append(
                    ExtractedLabel(
                        name=label.get("name", ""),
                        prefix=label.get("prefix"),
                    )
                )

        except Exception as e:
            logger.warning("Could not fetch labels of %s: %s", page_id, e)

        return labels

    @staticmethod
    def _extract_content_elements(body_html: str, title: str) -> dict[str, Any]:
        """Extract all content elements (text, tables, mentions, etc.) from HTML."""
        text_extractor = PlainTextExtractor()
        text_extractor.feed(body_html)
        content_text = text_extractor.get_text()

        table_extractor = TableExtractor()
        table_extractor.feed(body_html)

        mention_extractor = MentionExtractor()
        mention_extractor.feed(body_html)

        section_extractor = SectionExtractor()
        section_extractor.feed(body_html)

        code_extractor = CodeBlockExtractor()
        try:
            code_extractor.feed(body_html)
        except Exception:
            pass

        email_extractor = EmailExtractor()
        try:
            email_extractor.feed(body_html)
        except Exception:
            pass

        macro_extractor = MacroExtractor()
        try:
            macro_extractor.feed(body_html)
        except Exception:
            pass

        content_ir = generate_structured_ir(
            content_text=content_text,
            content_html=body_html,
            title=title,
            tables=table_extractor.tables,
            sections=section_extractor.sections,
            mentions=mention_extractor.mentions,
        )

        return {
            "content_text": content_text,
            "tables": table_extractor.tables,
            "mentions": mention_extractor.mentions,
            "sections": section_extractor.sections,
            "code_blocks": code_extractor.code_blocks,
            "emails": email_extractor.emails,
            "macros": macro_extractor.macros,
            "content_ir": content_ir,
        }

    async def _extract_page_metadata(
        self, data: dict, page_id: str
    ) -> dict[str, Any]:
        """Extract metadata (creator, version, space, ancestors, etc.)."""
        history = data.get("history", {})
        created_by_data = history.get("createdBy", {})
        creator = created_by_data.get("displayName", "Unknown")
        creator_account_id = created_by_data.get("accountId")
        creator_name, creator_team = extract_creator_info(creator)
        created_at = history.get("createdDate", "")

        creator_email = None
        if creator_account_id:
            user_details = await self.get_user_details(creator_account_id)
            if user_details:
                creator_email = user_details.get("email")

        last_updated = history.get("lastUpdated", {})
        last_modifier = last_updated.get("by", {}).get("displayName", creator)
        updated_at = last_updated.get("when", created_at)
        version = data.get("version", {}).get("number", 1)

        version_data = data.get("version", {})
        version_history = [{
            "number": version_data.get("number", version),
            "when": version_data.get("when", updated_at),
            "by": version_data.get("by", {}).get("displayName", last_modifier),
            "message": version_data.get("message", ""),
        }]

        return {
            "creator": creator,
            "creator_name": creator_name,
            "creator_team": creator_team,
            "creator_email": creator_email,
            "last_modifier": last_modifier,
            "version": version,
            "created_at": created_at,
            "updated_at": updated_at,
            "url": f"{self.base_url}/pages/viewpage.action?pageId={page_id}",
            "space_key": data.get("space", {}).get("key"),
            "ancestors": [
                {"id": a.get("id"), "title": a.get("title")}
                for a in data.get("ancestors", [])
            ],
            "version_history": version_history,
        }

    @staticmethod
    def _extract_restrictions(data: dict) -> list[ExtractedRestriction]:
        """Extract read/update restrictions from page data."""
        restrictions: list[ExtractedRestriction] = []
        restrictions_data = data.get("restrictions", {})

        for operation in ("read", "update"):
            op_restrictions = restrictions_data.get(operation, {}).get(
                "restrictions", {}
            )
            for user in op_restrictions.get("user", {}).get("results", []):
                restrictions.append(
                    ExtractedRestriction(
                        operation=operation,
                        restriction_type="user",
                        name=user.get("displayName", ""),
                        account_id=user.get("accountId"),
                    )
                )
            for group in op_restrictions.get("group", {}).get("results", []):
                restrictions.append(
                    ExtractedRestriction(
                        operation=operation,
                        restriction_type="group",
                        name=group.get("name", ""),
                    )
                )

        return restrictions

    async def _enrich_mentions_with_email(self, mentions: list) -> None:
        """Look up email addresses for mentioned users."""
        for mention in mentions:
            if mention.user_id:
                user_details = await self.get_user_details(mention.user_id)
                if user_details:
                    mention.email = user_details.get("email")
                    if not mention.display_name:
                        mention.display_name = user_details.get("display_name")

    async def get_page_full(self, page_id: str) -> FullPageContent | None:
        """Fetch full page content with all metadata."""
        url = f"{self.base_url}/rest/api/content/{page_id}"
        _default_expand = (
            "body.storage,version,space,ancestors,"
            "history.createdBy,history.lastUpdated,metadata.labels,"
            "restrictions.read.restrictions.user,"
            "restrictions.read.restrictions.group,"
            "restrictions.update.restrictions.user,"
            "restrictions.update.restrictions.group"
        )
        params = {"expand": os.getenv("CONFLUENCE_CRAWL_EXPAND", _default_expand)}

        try:
            response = await self._http_get_with_retry(url, params=params)
            data = response.json()

            title = data.get("title", "Unknown")
            body_html = data.get("body", {}).get("storage", {}).get("value", "")

            elements = self._extract_content_elements(body_html, title)
            meta = await self._extract_page_metadata(data, page_id)
            restrictions = self._extract_restrictions(data)

            labels = await self.get_labels(page_id)
            comments = await self.get_comments(page_id)

            link_extractor = LinkExtractor(base_url=self.base_url)
            try:
                link_extractor.feed(body_html)
            except Exception:
                pass

            await self._enrich_mentions_with_email(elements["mentions"])

            content_text = elements["content_text"]
            return FullPageContent(
                page_id=page_id,
                title=title,
                content_text=content_text,
                content_html=body_html,
                content_preview=(
                    content_text[:200] + "..."
                    if len(content_text) > 200
                    else content_text
                ),
                content_ir=elements["content_ir"],
                tables=elements["tables"],
                mentions=elements["mentions"],
                sections=elements["sections"],
                code_blocks=elements["code_blocks"],
                creator=meta["creator"],
                creator_name=meta["creator_name"],
                creator_team=meta["creator_team"],
                creator_email=meta["creator_email"],
                last_modifier=meta["last_modifier"],
                version=meta["version"],
                url=meta["url"],
                created_at=meta["created_at"],
                updated_at=meta["updated_at"],
                labels=labels,
                comments=comments,
                emails=elements["emails"],
                macros=elements["macros"],
                space_key=meta["space_key"],
                ancestors=meta["ancestors"],
                internal_links=link_extractor.internal_links,
                external_links=link_extractor.external_links,
                restrictions=restrictions,
                version_history=meta["version_history"],
            )

        except httpx.TimeoutException as e:
            logger.error("Page %s TIMEOUT (%s)", page_id, type(e).__name__)
            return None
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            logger.error("Page %s HTTP %d: %s", page_id, status, body)
            return None
        except Exception as e:
            logger.error(
                "Page %s ERROR (%s): %s", page_id, type(e).__name__, e
            )
            return None

    async def get_attachments(self, page_id: str) -> list[dict]:
        """Fetch attachment metadata for a page."""
        url = f"{self.base_url}/rest/api/content/{page_id}/child/attachment"
        params = {"limit": 100}

        try:
            response = await self._http_get_with_retry(url, params=params)
            return response.json().get("results", [])
        except Exception as e:
            logger.warning(
                "Could not fetch attachments of %s: %s", page_id, e
            )
            return []

    @staticmethod
    async def _parse_attachment_content(
        file_path: Path,
        content: bytes,
        media_type: str,
        filename: str,
    ) -> AttachmentParseResult | None:
        """Dispatch attachment content to the appropriate parser."""
        media_lower = media_type.lower()
        filename_lower = filename.lower()

        def status_emit(message: str) -> None:
            logger.info("[attachment] %s", message)

        if "pdf" in media_lower or filename_lower.endswith(".pdf"):
            return AttachmentParser.parse_pdf(file_path, heartbeat_fn=status_emit)

        if any(x in media_lower for x in ("spreadsheet", "excel", "xlsx", "xls")) or any(
            filename_lower.endswith(ext) for ext in (".xlsx", ".xls", ".xlsm")
        ):
            return AttachmentParser.parse_excel(file_path)

        if any(x in media_lower for x in ("presentation", "powerpoint", "pptx", "ppt")) or any(
            filename_lower.endswith(ext) for ext in (".pptx", ".ppt")
        ):
            return AttachmentParser.parse_ppt(file_path, heartbeat_fn=status_emit)

        if any(x in media_lower for x in ("word", "docx", "doc")) or any(
            filename_lower.endswith(ext) for ext in (".docx", ".doc")
        ):
            return AttachmentParser.parse_word(file_path)

        if "image" in media_lower or any(
            filename_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp")
        ):
            return await AttachmentParser.parse_image_async(file_path, content)

        if filename_lower.endswith(".txt") or "text" in media_lower:
            return ConfluenceFullClient._decode_text_attachment(content, 1.0)

        if filename_lower.endswith(".csv"):
            return ConfluenceFullClient._decode_text_attachment(content, 0.95)

        return AttachmentParseResult(
            extracted_text=f"[Unsupported format: {media_type}]",
            extracted_tables=[],
            confidence=0.0,
            ocr_skip_reason="unsupported_media_type",
        )

    @staticmethod
    def _decode_text_attachment(
        content: bytes, confidence: float
    ) -> AttachmentParseResult:
        """Decode a text/CSV attachment with UTF-8 → CP949 fallback."""
        try:
            text = content.decode("utf-8")
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=confidence,
                native_text_chars=AttachmentParser._text_chars(text),
            )
        except UnicodeDecodeError:
            text = content.decode("cp949", errors="replace")
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=0.8,
                native_text_chars=AttachmentParser._text_chars(text),
            )

    @staticmethod
    def _apply_parse_result(
        result: AttachmentContent, parse_result: AttachmentParseResult | None
    ) -> None:
        """Copy fields from AttachmentParseResult into AttachmentContent."""
        if parse_result is not None:
            result.extracted_text = parse_result.extracted_text
            result.extracted_tables = parse_result.extracted_tables
            result.ocr_confidence = parse_result.confidence
            result.ocr_mode = parse_result.ocr_mode
            result.ocr_applied = parse_result.ocr_applied
            result.ocr_skip_reason = parse_result.ocr_skip_reason
            result.ocr_units_attempted = parse_result.ocr_units_attempted
            result.ocr_units_extracted = parse_result.ocr_units_extracted
            result.ocr_units_deferred = parse_result.ocr_units_deferred
            result.native_text_chars = parse_result.native_text_chars
            result.ocr_text_chars = parse_result.ocr_text_chars
        if result.extracted_text is None:
            result.extracted_text = ""

    async def download_attachment(
        self, attachment: dict, page_id: str
    ) -> AttachmentContent:
        """Download an attachment and extract its content."""
        att_id = attachment.get("id", "")
        filename = attachment.get("title", "")
        media_type = attachment.get("extensions", {}).get("mediaType", "unknown")
        file_size = attachment.get("extensions", {}).get("fileSize", 0)
        download_url = (
            f"{self.base_url}"
            f"{attachment.get('_links', {}).get('download', '')}"
        )

        has_visual = any(
            filename.lower().endswith(ext)
            for ext in (
                ".pptx", ".ppt", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp"
            )
        )

        result = AttachmentContent(
            id=att_id,
            filename=filename,
            media_type=media_type,
            file_size=file_size,
            download_url=download_url,
            has_visual_content=has_visual,
            ocr_mode=(
                AttachmentParser.current_policy().attachment_ocr_mode
                if has_visual
                else None
            ),
        )

        # File size limit (50MB)
        if file_size > 50 * 1024 * 1024:
            result.parse_error = (
                f"File size exceeded ({file_size / 1024 / 1024:.1f}MB > 50MB)"
            )
            self._record_attachment_stats(result)
            return result

        try:
            response = await self.client.get(download_url)
            response.raise_for_status()
            content = response.content

            # Save file
            safe_filename = re.sub(r"[^\w\-. ]", "_", filename)
            file_path = self.attachments_dir / f"{page_id}_{safe_filename}"
            await asyncio.to_thread(file_path.write_bytes, content)
            result.download_path = str(file_path)

            parse_result = await self._parse_attachment_content(
                file_path, content, media_type, filename,
            )
            self._apply_parse_result(result, parse_result)

        except Exception as e:
            result.parse_error = str(e)

        self._record_attachment_stats(result)
        return result

    # ------------------------------------------------------------------
    # Child page discovery
    # ------------------------------------------------------------------

    async def get_child_pages(self, page_id: str) -> list[str]:
        """Return child page IDs with pagination."""
        url: str | None = (
            f"{self.base_url}/rest/api/content/{page_id}/child/page"
        )
        params: dict[str, Any] = {"limit": 100}
        child_ids: list[str] = []
        visited_urls: set[str] = set()

        try:
            while url:
                if url in visited_urls:
                    break
                visited_urls.add(url)

                response = await self._http_get_with_retry(url, params=params)
                data = response.json()

                results = data.get("results", [])
                if not results:
                    break

                for result in results:
                    child_ids.append(result.get("id"))

                links = data.get("_links", {})
                next_link = links.get("next")
                if next_link:
                    url = f"{self.base_url}{next_link}"
                    params = {}
                else:
                    url = None
        except Exception as e:
            logger.warning(
                "Could not fetch children of %s: %s", page_id, e
            )

        return child_ids

    async def _fetch_cql_page(
        self, url: str, params: dict
    ) -> tuple[list[dict], int] | None:
        """Fetch a single CQL search page. Returns (results, totalSize) or None on error."""
        try:
            resp = await self._http_get_with_retry(url, params=params)
            data = resp.json()
            return data.get("results", []), data.get("totalSize", 0)
        except Exception as e:
            logger.warning("CQL search error (start=%s): %s", params.get("start"), e)
            return None

    async def get_all_descendant_page_ids_via_cql(
        self, root_page_id: str
    ) -> set[str]:
        """Collect all descendant page IDs under *root_page_id* using CQL."""
        url = f"{self.base_url}/rest/api/content/search"
        cql = f"ancestor={root_page_id} and type=page"
        start = 0
        limit = 100
        all_ids: set[str] = set()

        while not self.shutdown_requested:
            page_result = await self._fetch_cql_page(
                url, {"cql": cql, "limit": limit, "start": start},
            )
            if page_result is None:
                break

            results, total_size = page_result
            if not results:
                break

            for r in results:
                pid = r.get("id")
                if pid:
                    all_ids.add(str(pid))

            start += limit
            if start % 1000 == 0:
                logger.info("CQL enumeration: %d/%d pages", len(all_ids), total_size)
            if start >= total_size:
                break

        logger.info("CQL enumeration complete: %d pages", len(all_ids))
        return all_ids

    # ------------------------------------------------------------------
    # Crawl strategies
    # ------------------------------------------------------------------

    async def _resume_visited_children(
        self,
        page_id: str,
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """For already-visited pages, explore children so resume works."""
        if depth > max_depth:
            return
        try:
            child_ids = await self.get_child_pages(page_id)
            if child_ids:
                await self._crawl_children(
                    child_ids, depth, max_depth, max_pages,
                    download_attachments, max_attachments_per_page,
                    progress, task_id, source_key,
                )
        except Exception:
            pass

    async def crawl_recursive(
        self,
        page_id: str,
        depth: int = 0,
        max_depth: int = 10,
        max_pages: int | None = None,
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        progress: Any | None = None,
        task_id: Any = None,
        source_key: str = "unknown",
    ) -> FullPageContent | None:
        """Recursive DFS crawl with optional parallelism."""

        if self.shutdown_requested:
            return None

        # Already visited: skip content, but still explore children for resume
        if page_id in self.visited_pages:
            await self._resume_visited_children(
                page_id, depth, max_depth, max_pages,
                download_attachments, max_attachments_per_page,
                progress, task_id, source_key,
            )
            return None
        self.visited_pages.add(page_id)

        if self._should_stop_crawl(max_pages) or depth > max_depth:
            return None

        # Phase 1: process page (semaphore-protected HTTP)
        page, child_ids = await self._process_single_page(
            page_id, download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )

        # Phase 2: crawl children (outside semaphore)
        await self._crawl_children(
            child_ids, depth, max_depth, max_pages,
            download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )

        return page

    async def _bfs_process_item(
        self,
        page_id: str,
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        source_key: str,
        queue: asyncio.Queue[tuple[str, int]],
    ) -> bool:
        """Process one BFS queue item. Returns False to stop the worker."""
        if self.shutdown_requested:
            return False
        if page_id in self.visited_pages:
            return True
        self.visited_pages.add(page_id)

        if max_pages and self._total_pages_crawled >= max_pages:
            return False
        if depth > max_depth:
            return True

        _, child_ids = await self._process_single_page(
            page_id, download_attachments, max_attachments_per_page,
            None, None, source_key,
        )
        for child_id in child_ids:
            if child_id not in self.visited_pages:
                await queue.put((child_id, depth + 1))
        return True

    async def crawl_bfs(
        self,
        root_page_id: str,
        max_depth: int = 10,
        max_pages: int | None = None,
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        source_key: str = "unknown",
    ) -> None:
        """BFS crawl using asyncio.Queue for better parallelism.

        Unlike crawl_recursive (DFS with gather), BFS flattens the task tree
        so workers can process pages from any level concurrently.
        """
        queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        await queue.put((root_page_id, 0))

        async def worker() -> None:
            while True:
                try:
                    page_id, depth = await asyncio.wait_for(
                        queue.get(), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    break

                keep_going = await self._bfs_process_item(
                    page_id, depth, max_depth, max_pages,
                    download_attachments, max_attachments_per_page,
                    source_key, queue,
                )
                queue.task_done()
                if not keep_going:
                    break

        workers = [
            asyncio.create_task(worker()) for _ in range(self._max_concurrent)
        ]
        await queue.join()
        for w in workers:
            w.cancel()

    async def _process_single_page(
        self,
        page_id: str,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
        skip_children: bool = False,
    ) -> tuple[FullPageContent | None, list[str]]:
        """Process a single page: fetch content + attachments + child IDs.

        Uses the semaphore (if configured) to limit concurrent HTTP calls.
        """
        if self._page_sem:
            async with self._page_sem:
                return await self._do_process_page(
                    page_id,
                    download_attachments,
                    max_attachments_per_page,
                    progress,
                    task_id,
                    source_key,
                    skip_children=skip_children,
                )
        return await self._do_process_page(
            page_id,
            download_attachments,
            max_attachments_per_page,
            progress,
            task_id,
            source_key,
            skip_children=skip_children,
        )

    @staticmethod
    def _validate_page_content(page: FullPageContent, page_id: str) -> None:
        """Log warnings for pages with empty or suspicious content."""
        text_len = len(page.content_text) if page.content_text else 0
        html_len = len(page.content_html) if page.content_html else 0
        if text_len > 0:
            return
        if html_len == 0:
            logger.warning(
                "Page %s (%s) has empty body (html=0, text=0) "
                "-- possible permission issue",
                page_id,
                page.title[:50],
            )
        else:
            logger.warning(
                "Page %s (%s) has HTML (%d chars) but extracted text is empty",
                page_id,
                page.title[:50],
                html_len,
            )

    async def _download_page_attachments(
        self, page: FullPageContent, page_id: str, max_attachments: int
    ) -> None:
        """Download and attach parsed attachments to a page."""
        attachments_meta = await self.get_attachments(page_id)
        target_attachments = attachments_meta[:max_attachments]
        if not target_attachments:
            return

        _att_sem = asyncio.Semaphore(2)

        async def _dl_one(meta: dict) -> AttachmentContent | None:
            if self.shutdown_requested:
                return None
            async with _att_sem:
                return await self.download_attachment(meta, page_id)

        results = await asyncio.gather(
            *[_dl_one(m) for m in target_attachments],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, (Exception, BaseException)):
                att_name = target_attachments[i].get("title", "unknown")
                logger.warning("Attachment download failed (%s): %s", att_name, r)

        page.attachments = [
            r for r in results
            if r is not None and not isinstance(r, (Exception, BaseException))
        ]

        has_images = any(
            "image" in m.get("extensions", {}).get("mediaType", "").lower()
            for m in target_attachments
        )
        if has_images:
            gc.collect()

    async def _do_process_page(
        self,
        page_id: str,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
        skip_children: bool = False,
    ) -> tuple[FullPageContent | None, list[str]]:
        """Core page processing logic (called inside semaphore)."""
        page = await self.get_page_full(page_id)
        if not page:
            logger.warning(
                "Page %s fetch failed (see error log above), "
                "continuing with children",
                page_id,
            )
            child_ids = await self.get_child_pages(page_id)
            return None, child_ids

        self._validate_page_content(page, page_id)
        self.all_pages.append(page)
        self._total_pages_crawled += 1

        # Periodic checkpoint + incremental save
        self._pages_since_checkpoint += 1
        if self._pages_since_checkpoint >= self.CHECKPOINT_INTERVAL:
            self.save_checkpoint(source_key)
            self.save_incremental(source_key)
            self._pages_since_checkpoint = 0

        if progress and task_id:
            progress.update(
                task_id,
                description=f"({len(self.all_pages)}) {page.title[:40]}...",
            )

        if download_attachments and not self.shutdown_requested:
            await self._download_page_attachments(
                page, page_id, max_attachments_per_page,
            )

        # Child page IDs (skipped in flat mode)
        child_ids = (
            [] if skip_children else await self.get_child_pages(page_id)
        )
        return page, child_ids

    def _should_stop_crawl(self, max_pages: int | None) -> bool:
        """Return True when crawling should stop (shutdown or page limit)."""
        return self.shutdown_requested or (
            bool(max_pages) and self._total_pages_crawled >= max_pages  # type: ignore[operator]
        )

    async def _crawl_children_parallel(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl children concurrently via asyncio.gather."""
        tasks: list[asyncio.Task[FullPageContent | None]] = []
        for child_id in child_ids:
            if self._should_stop_crawl(max_pages):
                break
            tasks.append(
                asyncio.ensure_future(
                    self.crawl_recursive(
                        child_id,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_pages=max_pages,
                        download_attachments=download_attachments,
                        max_attachments_per_page=max_attachments_per_page,
                        progress=progress,
                        task_id=task_id,
                        source_key=source_key,
                    )
                )
            )

        if not tasks:
            return

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                logger.warning("Child page crawl error: %s", r)

    async def _crawl_children_sequential(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl children one at a time."""
        for child_id in child_ids:
            if self._should_stop_crawl(max_pages):
                break
            await self.crawl_recursive(
                child_id,
                depth=depth + 1,
                max_depth=max_depth,
                max_pages=max_pages,
                download_attachments=download_attachments,
                max_attachments_per_page=max_attachments_per_page,
                progress=progress,
                task_id=task_id,
                source_key=source_key,
            )

    async def _crawl_children(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl child pages (parallel or sequential).

        When ``_max_concurrent > 1``, uses ``asyncio.gather`` for parallelism.
        The semaphore (``_page_sem``) controls overall concurrent HTTP calls,
        so nested gather calls remain safe for the Confluence server.
        """
        if not child_ids or self.shutdown_requested:
            return

        args = (
            child_ids, depth, max_depth, max_pages,
            download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )
        if self._max_concurrent > 1:
            await self._crawl_children_parallel(*args)
        else:
            await self._crawl_children_sequential(*args)

    async def crawl_flat(
        self,
        page_ids: list[str],
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        max_pages: int | None = None,
        progress: Any | None = None,
        task_id: Any = None,
        source_key: str = "unknown",
    ) -> None:
        """Crawl a flat list of page IDs (no recursive descent)."""
        total = len(page_ids)
        for i, page_id in enumerate(page_ids):
            if self.shutdown_requested:
                break
            if max_pages and self._total_pages_crawled >= max_pages:
                break
            if page_id in self.visited_pages:
                continue

            self.visited_pages.add(page_id)

            await self._process_single_page(
                page_id,
                download_attachments,
                max_attachments_per_page,
                progress,
                task_id,
                source_key,
                skip_children=True,
            )

            if progress and task_id and (i + 1) % 50 == 0:
                progress.update(
                    task_id,
                    description=(
                        f"flat crawl: {self._total_pages_crawled}/{total}"
                    ),
                )
