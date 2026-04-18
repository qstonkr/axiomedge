"""PDF parsing mixin for AttachmentParser.

Handles PDF text extraction via PyMuPDF with OCR fallback for textless pages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .models import AttachmentParseResult
from ._attachment_helpers import _get_ocr_postprocess_flag

logger = logging.getLogger(__name__)


class _PdfParserMixin:
    """PDF parsing methods for AttachmentParser."""

    @classmethod
    def _ocr_pdf_page(cls, page, page_num: int, total_pages: int, policy, heartbeat_fn) -> tuple:
        """OCR a single textless PDF page via image rendering.

        Returns (ocr_text, success_bool).
        """
        import fitz

        cls._emit_status(
            heartbeat_fn,
            f"ocr_processing pdf page={page_num}/{total_pages}",
        )
        zoom = 2.0  # 144 DPI (72 * 2)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        del pix  # Free native memory immediately

        postprocess = _get_ocr_postprocess_flag()

        ocr_text = cls._ocr_slide_image(
            png_bytes, page_num,
            preprocess=True,
            layout_analysis=policy.layout_analysis_enabled,
            postprocess=postprocess,
        )
        return ocr_text

    @staticmethod
    def _extract_pdf_page_tables(page, page_num: int) -> list[dict]:
        """Extract tables from a single PDF page using PyMuPDF."""
        tables = []
        try:
            page_tables = page.find_tables()
            for table in page_tables:
                table_data = table.extract()
                if table_data and len(table_data) > 1:
                    headers = table_data[0]
                    rows = table_data[1:]
                    tables.append({
                        "page": page_num,
                        "headers": headers,
                        "rows": [
                            dict(zip(headers, row))
                            for row in rows
                            if len(row) == len(headers)
                        ],
                    })
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ):
            pass  # 테이블 추출 실패 시 건너뛰기
        return tables

    @staticmethod
    def _compute_pdf_confidence(
        has_text: bool, ocr_units_extracted: int,
    ) -> float:
        """Compute PDF parse confidence score."""
        if has_text and ocr_units_extracted == 0:
            return 0.9
        if has_text:
            return 0.7
        return 0.0

    @classmethod
    def parse_pdf(
        cls, file_path: Path, heartbeat_fn=None,
    ) -> AttachmentParseResult:
        """PDF에서 텍스트와 테이블 추출

        Strategy:
        1. PyMuPDF 텍스트 레이어 추출 시도
        2. 텍스트가 빈 페이지 -> 이미지 렌더링 -> PaddleOCR fallback
           (이미지 기반 PDF -- PPT를 PDF로 내보낸 파일 등)
        """
        try:
            policy = cls.current_policy()
            import fitz  # PyMuPDF  # noqa: F811

            doc = fitz.open(file_path)
            text_parts: list[str] = []
            tables: list[dict] = []
            native_text_chars = 0
            total_pages = len(doc)
            textless_pages = 0
            ocr_counters = {
                "attempted": 0, "extracted": 0, "deferred": 0, "chars": 0,
            }

            cls._emit_status(
                heartbeat_fn, f"native_extract pdf pages={total_pages}",
            )

            for page_num, page in enumerate(doc, 1):
                page_text = page.get_text("text")
                if page_text.strip():
                    text_parts.append(f"[Page {page_num}]\n{page_text}")
                    native_text_chars += cls._text_chars(page_text)
                else:
                    textless_pages += 1
                    cls._process_textless_pdf_page(
                        page, page_num, total_pages, policy,
                        heartbeat_fn, text_parts, ocr_counters,
                    )

                tables.extend(
                    cls._extract_pdf_page_tables(page, page_num),
                )

                if heartbeat_fn and page_num % 5 == 0:
                    heartbeat_fn(f"pdf_ocr: {page_num}/{total_pages}")

            doc.close()

            full_text = "\n\n".join(text_parts)
            if ocr_counters["extracted"] > 0:
                logger.info(
                    "[PDF OCR] %d/%d pages used OCR fallback",
                    ocr_counters["extracted"], total_pages,
                )
            if ocr_counters["deferred"] > 0:
                cls._emit_status(
                    heartbeat_fn,
                    f"ocr_skipped_budget pdf "
                    f"deferred={ocr_counters['deferred']}",
                )

            confidence = cls._compute_pdf_confidence(
                bool(full_text.strip()), ocr_counters["extracted"],
            )

            skip_reason = None
            if textless_pages > 0 and policy.attachment_ocr_mode == "off":
                skip_reason = "disabled"
            elif ocr_counters["deferred"] > 0:
                skip_reason = "budget_exceeded"

            return AttachmentParseResult(
                extracted_text=full_text,
                extracted_tables=tables,
                confidence=confidence,
                ocr_mode=policy.attachment_ocr_mode,
                ocr_applied=ocr_counters["extracted"] > 0,
                ocr_skip_reason=skip_reason,
                ocr_units_attempted=ocr_counters["attempted"],
                ocr_units_extracted=ocr_counters["extracted"],
                ocr_units_deferred=ocr_counters["deferred"],
                native_text_chars=native_text_chars,
                ocr_text_chars=ocr_counters["chars"],
            )

        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as e:
            return AttachmentParseResult(
                extracted_text=f"[PDF 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_mode=cls.current_policy().attachment_ocr_mode,
                ocr_skip_reason="parse_error",
            )

    @classmethod
    def _process_textless_pdf_page(
        cls, page, page_num, total_pages, policy, heartbeat_fn,
        text_parts, ocr_counters,
    ) -> None:
        """Handle a textless PDF page -- apply OCR if allowed by policy.

        Note: This mutates text_parts and ocr_counters in place.
        """
        if policy.attachment_ocr_mode == "off":
            return
        if ocr_counters["attempted"] >= policy.ocr_max_pdf_pages:
            ocr_counters["deferred"] += 1
            return
        try:
            ocr_counters["attempted"] += 1
            ocr_text = cls._ocr_pdf_page(
                page, page_num, total_pages, policy, heartbeat_fn,
            )
            if ocr_text and ocr_text.strip():
                text_parts.append(f"[Page {page_num}]\n{ocr_text}")
                ocr_counters["extracted"] += 1
                ocr_counters["chars"] += cls._text_chars(ocr_text)
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as ocr_err:
            logger.warning(
                "[PDF OCR] Page %d OCR failed: %s", page_num, ocr_err,
            )
