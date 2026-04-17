"""Document parser -- PDF parsing functions.

Extracted from document_parser.py for SRP.
"""

from __future__ import annotations

import logging

from . import _parser_utils
from ._parser_utils import ParseResult

logger = logging.getLogger(__name__)


def _parse_pdf(data: bytes, _filename: str) -> str:
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


def _extract_pdf_date(raw: str) -> str:
    """Parse PDF date format (D:YYYYMMDDHHmmSS+TZ) to ISO 8601."""
    if not raw:
        return ""
    import re as _re
    m = _re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}"
    return ""


def _extract_page_tables(page, tables: list) -> None:
    """Extract tables from a PDF page."""
    for table in page.find_tables():
        table_data = table.extract()
        if table_data:
            cleaned = [
                [str(cell) if cell is not None else "" for cell in row]
                for row in table_data
            ]
            tables.append(cleaned)


def _extract_page_images(page, doc, extracted_images: list) -> None:
    """Extract images from a PDF page."""
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        img_data = doc.extract_image(xref)
        if img_data and img_data.get("image"):
            img_bytes = img_data["image"]
            if 1024 < len(img_bytes) < 10_000_000:
                extracted_images.append(img_bytes)


def _extract_pdf_page_heading(page) -> str:
    """PDF 페이지에서 폰트 크기 기반으로 heading(제목) 추출.

    페이지 상단 20% 영역에서 가장 큰 폰트의 텍스트를 heading으로 판단.
    """
    try:
        blocks = page.get_text("dict", flags=0)["blocks"]
        if not blocks:
            return ""
        page_height = page.rect.height
        heading_zone = page_height * 0.2  # 상단 20%

        candidates = []
        for block in blocks:
            if block.get("type") != 0:  # text block만
                continue
            for line in block.get("lines", []):
                # 상단 영역만
                if line["bbox"][1] > heading_zone:
                    continue
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    size = span.get("size", 0)
                    flags = span.get("flags", 0)
                    is_bold = bool(flags & 2**4)  # bit 4 = bold
                    if text and len(text) >= 2:
                        # 볼드이면 크기 보너스
                        effective_size = size * 1.2 if is_bold else size
                        candidates.append((effective_size, text))

        if not candidates:
            return ""
        # 가장 큰 폰트 텍스트
        candidates.sort(key=lambda x: -x[0])
        best_size, best_text = candidates[0]
        # 본문 크기(보통 10~12pt) 대비 충분히 커야 heading
        if best_size < 13:
            return ""
        return best_text[:100]
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return ""


def _classify_pdf_page(
    page, page_num: int, filename: str, doc,
    has_broken_cmap_fn, is_garbled_fn,
    texts: list, scanned_pages: list, tables: list, extracted_images: list,
) -> None:
    """Classify a single PDF page and extract text/tables/images."""
    has_broken_fonts = has_broken_cmap_fn(page)
    text = page.get_text()
    page_label = page_num + 1

    if has_broken_fonts or (text.strip() and is_garbled_fn(text)):
        scanned_pages.append(page_label)
        if has_broken_fonts:
            logger.info("Broken CMap font on page %d of %s, routing to OCR", page_label, filename)
        elif text.strip():
            logger.info("Garbled text on page %d of %s, routing to OCR", page_label, filename)
    elif text.strip():
        # PDF heading 추출 — 폰트 크기 기반
        heading = _extract_pdf_page_heading(page)
        if heading:
            texts.append(f"## {heading}\n\n[Page {page_label}]\n{text}")
        else:
            texts.append(f"[Page {page_label}]\n{text}")
    else:
        scanned_pages.append(page_label)

    _extract_page_tables(page, tables)
    _extract_page_images(page, doc, extracted_images)


def _has_broken_cmap_fonts(page, doc) -> bool:
    """Detect fonts with broken ToUnicode CMaps (common in PowerPoint PDF exports).

    Type0/Identity-H fonts with large embedded TrueType but very few Unicode
    mappings indicate stripped CMap tables -- all Korean chars map to a single
    character (e.g. 폐 or 손).
    """
    import re as _re
    for xref, _ext, ftype, _basefont, _name, encoding in page.get_fonts():
        if ftype != "Type0" or encoding != "Identity-H":
            continue
        if _check_font_broken_cmap(doc, xref, _re):
            return True
    return False


def _check_font_broken_cmap(doc, xref: int, _re) -> bool:
    """Check if a single Type0 font has a broken CMap."""
    try:
        obj = doc.xref_object(xref)
        tounicode_match = _re.search(r"/ToUnicode (\d+) 0 R", obj)
        if not tounicode_match:
            return True
        cmap = doc.xref_stream(int(tounicode_match.group(1))).decode("latin-1", errors="replace")
        bfchar_count = sum(int(m) for m in _re.findall(r"(\d+)\s+beginbfchar", cmap))
        bfrange_count = sum(int(m) for m in _re.findall(r"(\d+)\s+beginbfrange", cmap))
        total_mappings = bfchar_count + bfrange_count

        font_size = _get_embedded_font_size(doc, obj, _re)
        if font_size is None:
            return False
        return font_size > 10_000 and total_mappings < 20
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def _get_embedded_font_size(doc, font_obj: str, _re) -> int | None:
    """Get embedded font file size from a CID font descriptor chain."""
    desc_match = _re.search(r"/DescendantFonts\s+(\d+)\s+0\s+R", font_obj)
    if not desc_match:
        return None
    desc_arr = doc.xref_object(int(desc_match.group(1)))
    cidfont_xref = _re.search(r"(\d+)\s+0\s+R", desc_arr)
    if not cidfont_xref:
        return None
    cidfont = doc.xref_object(int(cidfont_xref.group(1)))
    fd_match = _re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", cidfont)
    if not fd_match:
        return None
    fd = doc.xref_object(int(fd_match.group(1)))
    ff2_match = _re.search(r"/FontFile2\s+(\d+)\s+0\s+R", fd)
    if not ff2_match:
        return None
    return len(doc.xref_stream(int(ff2_match.group(1))))


def _is_garbled_text(text: str) -> bool:
    """Detect garbled text from broken font mapping in PDFs."""
    if not text or len(text.strip()) < 10:
        return False
    clean = text.strip().replace(" ", "").replace("\n", "")
    if not clean:
        return False
    from collections import Counter
    char_counts = Counter(clean)
    total = len(clean)

    top1_ratio = char_counts.most_common(1)[0][1] / total
    if top1_ratio > 0.25:
        return True

    cjk_counts = [(ch, cnt) for ch, cnt in char_counts.most_common(10)
                   if '\u4e00' <= ch <= '\u9fff' or '\uac00' <= ch <= '\ud7a3']
    if len(cjk_counts) >= 2:
        top3_cjk_total = sum(cnt for _, cnt in cjk_counts[:3])
        if top3_cjk_total / total > 0.4:
            return True

    unique_ratio = len(set(clean)) / total
    if unique_ratio < 0.08 and total > 30:
        return True
    return False


def _parse_pdf_enhanced(data: bytes, filename: str) -> ParseResult:
    """Enhanced PDF parsing with table extraction, scanned page detection, and image OCR."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"PDF open failed (encrypted or corrupt?): {e}") from e

    file_modified_at = _extract_pdf_date(doc.metadata.get("modDate", "")) or \
                       _extract_pdf_date(doc.metadata.get("creationDate", ""))

    texts = []
    tables: list[list[list[str]]] = []
    scanned_pages: list[int] = []
    extracted_images: list[bytes] = []

    for page_num, page in enumerate(doc):
        _classify_pdf_page(
            page, page_num, filename, doc,
            lambda p: _has_broken_cmap_fonts(p, doc), _is_garbled_text,
            texts, scanned_pages, tables, extracted_images,
        )

    doc.close()

    ocr_texts = []
    visual_analyses = []
    total_images = 0

    if scanned_pages:
        logger.info(
            "Detected %d scanned/garbled pages in %s: %s",
            len(scanned_pages), filename, scanned_pages,
        )
        doc2 = pymupdf.open(stream=data, filetype="pdf")
        for page_num in scanned_pages:
            page = doc2[page_num - 1]
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            logger.info(
                "Rendered page %d: %dx%d (%d bytes)",
                page_num, pix.width, pix.height, len(img_bytes),
            )
            page_ocr_text, page_analyses = _parser_utils._process_images_ocr([img_bytes])
            if page_ocr_text.strip():
                # Replace [Image 1 OCR] with [Page N OCR] for page renders
                page_ocr_clean = page_ocr_text.replace("[Image 1 OCR] ", "").strip()
                ocr_texts.append(f"[Page {page_num} OCR] {page_ocr_clean}")
                total_images += 1
            visual_analyses.extend(page_analyses)
        doc2.close()

    # Process embedded images (non-page) — keep [Image N OCR] tags
    if extracted_images:
        img_ocr_text, img_analyses = _parser_utils._process_images_ocr(extracted_images)
        if img_ocr_text.strip():
            ocr_texts.append(img_ocr_text)
            total_images += len(extracted_images)
        visual_analyses.extend(img_analyses)

    return ParseResult(
        text="\n\n".join(texts),
        tables=tables,
        ocr_text="\n".join(ocr_texts),
        images_processed=total_images,
        visual_analyses=visual_analyses,
        file_modified_at=file_modified_at,
    )
