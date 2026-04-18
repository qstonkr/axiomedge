# pyright: reportAttributeAccessIssue=false
"""Confluence attachment download and content extraction.

Provides :class:`AttachmentMixin` with methods for fetching attachment
metadata, downloading files, and dispatching to format-specific parsers.
Extracted from ``client.py`` for SRP.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .attachment_parser import AttachmentParser
from .models import (
    AttachmentContent,
    AttachmentParseResult,
)

logger = logging.getLogger(__name__)


class AttachmentMixin:
    """Attachment handling mixed into :class:`ConfluenceFullClient`.

    Host class must provide: ``base_url``, ``client`` (:class:`httpx.AsyncClient`),
    ``attachments_dir``, ``_http_get_with_retry``, ``_record_attachment_stats``.
    """

    # ------------------------------------------------------------------
    # Attachment metadata
    # ------------------------------------------------------------------

    async def get_attachments(self, page_id: str) -> list[dict]:
        """Fetch attachment metadata for a page."""
        url = f"{self.base_url}/rest/api/content/{page_id}/child/attachment"
        params = {"limit": 100}

        try:
            response = await self._http_get_with_retry(url, params=params)
            return response.json().get("results", [])
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "Could not fetch attachments of %s: %s", page_id, e
            )
            return []

    # ------------------------------------------------------------------
    # Attachment content parsing
    # ------------------------------------------------------------------

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
            return AttachmentMixin._decode_text_attachment(content, 1.0)

        if filename_lower.endswith(".csv"):
            return AttachmentMixin._decode_text_attachment(content, 0.95)

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
        """Decode a text/CSV attachment with UTF-8 -> CP949 fallback."""
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

    # ------------------------------------------------------------------
    # Attachment download
    # ------------------------------------------------------------------

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

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            result.parse_error = str(e)

        self._record_attachment_stats(result)
        return result
