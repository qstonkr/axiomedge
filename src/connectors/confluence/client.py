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
        self.client = httpx.AsyncClient(
            timeout=_timeout, verify=False, headers=self.headers
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
                if attempt < max_retries - 1:
                    wait = min(2**attempt * 2, 30)
                    logger.warning(
                        "HTTP retry %d/%d (%s): %s",
                        attempt + 1,
                        max_retries,
                        type(e).__name__,
                        url[:80],
                    )
                    await asyncio.sleep(wait)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 500, 502, 503, 504):
                    last_error = e
                    if attempt < max_retries - 1:
                        wait = min(2**attempt * 5, 60)
                        logger.warning(
                            "HTTP %d retry %d/%d: %s",
                            e.response.status_code,
                            attempt + 1,
                            max_retries,
                            url[:80],
                        )
                        await asyncio.sleep(wait)
                else:
                    raise
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
            body_html = (
                data.get("body", {}).get("storage", {}).get("value", "")
            )

            # Plain text extraction
            text_extractor = PlainTextExtractor()
            text_extractor.feed(body_html)
            content_text = text_extractor.get_text()

            # Table extraction
            table_extractor = TableExtractor()
            table_extractor.feed(body_html)
            tables = table_extractor.tables

            # Mention extraction
            mention_extractor = MentionExtractor()
            mention_extractor.feed(body_html)
            mentions = mention_extractor.mentions

            # Section extraction
            section_extractor = SectionExtractor()
            section_extractor.feed(body_html)
            sections = section_extractor.sections

            # Code block extraction
            code_extractor = CodeBlockExtractor()
            try:
                code_extractor.feed(body_html)
            except Exception:
                pass
            code_blocks = code_extractor.code_blocks

            # Email link extraction
            email_extractor = EmailExtractor()
            try:
                email_extractor.feed(body_html)
            except Exception:
                pass
            emails = email_extractor.emails

            # Macro extraction
            macro_extractor = MacroExtractor()
            try:
                macro_extractor.feed(body_html)
            except Exception:
                pass
            macros = macro_extractor.macros

            # Structured IR generation (RAG optimized)
            content_ir = generate_structured_ir(
                content_text=content_text,
                content_html=body_html,
                title=title,
                tables=tables,
                sections=sections,
                mentions=mentions,
            )

            # Metadata
            history = data.get("history", {})
            created_by_data = history.get("createdBy", {})
            creator = created_by_data.get("displayName", "Unknown")
            creator_account_id = created_by_data.get("accountId")
            creator_name, creator_team = extract_creator_info(creator)
            created_at = history.get("createdDate", "")

            # Creator email lookup
            creator_email = None
            if creator_account_id:
                user_details = await self.get_user_details(creator_account_id)
                if user_details:
                    creator_email = user_details.get("email")

            last_updated = history.get("lastUpdated", {})
            last_modifier = last_updated.get("by", {}).get(
                "displayName", creator
            )
            updated_at = last_updated.get("when", created_at)

            version = data.get("version", {}).get("number", 1)
            page_url = (
                f"{self.base_url}/pages/viewpage.action?pageId={page_id}"
            )

            # Space info
            space_key = data.get("space", {}).get("key")

            # Ancestors
            ancestors = [
                {"id": a.get("id"), "title": a.get("title")}
                for a in data.get("ancestors", [])
            ]

            # Labels
            labels = await self.get_labels(page_id)

            # Comments
            comments = await self.get_comments(page_id)

            # Internal/external links
            link_extractor = LinkExtractor(base_url=self.base_url)
            try:
                link_extractor.feed(body_html)
            except Exception:
                pass
            internal_links = link_extractor.internal_links
            external_links = link_extractor.external_links

            # Restrictions
            restrictions: list[ExtractedRestriction] = []
            restrictions_data = data.get("restrictions", {})

            read_restrictions = restrictions_data.get("read", {}).get(
                "restrictions", {}
            )
            for user in read_restrictions.get("user", {}).get("results", []):
                restrictions.append(
                    ExtractedRestriction(
                        operation="read",
                        restriction_type="user",
                        name=user.get("displayName", ""),
                        account_id=user.get("accountId"),
                    )
                )
            for group in read_restrictions.get("group", {}).get("results", []):
                restrictions.append(
                    ExtractedRestriction(
                        operation="read",
                        restriction_type="group",
                        name=group.get("name", ""),
                    )
                )

            update_restrictions = restrictions_data.get("update", {}).get(
                "restrictions", {}
            )
            for user in update_restrictions.get("user", {}).get("results", []):
                restrictions.append(
                    ExtractedRestriction(
                        operation="update",
                        restriction_type="user",
                        name=user.get("displayName", ""),
                        account_id=user.get("accountId"),
                    )
                )
            for group in update_restrictions.get("group", {}).get(
                "results", []
            ):
                restrictions.append(
                    ExtractedRestriction(
                        operation="update",
                        restriction_type="group",
                        name=group.get("name", ""),
                    )
                )

            # Version history snapshot
            version_data = data.get("version", {})
            current_version_snapshot = {
                "number": version_data.get("number", version),
                "when": version_data.get("when", updated_at),
                "by": version_data.get("by", {}).get(
                    "displayName", last_modifier
                ),
                "message": version_data.get("message", ""),
            }
            version_history = [current_version_snapshot]

            # Enrich mentions with email
            for mention in mentions:
                if mention.user_id:
                    user_details = await self.get_user_details(mention.user_id)
                    if user_details:
                        mention.email = user_details.get("email")
                        if not mention.display_name:
                            mention.display_name = user_details.get(
                                "display_name"
                            )

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
                content_ir=content_ir,
                tables=tables,
                mentions=mentions,
                sections=sections,
                code_blocks=code_blocks,
                creator=creator,
                creator_name=creator_name,
                creator_team=creator_team,
                creator_email=creator_email,
                last_modifier=last_modifier,
                version=version,
                url=page_url,
                created_at=created_at,
                updated_at=updated_at,
                labels=labels,
                comments=comments,
                emails=emails,
                macros=macros,
                space_key=space_key,
                ancestors=ancestors,
                internal_links=internal_links,
                external_links=external_links,
                restrictions=restrictions,
                version_history=version_history,
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
            def status_emit(message: str) -> None:
                logger.info("[attachment] %s", message)

            # Save file
            safe_filename = re.sub(r"[^\w\-_\. ]", "_", filename)
            file_path = self.attachments_dir / f"{page_id}_{safe_filename}"
            with open(file_path, "wb") as f:
                f.write(content)
            result.download_path = str(file_path)

            # Content extraction by type
            media_lower = media_type.lower()
            filename_lower = filename.lower()
            parse_result: AttachmentParseResult | None = None

            if "pdf" in media_lower or filename_lower.endswith(".pdf"):
                parse_result = AttachmentParser.parse_pdf(
                    file_path, heartbeat_fn=status_emit
                )

            elif any(
                x in media_lower
                for x in ["spreadsheet", "excel", "xlsx", "xls"]
            ) or any(
                filename_lower.endswith(ext)
                for ext in [".xlsx", ".xls", ".xlsm"]
            ):
                parse_result = AttachmentParser.parse_excel(file_path)

            elif any(
                x in media_lower
                for x in ["presentation", "powerpoint", "pptx", "ppt"]
            ) or any(
                filename_lower.endswith(ext) for ext in [".pptx", ".ppt"]
            ):
                parse_result = AttachmentParser.parse_ppt(
                    file_path, heartbeat_fn=status_emit
                )

            elif any(
                x in media_lower for x in ["word", "docx", "doc"]
            ) or any(
                filename_lower.endswith(ext) for ext in [".docx", ".doc"]
            ):
                parse_result = AttachmentParser.parse_word(file_path)

            elif "image" in media_lower or any(
                filename_lower.endswith(ext)
                for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]
            ):
                parse_result = await AttachmentParser.parse_image_async(
                    file_path, content
                )

            elif filename_lower.endswith(".txt") or "text" in media_lower:
                try:
                    text = content.decode("utf-8")
                    parse_result = AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=1.0,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )
                except UnicodeDecodeError:
                    text = content.decode("cp949", errors="replace")
                    parse_result = AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.8,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )

            elif filename_lower.endswith(".csv"):
                try:
                    text = content.decode("utf-8")
                    parse_result = AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.95,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )
                except UnicodeDecodeError:
                    text = content.decode("cp949", errors="replace")
                    parse_result = AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.8,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )

            else:
                parse_result = AttachmentParseResult(
                    extracted_text=f"[Unsupported format: {media_type}]",
                    extracted_tables=[],
                    confidence=0.0,
                    ocr_skip_reason="unsupported_media_type",
                )

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

    async def get_all_descendant_page_ids_via_cql(
        self, root_page_id: str
    ) -> set[str]:
        """Collect all descendant page IDs under *root_page_id* using CQL."""
        url = f"{self.base_url}/rest/api/content/search"
        cql = f"ancestor={root_page_id} and type=page"
        start = 0
        limit = 100
        all_ids: set[str] = set()

        while True:
            if self.shutdown_requested:
                break
            params = {"cql": cql, "limit": limit, "start": start}
            try:
                resp = await self._http_get_with_retry(url, params=params)
                data = resp.json()
            except Exception as e:
                logger.warning("CQL search error (start=%d): %s", start, e)
                break

            results = data.get("results", [])
            if not results:
                break

            for r in results:
                pid = r.get("id")
                if pid:
                    all_ids.add(str(pid))

            total_size = data.get("totalSize", 0)
            start += limit

            if start % 1000 == 0 or not results:
                logger.info("CQL enumeration: %d/%d pages", len(all_ids), total_size)

            if start >= total_size:
                break

        logger.info("CQL enumeration complete: %d pages", len(all_ids))
        return all_ids

    # ------------------------------------------------------------------
    # Crawl strategies
    # ------------------------------------------------------------------

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
            if depth <= max_depth:
                try:
                    child_ids = await self.get_child_pages(page_id)
                    if child_ids:
                        await self._crawl_children(
                            child_ids,
                            depth,
                            max_depth,
                            max_pages,
                            download_attachments,
                            max_attachments_per_page,
                            progress,
                            task_id,
                            source_key,
                        )
                except Exception:
                    pass
            return None
        self.visited_pages.add(page_id)

        if max_pages and self._total_pages_crawled >= max_pages:
            return None

        if depth > max_depth:
            return None

        # Phase 1: process page (semaphore-protected HTTP)
        page, child_ids = await self._process_single_page(
            page_id,
            download_attachments,
            max_attachments_per_page,
            progress,
            task_id,
            source_key,
        )

        # Phase 2: crawl children (outside semaphore)
        await self._crawl_children(
            child_ids,
            depth,
            max_depth,
            max_pages,
            download_attachments,
            max_attachments_per_page,
            progress,
            task_id,
            source_key,
        )

        return page

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

                if self.shutdown_requested:
                    queue.task_done()
                    break
                # Check-and-add atomically (safe in asyncio single-thread)
                if page_id in self.visited_pages:
                    queue.task_done()
                    continue
                self.visited_pages.add(page_id)

                if max_pages and self._total_pages_crawled >= max_pages:
                    queue.task_done()
                    break
                if depth > max_depth:
                    queue.task_done()
                    continue

                page, child_ids = await self._process_single_page(
                    page_id,
                    download_attachments,
                    max_attachments_per_page,
                    None,
                    None,
                    source_key,
                )

                # Enqueue children
                for child_id in child_ids:
                    if child_id not in self.visited_pages:
                        await queue.put((child_id, depth + 1))

                # Checkpoint is handled inside _do_process_page
                queue.task_done()

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

        # Per-page content validation
        text_len = len(page.content_text) if page.content_text else 0
        html_len = len(page.content_html) if page.content_html else 0
        if text_len == 0:
            if html_len == 0:
                logger.warning(
                    "Page %s (%s) has empty body (html=0, text=0) "
                    "-- possible permission issue",
                    page_id,
                    page.title[:50],
                )
            else:
                logger.warning(
                    "Page %s (%s) has HTML (%d chars) but extracted text "
                    "is empty",
                    page_id,
                    page.title[:50],
                    html_len,
                )

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

        # Attachment processing (with shutdown check, parallel download+OCR)
        if download_attachments and not self.shutdown_requested:
            attachments_meta = await self.get_attachments(page_id)
            target_attachments = attachments_meta[:max_attachments_per_page]

            if target_attachments:
                _att_sem = asyncio.Semaphore(2)

                async def _dl_one(
                    meta: dict,
                ) -> AttachmentContent | None:
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
                        att_name = target_attachments[i].get(
                            "title", "unknown"
                        )
                        logger.warning(
                            "Attachment download failed (%s): %s",
                            att_name,
                            r,
                        )
                page.attachments = [
                    r
                    for r in results
                    if r is not None
                    and not isinstance(r, (Exception, BaseException))
                ]

                image_count = sum(
                    1
                    for m in target_attachments
                    if "image"
                    in m.get("extensions", {})
                    .get("mediaType", "")
                    .lower()
                )
                if image_count > 0:
                    gc.collect()

        # Child page IDs (skipped in flat mode)
        child_ids = (
            [] if skip_children else await self.get_child_pages(page_id)
        )
        return page, child_ids

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

        if self._max_concurrent > 1:
            # Parallel mode
            tasks: list[asyncio.Task[FullPageContent | None]] = []
            for child_id in child_ids:
                if self.shutdown_requested:
                    break
                if max_pages and self._total_pages_crawled >= max_pages:
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
                if isinstance(r, Exception) and not isinstance(
                    r, asyncio.CancelledError
                ):
                    logger.warning("Child page crawl error: %s", r)

        else:
            # Sequential mode
            for child_id in child_ids:
                if self.shutdown_requested:
                    break
                if max_pages and self._total_pages_crawled >= max_pages:
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
