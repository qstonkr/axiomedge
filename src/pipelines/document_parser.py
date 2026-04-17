"""Document parser for PDF, DOCX, PPTX, XLSX, and TXT files.

Facade module -- delegates to sub-modules and re-exports all symbols
for backward compatibility.

Sub-modules:
    _parser_utils.py  -- ParseResult, table/text/docx/xlsx/OCR helpers
    _pdf_parser.py    -- PDF parsing (pymupdf) + CMap/garble detection
    _pptx_parser.py   -- PPTX parsing (python-pptx) + image extraction
"""

from __future__ import annotations

import logging
from pathlib import Path

# -- Re-exports from sub-modules (backward compatibility) ---------------------
from ._parser_utils import (  # noqa: F401
    MAX_FILE_SIZE,
    ParseResult,
    _convert_ppt_to_pptx,
    _extract_table_data,
    _extract_text_fallback,
    _extract_text_from_boxes,
    _extract_vision_analysis,
    _format_docx_paragraph,
    _ocr_request,
    _parse_docx,
    _parse_text,
    _parse_xlsx,
    _prepare_image_bytes,
    _process_images_ocr,
    _process_single_image_ocr,
    _resize_image,
    _table_to_markdown,
)
from ._pdf_parser import (  # noqa: F401
    _check_font_broken_cmap,
    _classify_pdf_page,
    _extract_page_images,
    _extract_page_tables,
    _extract_pdf_date,
    _extract_pdf_page_heading,
    _get_embedded_font_size,
    _has_broken_cmap_fonts,
    _is_garbled_text,
    _parse_pdf,
    _parse_pdf_enhanced,
)
from ._pptx_parser import (  # noqa: F401
    _extract_pptx_modified_date,
    _extract_slide_text,
    _extract_slide_title,
    _iter_pptx_shapes,
    _parse_pptx,
    _parse_pptx_enhanced,
    _process_enhanced_slide,
    _process_pptx_shape,
)

_EXT_PPTX = ".pptx"

logger = logging.getLogger(__name__)


def _parse_image(data: bytes, _filename: str) -> ParseResult:
    """Parse image file through OCR/CV pipeline."""
    ocr_text, visual_analyses = _process_images_ocr([data])
    return ParseResult(
        ocr_text=ocr_text,
        images_processed=1,
        visual_analyses=visual_analyses,
    )


def parse_file(filepath: str | Path) -> str:
    """Parse a local file and return its text content.

    Supports: PDF, DOCX, PPTX, XLSX, TXT/MD/CSV/JSON/XML/YAML.
    Returns empty string for unsupported types or on errors.
    """
    path = Path(filepath)
    if not path.exists() or not path.is_file():
        logger.warning("File not found: %s", path)
        return ""
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / 1e6
        raise ValueError(
            f"File too large: {file_size / 1e6:.0f}MB"
            f" (max {max_mb:.0f}MB)"
        )
    data = path.read_bytes()
    return parse_bytes(data, path.name)


def parse_file_enhanced(filepath: str | Path) -> ParseResult:
    """Parse a local file with enhanced output including tables and OCR.

    Returns a ParseResult with text, tables, OCR text, and visual analyses.
    """
    path = Path(filepath)
    if not path.exists() or not path.is_file():
        logger.warning("File not found: %s", path)
        return ParseResult()
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / 1e6
        raise ValueError(
            f"File too large: {file_size / 1e6:.0f}MB"
            f" (max {max_mb:.0f}MB)"
        )
    data = path.read_bytes()
    return parse_bytes_enhanced(data, path.name)


def parse_bytes(data: bytes, filename: str) -> str:
    """Parse file bytes and return plain text content."""
    if not data:
        raise ValueError(f"Empty file: {filename}")

    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(data, filename)
    elif ext == ".docx":
        return _parse_docx(data, filename)
    elif ext == ".doc":
        logger.warning(
            "Legacy .doc format is not supported (use .docx): %s",
            filename,
        )
        return ""
    elif ext == _EXT_PPTX:
        return _parse_pptx(data, filename)
    elif ext == ".ppt":
        converted = _convert_ppt_to_pptx(data, filename)
        if converted is None:
            return ""
        return _parse_pptx(converted, filename)
    elif ext in (".xlsx", ".xls", ".xlsm"):
        return _parse_xlsx(data, filename)
    elif ext in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml"):
        return _parse_text(data)
    else:
        logger.warning("Unsupported file type: %s", ext)
        return ""


def parse_bytes_enhanced(data: bytes, filename: str) -> ParseResult:
    """Parse file bytes with enhanced output."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf_enhanced(data, filename)
    elif ext == _EXT_PPTX:
        return _parse_pptx_enhanced(data, filename)
    elif ext == ".ppt":
        converted = _convert_ppt_to_pptx(data, filename)
        if converted is None:
            return ParseResult(
                text=(
                    f"[Error] Failed to convert .ppt file: {filename}."
                    " LibreOffice (soffice) is required."
                ),
            )
        return _parse_pptx_enhanced(converted, filename)
    elif ext in (
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff",
    ):
        return _parse_image(data, filename)
    else:
        # For other types, wrap plain text in ParseResult
        text = parse_bytes(data, filename)
        return ParseResult(text=text)
