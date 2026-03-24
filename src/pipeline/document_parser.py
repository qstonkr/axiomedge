"""Document parser for PDF, DOCX, PPTX, XLSX, and TXT files.

Extracted and simplified from oreo-ecosystem attachment_parser.py.
Enhanced with:
- PDF table extraction using PyMuPDF page.find_tables()
- Scanned PDF detection (pages with no extractable text)
- Image extraction from PDF (page.get_images() + doc.extract_image())
- Image extraction from PPTX (shape.image.blob for PICTURE shapes)
- OCR/CV Pipeline routing for extracted images
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Enhanced document parse result with text, tables, and OCR data."""

    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    ocr_text: str = ""  # text extracted via OCR from images/scanned pages
    images_processed: int = 0
    visual_analyses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Combined text from all sources."""
        parts = [self.text]
        if self.ocr_text:
            parts.append(f"\n[OCR Extracted Text]\n{self.ocr_text}")
        for table in self.tables:
            parts.append(f"\n[Table]\n{_table_to_markdown(table)}")
        return "\n".join(p for p in parts if p.strip())


def parse_file(filepath: str | Path) -> str:
    """Parse a local file and return its text content.

    Supports: PDF, DOCX, PPTX, XLSX, TXT/MD/CSV/JSON/XML/YAML.
    Returns empty string for unsupported types or on errors.
    """
    path = Path(filepath)
    if not path.exists() or not path.is_file():
        logger.warning("File not found: %s", path)
        return ""
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
        logger.warning("Legacy .doc format is not supported (use .docx): %s", filename)
        return ""
    elif ext == ".pptx":
        return _parse_pptx(data, filename)
    elif ext == ".ppt":
        logger.warning("Legacy .ppt format is not supported (use .pptx): %s", filename)
        return ""
    elif ext in (".xlsx", ".xls", ".xlsm"):
        return _parse_xlsx(data, filename)
    elif ext in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml"):
        return _parse_text(data)
    else:
        logger.warning("Unsupported file type: %s", ext)
        return ""


def parse_bytes_enhanced(data: bytes, filename: str) -> ParseResult:
    """Parse file bytes with enhanced output including tables, OCR, and images."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf_enhanced(data, filename)
    elif ext == ".pptx":
        return _parse_pptx_enhanced(data, filename)
    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"):
        return _parse_image(data, filename)
    else:
        # For other types, wrap plain text in ParseResult
        text = parse_bytes(data, filename)
        return ParseResult(text=text)


# ---------------------------------------------------------------------------
# Individual parsers
# ---------------------------------------------------------------------------


def _parse_pdf(data: bytes, filename: str) -> str:
    """Parse PDF using pymupdf (PyMuPDF)."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"PDF open failed (encrypted or corrupt?): {e}") from e
    texts = []
    for page_num, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            texts.append(f"[Page {page_num + 1}]\n{text}")
    doc.close()
    return "\n\n".join(texts)


def _parse_pdf_enhanced(data: bytes, filename: str) -> ParseResult:
    """Enhanced PDF parsing with table extraction, scanned page detection, and image OCR."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"PDF open failed (encrypted or corrupt?): {e}") from e
    texts = []
    tables: list[list[list[str]]] = []
    scanned_pages: list[int] = []
    extracted_images: list[bytes] = []

    for page_num, page in enumerate(doc):
        # Text extraction
        text = page.get_text()
        if text.strip():
            texts.append(f"[Page {page_num + 1}]\n{text}")
        else:
            # Scanned PDF detection: page has no extractable text
            scanned_pages.append(page_num + 1)

        # Table extraction using PyMuPDF find_tables()
        page_tables = page.find_tables()
        for table in page_tables:
            table_data = table.extract()
            if table_data:
                # Convert None cells to empty strings
                cleaned = [
                    [str(cell) if cell is not None else "" for cell in row]
                    for row in table_data
                ]
                tables.append(cleaned)

        # Image extraction from PDF pages
        image_list = page.get_images(full=True)
        for img_info in image_list:
            xref = img_info[0]
            img_data = doc.extract_image(xref)
            if img_data and img_data.get("image"):
                img_bytes = img_data["image"]
                # Only process reasonably sized images (> 1KB, < 10MB)
                if 1024 < len(img_bytes) < 10_000_000:
                    extracted_images.append(img_bytes)

    doc.close()

    # Process scanned pages via OCR
    ocr_texts = []
    if scanned_pages:
        logger.info("Detected %d scanned pages in %s: %s", len(scanned_pages), filename, scanned_pages)
        # Re-open and render scanned pages as images for OCR
        doc2 = pymupdf.open(stream=data, filetype="pdf")
        for page_num in scanned_pages:
            page = doc2[page_num - 1]
            # Render page at 200 DPI for OCR
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            extracted_images.append(img_bytes)
        doc2.close()

    # Route extracted images through OCR/CV pipeline
    visual_analyses = []
    if extracted_images:
        ocr_text, analyses = _process_images_ocr(extracted_images)
        if ocr_text:
            ocr_texts.append(ocr_text)
        visual_analyses = analyses

    return ParseResult(
        text="\n\n".join(texts),
        tables=tables,
        ocr_text="\n".join(ocr_texts),
        images_processed=len(extracted_images),
        visual_analyses=visual_analyses,
    )


def _parse_docx(data: bytes, filename: str) -> str:
    """Parse DOCX using python-docx."""
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"DOCX open failed (corrupt?): {e}") from e
    texts = []

    for para in doc.paragraphs:
        if para.text.strip():
            if para.style and para.style.name.startswith("Heading"):
                level = para.style.name[-1] if para.style.name[-1].isdigit() else "1"
                texts.append(f"{'#' * int(level)} {para.text}")
            else:
                texts.append(para.text)

    for table in doc.tables:
        table_data = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            table_data.append(row_data)
        if table_data:
            texts.append(_table_to_markdown(table_data))

    return "\n\n".join(texts)


def _parse_pptx(data: bytes, filename: str) -> str:
    """Parse PPTX using python-pptx."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"PPTX open failed (corrupt?): {e}") from e
    texts = []

    def _iter_shapes(shapes, _depth: int = 0):
        if _depth > 10:
            return
        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from _iter_shapes(shape.shapes, _depth + 1)

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_texts = [f"[Slide {slide_num}]"]
        for shape in _iter_shapes(slide.shapes):
            if hasattr(shape, "text") and shape.text.strip():
                slide_texts.append(shape.text)
            if shape.has_table:
                table_data = []
                for row in shape.table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_data.append(row_data)
                if table_data:
                    slide_texts.append(_table_to_markdown(table_data))
        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                slide_texts.append(f"[Notes] {notes_text}")
        if len(slide_texts) > 1:
            texts.append("\n".join(slide_texts))

    return "\n\n".join(texts)


def _parse_pptx_enhanced(data: bytes, filename: str) -> ParseResult:
    """Enhanced PPTX parsing with image extraction and OCR routing."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(io.BytesIO(data))
    texts = []
    tables: list[list[list[str]]] = []
    extracted_images: list[bytes] = []

    def _iter_shapes(shapes, _depth: int = 0):
        if _depth > 10:
            return
        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from _iter_shapes(shape.shapes, _depth + 1)

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_texts = [f"[Slide {slide_num}]"]
        for shape in _iter_shapes(slide.shapes):
            # Text frames
            if hasattr(shape, "text") and shape.text.strip():
                slide_texts.append(shape.text)

            # Tables
            if shape.has_table:
                table_data = []
                for row in shape.table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_data.append(row_data)
                if table_data:
                    tables.append(table_data)
                    slide_texts.append(_table_to_markdown(table_data))

            # Image extraction from PICTURE shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                img_bytes = shape.image.blob
                if img_bytes and 1024 < len(img_bytes) < 10_000_000:
                    extracted_images.append(img_bytes)

        # Slide notes
        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                slide_texts.append(f"[Notes] {notes_text}")

        if len(slide_texts) > 1:
            texts.append("\n".join(slide_texts))

    # Route extracted images through OCR/CV pipeline
    visual_analyses = []
    ocr_text = ""
    if extracted_images:
        ocr_text, visual_analyses = _process_images_ocr(extracted_images)

    return ParseResult(
        text="\n\n".join(texts),
        tables=tables,
        ocr_text=ocr_text,
        images_processed=len(extracted_images),
        visual_analyses=visual_analyses,
    )


def _parse_image(data: bytes, filename: str) -> ParseResult:
    """Parse image file through OCR/CV pipeline."""
    ocr_text, visual_analyses = _process_images_ocr([data])
    return ParseResult(
        ocr_text=ocr_text,
        images_processed=1,
        visual_analyses=visual_analyses,
    )


def _parse_xlsx(data: bytes, filename: str) -> str:
    """Parse XLSX using openpyxl."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    texts = []

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        texts.append(f"[Sheet: {sheet_name}]")
        sheet_data = []
        for row in sheet.iter_rows(values_only=True):
            row_data = [str(cell) if cell is not None else "" for cell in row]
            if any(cell.strip() for cell in row_data):
                sheet_data.append(row_data)
        if sheet_data:
            texts.append(_table_to_markdown(sheet_data))

    wb.close()
    return "\n\n".join(texts)


def _parse_text(data: bytes) -> str:
    """Parse text files with UTF-8 / EUC-KR fallback."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("euc-kr", errors="ignore")


# ---------------------------------------------------------------------------
# OCR / CV Pipeline routing
# ---------------------------------------------------------------------------


def _process_images_ocr(
    images: list[bytes],
) -> tuple[str, list[dict[str, Any]]]:
    """Route extracted images through OCR (synchronous).

    Uses PaddleOCR via OCRWithCoords for direct synchronous text extraction.
    This avoids asyncio.run() issues when called from an already-running event loop
    (e.g. FastAPI async context).

    Returns:
        (ocr_text, visual_analyses)
    """
    ocr_texts: list[str] = []
    visual_analyses: list[dict[str, Any]] = []

    for i, img_bytes in enumerate(images):
        # Use OCRWithCoords for synchronous OCR (no asyncio needed)
        try:
            from src.cv_pipeline.ocr_with_coords import OCRWithCoords

            ocr = OCRWithCoords()
            boxes = ocr.extract(img_bytes)
            if boxes:
                text = " ".join(b.text for b in boxes if b.text.strip())
                if text:
                    ocr_texts.append(f"[Image {i + 1} OCR] {text}")
                continue
        except ImportError:
            pass

        # Fallback: direct PaddleOCR via sync path
        try:
            from src.ocr.paddle_ocr_provider import PaddleOCRProvider

            provider = PaddleOCRProvider()
            # Use the sync _parse_result + engine directly to avoid async
            engine = provider._ocr  # May be None if not initialized
            if engine is None:
                try:
                    from paddleocr import PaddleOCR  # type: ignore[import-not-found]

                    engine = PaddleOCR(lang="korean", use_gpu=False, use_angle_cls=True)
                except (ImportError, TypeError):
                    logger.debug("PaddleOCR not available for image %d", i + 1)
                    continue

            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as f:
                f.write(img_bytes)
                f.flush()
                raw = engine.ocr(f.name, cls=True)
            text, _confidence = provider._parse_result(raw)
            if text.strip():
                ocr_texts.append(f"[Image {i + 1} OCR] {text}")
        except ImportError:
            logger.debug("Neither OCRWithCoords nor PaddleOCR available for image %d", i + 1)

    return "\n".join(ocr_texts), visual_analyses


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_to_markdown(data: list[list[str]], max_rows: int = 50) -> str:
    """Convert table data to markdown format."""
    if not data:
        return ""
    lines = []
    header = data[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in data[1:max_rows]:
        row = row + [""] * (len(header) - len(row))
        row = row[: len(header)]
        lines.append("| " + " | ".join(row) + " |")
    if len(data) > max_rows:
        lines.append(f"... ({len(data) - max_rows} rows omitted)")
    return "\n".join(lines)
