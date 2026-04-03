"""Step 1: PaddleOCR coordinate OCR.

Extracts text with bounding box coordinates using PaddleOCR.
"""

from __future__ import annotations

import logging

from .models import OCRBox

logger = logging.getLogger(__name__)


class OCRWithCoords:
    """Extract text + coordinates with PaddleOCR."""

    def extract(self, image_bytes: bytes) -> list[OCRBox]:
        """Extract text + bounding box coordinates via PaddleOCR.

        Args:
            image_bytes: Image byte data

        Returns:
            List of OCRBox (text, coordinates, confidence)
        """
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except ImportError:
            logger.debug("PaddleOCR not available")
            return []

        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img_array = np.array(img)

        # Initialize PaddleOCR
        ocr = PaddleOCR(lang="korean", use_gpu=False, use_angle_cls=True)
        result = ocr.ocr(img_array, cls=True)

        if not result or not isinstance(result, list) or len(result) == 0:
            return []

        # Handle both new predict API and legacy ocr API formats
        ocr_result = result[0]

        # Try new-style .json attribute first
        try:
            if hasattr(ocr_result, "json"):
                result_dict = ocr_result.json
                res = result_dict.get("res", {})
                rec_texts = res.get("rec_texts", [])
                rec_scores = res.get("rec_scores", [])
                dt_polys = res.get("dt_polys", [])

                if rec_texts and dt_polys:
                    boxes: list[OCRBox] = []
                    for i, (text, poly) in enumerate(zip(rec_texts, dt_polys)):
                        if not text:
                            continue
                        score = rec_scores[i] if i < len(rec_scores) else 0.0
                        if not isinstance(poly, (list, tuple)) or len(poly) < 4:
                            continue
                        polygon = [[float(p[0]), float(p[1])] for p in poly[:4]]
                        cx = sum(p[0] for p in polygon) / 4
                        cy = sum(p[1] for p in polygon) / 4
                        boxes.append(OCRBox(
                            text=text,
                            polygon=polygon,
                            confidence=float(score),
                            center=(cx, cy),
                        ))
                    logger.debug("Extracted %d OCR boxes with coordinates", len(boxes))
                    return boxes
        except (AttributeError, TypeError):
            pass

        # Legacy format: [[box, (text, score)], ...]
        return self._extract_legacy(result)

    def _extract_legacy(self, result: list) -> list[OCRBox]:
        """Legacy PaddleOCR format handling."""
        boxes: list[OCRBox] = []
        try:
            ocr_lines = result[0]
            if not isinstance(ocr_lines, list):
                return []
            for line in ocr_lines:
                if not isinstance(line, (list, tuple)) or len(line) < 2:
                    continue
                poly_raw = line[0]
                text_info = line[1]
                if not isinstance(text_info, (list, tuple)) or len(text_info) < 2:
                    continue
                text = str(text_info[0]) if text_info[0] else ""
                conf = float(text_info[1]) if text_info[1] else 0.0
                if not text or not isinstance(poly_raw, (list, tuple)) or len(poly_raw) < 4:
                    continue
                polygon = [[float(p[0]), float(p[1])] for p in poly_raw[:4]]
                cx = sum(p[0] for p in polygon) / 4
                cy = sum(p[1] for p in polygon) / 4
                boxes.append(OCRBox(text=text, polygon=polygon, confidence=conf, center=(cx, cy)))
        except Exception as exc:
            logger.warning("Legacy OCR parsing failed: %s", exc)
        return boxes
