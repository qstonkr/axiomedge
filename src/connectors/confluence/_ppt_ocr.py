# pyright: reportAttributeAccessIssue=false
"""PPT OCR sub-operations mixin.

Handles slide rendering OCR, shape-by-shape OCR, retry logic,
and PDF fallback for PowerPoint files.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

from src.config.weights import weights as _w
from ._attachment_helpers import (
    _apply_ocr_postprocess,
    _preprocess_shape_image,
    _try_layout_ocr,
)

logger = logging.getLogger(__name__)


class _PptOcrMixin:
    """PPT OCR operations (slide render, shape OCR, retry, PDF fb)."""

    @classmethod
    def _render_and_ocr_slides(
        cls, file_path: Path, policy, heartbeat_fn,
        ocr_preprocess: bool, ocr_postprocess: bool,
    ) -> tuple[bool, list[str], int, int, int, int, set[int]]:
        """Render PPTX slides via LibreOffice and OCR each image.

        Returns (slide_rendered, text_parts, attempted, extracted,
                 deferred, ocr_chars, extracted_slides).
        """
        text_parts: list[str] = []
        ocr_units_attempted = 0
        ocr_units_extracted = 0
        ocr_units_deferred = 0
        ocr_text_chars = 0
        extracted_slides: set[int] = set()

        try:
            from scripts.slide_renderer import render_slides_as_images

            rendered_slides = render_slides_as_images(
                Path(str(file_path)),
            )
            if not rendered_slides:
                return (
                    False, text_parts, 0, 0, 0, 0, extracted_slides,
                )

            logger.info(
                "[OCR] Slide rendering: %d slides from %s",
                len(rendered_slides), file_path,
            )
            for slide_num, png_bytes in rendered_slides:
                if ocr_units_attempted >= policy.ocr_max_ppt_slides:
                    ocr_units_deferred += 1
                    continue
                ocr_units_attempted += 1
                cls._emit_status(
                    heartbeat_fn,
                    f"ocr_processing ppt "
                    f"slide={slide_num}/{len(rendered_slides)}",
                )
                ocr_text = cls._ocr_slide_image(
                    png_bytes, slide_num,
                    preprocess=ocr_preprocess,
                    layout_analysis=policy.layout_analysis_enabled,
                    postprocess=ocr_postprocess,
                )
                if ocr_text:
                    text_parts.append(
                        f"[Slide {slide_num} OCR]\n{ocr_text}",
                    )
                    ocr_units_extracted += 1
                    ocr_text_chars += cls._text_chars(ocr_text)
                    extracted_slides.add(slide_num)
                if heartbeat_fn and slide_num % 5 == 0:
                    heartbeat_fn(
                        f"slide_render_ocr: "
                        f"{slide_num}/{len(rendered_slides)}",
                    )

            return (
                True, text_parts, ocr_units_attempted,
                ocr_units_extracted, ocr_units_deferred,
                ocr_text_chars, extracted_slides,
            )
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as render_err:
            logger.warning(
                "[OCR] Slide rendering failed, "
                "falling back to shape OCR: %s",
                render_err,
            )
            return (
                False, text_parts, 0, 0, 0, 0, extracted_slides,
            )

    @classmethod
    def _ocr_single_shape_image(
        cls, slide_num: int, image_bytes: bytes, policy,
        ocr_preprocess: bool, ocr_postprocess: bool,
        total_slides: int, heartbeat_fn,
        attempted_slides: set[int],
    ) -> tuple[str | None, float, bool]:
        """OCR a single shape image with preprocessing.

        Returns (ocr_text, ocr_conf, timed_out).
        """
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        img = cls._resize_image_if_needed(img)
        if img is None:
            return None, 0.0, False

        if slide_num not in attempted_slides:
            attempted_slides.add(slide_num)
            cls._emit_status(
                heartbeat_fn,
                f"ocr_processing ppt "
                f"slide={slide_num}/{total_slides}",
            )

        img_original = img.copy()
        if img_original.mode != "RGB":
            img_original = img_original.convert("RGB")

        img = _preprocess_shape_image(img, ocr_preprocess)

        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        png_bytes = img_buffer.getvalue()

        # Layout analysis -- uses original color image
        ocr_text, ocr_conf = _try_layout_ocr(img_original, policy)

        if not ocr_text:
            with cls._ocr_lock:
                ocr_text, ocr_conf, _ = cls._ocr_extract_safe(
                    png_bytes, f"slide_{slide_num}",
                )

        if ocr_text and ocr_postprocess:
            ocr_text, ocr_conf = _apply_ocr_postprocess(
                ocr_text, ocr_conf,
            )

        timed_out = ocr_text is None
        return ocr_text, ocr_conf, timed_out

    @classmethod
    def _process_one_shape_ocr(
        cls, slide_num, image_bytes, policy,
        prs_slides_count, heartbeat_fn,
        ocr_preprocess, ocr_postprocess,
        attempted_slides, extracted_slides,
    ) -> dict | None:
        """Process OCR for a single shape image.

        Returns None if OCR unavailable.
        """
        if (
            slide_num not in attempted_slides
            and len(attempted_slides) >= policy.ocr_max_ppt_slides
        ):
            return {"deferred": 1}
        ocr = cls._get_ocr_instance()
        if ocr is None:
            return None
        result = {
            "attempted": 0, "extracted": 0, "deferred": 0,
            "chars": 0, "text": None, "timed_out_item": None,
        }
        try:
            if slide_num not in attempted_slides:
                result["attempted"] = 1
            ocr_text, ocr_conf, timed_out = (
                cls._ocr_single_shape_image(
                    slide_num, image_bytes, policy,
                    ocr_preprocess, ocr_postprocess,
                    prs_slides_count, heartbeat_fn,
                    attempted_slides,
                )
            )
            if ocr_text and ocr_conf > 0.3:
                result["text"] = (
                    f"[Slide {slide_num} Image OCR]\n{ocr_text}"
                )
                if slide_num not in extracted_slides:
                    extracted_slides.add(slide_num)
                    result["extracted"] = 1
                result["chars"] = cls._text_chars(ocr_text)
            elif timed_out:
                result["timed_out_item"] = (slide_num, image_bytes)
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as ocr_err:
            logger.warning(
                "[OCR Warning] Slide %d image: %s",
                slide_num, ocr_err,
            )
        return result

    @staticmethod
    def _accumulate_ocr_result(
        item: dict, totals: dict, text_parts: list[str],
        timed_out_images: list[tuple[int, bytes]] | None = None,
    ) -> None:
        """Accumulate a single OCR item result into totals."""
        totals["attempted"] += item.get("attempted", 0)
        totals["extracted"] += item.get("extracted", 0)
        totals["deferred"] += item.get("deferred", 0)
        totals["chars"] += item.get("chars", 0)
        if item.get("text"):
            text_parts.append(item["text"])
        if (
            timed_out_images is not None
            and item.get("timed_out_item")
        ):
            timed_out_images.append(item["timed_out_item"])

    @classmethod
    def _shape_ocr_pass(
        cls, image_shapes, policy, prs_slides_count, heartbeat_fn,
        ocr_preprocess: bool, ocr_postprocess: bool,
        extracted_slides: set[int],
    ) -> tuple:
        """Run shape-by-shape OCR on collected image shapes.

        Returns (text_parts, attempted, extracted, deferred,
                 ocr_chars).
        """
        text_parts: list[str] = []
        timed_out_images: list[tuple[int, bytes]] = []
        totals = {
            "attempted": 0, "extracted": 0,
            "deferred": 0, "chars": 0,
        }
        ocr_processed = 0
        ocr_total = len(image_shapes)
        attempted_slides: set[int] = set()

        for slide_num, image_bytes in image_shapes:
            item_result = cls._process_one_shape_ocr(
                slide_num, image_bytes, policy,
                prs_slides_count, heartbeat_fn,
                ocr_preprocess, ocr_postprocess,
                attempted_slides, extracted_slides,
            )
            if item_result is None:
                break  # OCR not available
            cls._accumulate_ocr_result(
                item_result, totals, text_parts, timed_out_images,
            )
            ocr_processed += 1
            if heartbeat_fn and ocr_processed % 10 == 0:
                heartbeat_fn(
                    f"ocr: {ocr_processed}/{ocr_total} images, "
                    f"slide_{slide_num}",
                )

        # Retry timed-out images
        retry_results = cls._retry_timed_out_images(
            timed_out_images, policy, ocr_postprocess,
            attempted_slides, extracted_slides, heartbeat_fn,
        )
        text_parts.extend(retry_results["text_parts"])
        totals["attempted"] += retry_results["attempted"]
        totals["extracted"] += retry_results["extracted"]
        totals["deferred"] += retry_results["deferred"]
        totals["chars"] += retry_results["chars"]

        return (
            text_parts, totals["attempted"], totals["extracted"],
            totals["deferred"], totals["chars"],
        )

    @classmethod
    def _retry_timed_out_images(
        cls, timed_out_images, policy, ocr_postprocess,
        attempted_slides, extracted_slides, heartbeat_fn,
    ) -> dict:
        """Retry OCR on images that timed out in the first pass."""
        result = {
            "text_parts": [],
            "attempted": 0,
            "extracted": 0,
            "deferred": 0,
            "chars": 0,
        }
        if not timed_out_images:
            return result

        logger.info(
            "[OCR Retry] %d timed-out images, retrying...",
            len(timed_out_images),
        )
        if heartbeat_fn:
            heartbeat_fn(
                f"ocr_retry: {len(timed_out_images)} images to retry",
            )

        for slide_num, png_bytes in timed_out_images:
            item = cls._retry_one_image(
                slide_num, png_bytes, policy, ocr_postprocess,
                attempted_slides, extracted_slides,
            )
            cls._accumulate_ocr_result(
                item, result, result["text_parts"],
            )

        return result

    @classmethod
    def _retry_one_image(
        cls, slide_num, png_bytes, policy, ocr_postprocess,
        attempted_slides, extracted_slides,
    ) -> dict:
        """Retry OCR on a single timed-out image."""
        r = {
            "attempted": 0, "extracted": 0,
            "deferred": 0, "chars": 0, "text": None,
        }
        if (
            slide_num not in attempted_slides
            and len(attempted_slides) >= policy.ocr_max_ppt_slides
        ):
            r["deferred"] = 1
            return r
        try:
            if slide_num not in attempted_slides:
                attempted_slides.add(slide_num)
                r["attempted"] = 1
            with cls._ocr_lock:
                ocr_text, ocr_conf, _ = cls._ocr_extract_safe(
                    png_bytes, f"retry_slide_{slide_num}",
                )
            if ocr_text and ocr_postprocess:
                ocr_text, ocr_conf = _apply_ocr_postprocess(
                    ocr_text, ocr_conf,
                )
            if ocr_text and ocr_conf > 0.3:
                r["text"] = (
                    f"[Slide {slide_num} Image OCR]\n{ocr_text}"
                )
                if slide_num not in extracted_slides:
                    extracted_slides.add(slide_num)
                    r["extracted"] = 1
                r["chars"] = cls._text_chars(ocr_text)
                logger.info(
                    "[OCR Retry] slide_%d: OK (%d chars)",
                    slide_num, len(ocr_text),
                )
            else:
                logger.info(
                    "[OCR Retry] slide_%d: still failed", slide_num,
                )
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as retry_err:
            logger.warning(
                "[OCR Retry] slide_%d: error - %s",
                slide_num, retry_err,
            )
        return r

    @classmethod
    def _ppt_pdf_fallback(cls, file_path: Path, heartbeat_fn) -> tuple | None:
        """Convert PPTX to PDF via LibreOffice, then OCR the PDF.

        Returns (text, tables, ocr_text_chars) or None.
        """
        import subprocess
        import tempfile

        from scripts.slide_renderer import _find_soffice

        soffice = _find_soffice()
        if not soffice:
            return None

        try:
            with tempfile.TemporaryDirectory(
                prefix="pptx_pdf_fallback_",
            ) as tmpdir:
                lo_profile = os.path.join(tmpdir, "lo_profile")
                os.makedirs(lo_profile, exist_ok=True)

                result = subprocess.run(
                    [
                        soffice, "--headless", "--norestore",
                        f"-env:UserInstallation=file://{lo_profile}",
                        "--convert-to", "pdf",
                        "--outdir", tmpdir,
                        str(file_path),
                    ],
                    capture_output=True, text=True,
                    timeout=_w.timeouts.subprocess_ocr_cli,
                )
                if result.returncode != 0:
                    return None

                pdf_files = list(Path(tmpdir).glob("*.pdf"))
                if not pdf_files:
                    return None

                logger.info(
                    "[PPT] PDF fallback: converted %s -> %s",
                    file_path.name, pdf_files[0].name,
                )
                pdf_result = cls.parse_pdf(
                    pdf_files[0], heartbeat_fn=heartbeat_fn,
                )
                if pdf_result.extracted_text.strip():
                    return (
                        pdf_result.extracted_text,
                        pdf_result.extracted_tables,
                        pdf_result.ocr_text_chars,
                    )
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as fb_err:
            logger.warning(
                "[PPT] PDF fallback failed: %s", fb_err,
            )

        return None

    @classmethod
    def _apply_pdf_fallback_if_needed(
        cls, should_ocr, full_text, tables, ocr_text_chars,
        file_path, heartbeat_fn,
    ) -> tuple:
        """Apply PDF fallback if OCR results are too sparse."""
        if not (should_ocr and len(full_text.strip()) < 50):
            return full_text, tables, ocr_text_chars
        logger.info(
            "[PPT] Empty result after all extraction (%d chars),"
            " trying PDF fallback",
            len(full_text),
        )
        fb = cls._ppt_pdf_fallback(file_path, heartbeat_fn)
        if fb is not None:
            fb_text, fb_tables, fb_chars = fb
            if len(fb_text.strip()) > len(full_text.strip()):
                full_text = fb_text
                tables = fb_tables or tables
                ocr_text_chars = max(ocr_text_chars, fb_chars)
                logger.info(
                    "[PPT] PDF fallback produced %d chars",
                    len(full_text),
                )
        return full_text, tables, ocr_text_chars
