"""Attachment content extraction for Confluence crawled files.

Handles PDF, Excel, Word (.doc/.docx), PowerPoint (.ppt/.pptx), and image files.
Uses PaddleOCR for image-based content extraction with subprocess isolation to
defend against SIGSEGV crashes in PaddleOCR's C++ inference engine.

Originally extracted from ``scripts/confluence_full_crawler.py``.

Implementation is split across mixin modules:
- ``_pdf_parser.py``   -- PDF text extraction + OCR fallback
- ``_excel_parser.py`` -- Excel sheet/table extraction
- ``_word_parser.py``  -- Word (.doc/.docx) text extraction
- ``_ppt_parser.py``   -- PPT slide extraction + OCR pipeline
- ``_ppt_ocr.py``      -- PPT OCR sub-operations (render, shape, retry)
- ``_ocr_manager.py``  -- OCR singleton, slide OCR, image parsing
"""

from __future__ import annotations

import logging
from typing import Any

from .models import AttachmentOCRPolicy
from ._attachment_helpers import (  # noqa: E402
    _DEFAULT_ATTACHMENT_OCR_MODE,
    _DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT,
    _DEFAULT_OCR_MAX_PDF_PAGES,
    _DEFAULT_OCR_MAX_PPT_SLIDES,
    _DEFAULT_OCR_MIN_TEXT_CHARS,
    _SOURCE_ATTACHMENT_OCR_DEFAULTS,
    _env_bool,
    _env_int,  # noqa: F401 -- re-exported for test backward compat
    _filter_ocr_noise,  # noqa: F401 -- re-exported
    _resolve_bool_field,
    _resolve_int_field,
    _resolve_ocr_mode,
    _should_ocr_ppt,  # noqa: F401 -- re-exported
    _try_cli_doc_extract,  # noqa: F401 -- re-exported
)
from ._pdf_parser import _PdfParserMixin
from ._excel_parser import _ExcelParserMixin
from ._word_parser import _WordParserMixin
from ._ppt_parser import _PptParserMixin
from ._ocr_manager import _OcrManagerMixin

logger = logging.getLogger(__name__)


# =============================================================================
# Attachment Parser -- composed from mixins
# =============================================================================
class AttachmentParser(
    _PdfParserMixin,
    _ExcelParserMixin,
    _WordParserMixin,
    _PptParserMixin,
    _OcrManagerMixin,
):
    """첨부파일 내용 추출기

    Delegates format-specific parsing to mixin classes:
    - PDF:   _PdfParserMixin
    - Excel: _ExcelParserMixin
    - Word:  _WordParserMixin
    - PPT:   _PptParserMixin  (+ _PptOcrMixin)
    - OCR/Image: _OcrManagerMixin
    """

    _active_source_key = ""
    _ocr_policy = AttachmentOCRPolicy(
        attachment_ocr_mode=_DEFAULT_ATTACHMENT_OCR_MODE,
        ocr_min_text_chars=_DEFAULT_OCR_MIN_TEXT_CHARS,
        ocr_max_pdf_pages=_DEFAULT_OCR_MAX_PDF_PAGES,
        ocr_max_ppt_slides=_DEFAULT_OCR_MAX_PPT_SLIDES,
        ocr_max_images_per_attachment=_DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT,
        slide_render_enabled=_env_bool(
            "KNOWLEDGE_SLIDE_RENDER_ENABLED", True,
        ),
        layout_analysis_enabled=_env_bool(
            "KNOWLEDGE_LAYOUT_ANALYSIS_ENABLED", True,
        ),
    )

    @classmethod
    def configure_run(
        cls, source_key: str,
        overrides: dict[str, Any] | None = None,
    ) -> AttachmentOCRPolicy:
        """Resolve run-local OCR policy once per crawl source."""
        overrides = overrides or {}
        source_defaults = _SOURCE_ATTACHMENT_OCR_DEFAULTS.get(
            source_key, {},
        )
        legacy_slide_render = _env_bool(
            "KNOWLEDGE_SLIDE_RENDER_ENABLED", True,
        )
        legacy_layout_analysis = _env_bool(
            "KNOWLEDGE_LAYOUT_ANALYSIS_ENABLED", True,
        )

        cls._active_source_key = source_key
        cls._ocr_policy = AttachmentOCRPolicy(
            attachment_ocr_mode=_resolve_ocr_mode(
                overrides, source_defaults,
            ),
            ocr_min_text_chars=_resolve_int_field(
                overrides, source_defaults,
                "ocr_min_text_chars",
                "KNOWLEDGE_CRAWL_OCR_MIN_TEXT_CHARS",
                _DEFAULT_OCR_MIN_TEXT_CHARS,
            ),
            ocr_max_pdf_pages=_resolve_int_field(
                overrides, source_defaults,
                "ocr_max_pdf_pages",
                "KNOWLEDGE_CRAWL_OCR_MAX_PDF_PAGES",
                _DEFAULT_OCR_MAX_PDF_PAGES,
            ),
            ocr_max_ppt_slides=_resolve_int_field(
                overrides, source_defaults,
                "ocr_max_ppt_slides",
                "KNOWLEDGE_CRAWL_OCR_MAX_PPT_SLIDES",
                _DEFAULT_OCR_MAX_PPT_SLIDES,
            ),
            ocr_max_images_per_attachment=_resolve_int_field(
                overrides, source_defaults,
                "ocr_max_images_per_attachment",
                "KNOWLEDGE_CRAWL_OCR_MAX_IMAGES_PER_ATTACHMENT",
                _DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT,
            ),
            slide_render_enabled=_resolve_bool_field(
                overrides, source_defaults,
                "slide_render_enabled",
                "KNOWLEDGE_CRAWL_SLIDE_RENDER_ENABLED",
                legacy_slide_render,
            ),
            layout_analysis_enabled=_resolve_bool_field(
                overrides, source_defaults,
                "layout_analysis_enabled",
                "KNOWLEDGE_CRAWL_LAYOUT_ANALYSIS_ENABLED",
                legacy_layout_analysis,
            ),
        )
        return cls._ocr_policy

    @classmethod
    def current_policy(cls) -> AttachmentOCRPolicy:
        return cls._ocr_policy

    @staticmethod
    def _emit_status(heartbeat_fn, message: str) -> None:
        if heartbeat_fn:
            heartbeat_fn(message)
        else:
            logger.debug("[status] %s", message)

    @staticmethod
    def _text_chars(value: str | None) -> int:
        return (
            len(value.strip()) if value and value.strip() else 0
        )
