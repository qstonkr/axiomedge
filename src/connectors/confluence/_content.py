# pyright: reportAttributeAccessIssue=false
"""Confluence page content extraction and page processing orchestration.

Extracted from ``client.py`` for SRP.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
from typing import Any

import httpx

from .models import AttachmentContent
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
    ExtractedComment,
    ExtractedLabel,
    ExtractedRestriction,
    FullPageContent,
)
from .structured_ir import extract_creator_info, generate_structured_ir

logger = logging.getLogger(__name__)


class ContentMixin:
    """Page content fetching & metadata extraction.

    Host class must provide: ``base_url``, ``client`` (:class:`httpx.AsyncClient`),
    ``_http_get_with_retry``.
    """

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
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

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Could not fetch labels of %s: %s", page_id, e)

        return labels

    # ------------------------------------------------------------------
    # Content extraction helpers
    # ------------------------------------------------------------------

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

        email_extractor = EmailExtractor()
        try:
            email_extractor.feed(body_html)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

        macro_extractor = MacroExtractor()
        try:
            macro_extractor.feed(body_html)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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

    # ------------------------------------------------------------------
    # Full page fetch
    # ------------------------------------------------------------------

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
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error(
                "Page %s ERROR (%s): %s", page_id, type(e).__name__, e
            )
            return None

    # ------------------------------------------------------------------
    # Page processing orchestration
    # ------------------------------------------------------------------

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
            # PR-5 (B) — fetch 실패한 page 는 visited 와 별도로 추적해 다음
            # run / --retry-confluence-failed 에서 재시도 가능하게 함.
            try:
                self.failed_pages.add(page_id)
            except AttributeError:
                # legacy host class — failed_pages 미정의 시 no-op
                pass
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

