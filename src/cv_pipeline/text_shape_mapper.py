"""Step 4: Text-to-shape mapping.

Determines which shape each OCR text center point falls inside.
Uses point-in-polygon test with bbox margin fallback.
"""

from __future__ import annotations

import logging

import cv2

from .models import DetectedShape, OCRBox

logger = logging.getLogger(__name__)


class TextShapeMapper:
    """OCR text -> shape mapping."""

    def map(
        self, ocr_boxes: list[OCRBox], shapes: list[DetectedShape]
    ) -> tuple[dict[int, list[str]], list[str]]:
        """Determine which shape each OCR text center point falls inside.

        Args:
            ocr_boxes: OCR text boxes
            shapes: Detected shapes

        Returns:
            (shape_texts: {shape_idx: [texts]}, unmapped_texts)
        """
        shape_texts: dict[int, list[str]] = {}
        unmapped_texts: list[str] = []

        for box in ocr_boxes:
            if not box.text.strip():
                continue

            center = (box.center[0], box.center[1])
            best_idx = self._find_smallest_containing_shape(center, shapes)

            if best_idx is not None:
                if best_idx not in shape_texts:
                    shape_texts[best_idx] = []
                shape_texts[best_idx].append(box.text)
            else:
                unmapped_texts.append(box.text)

        logger.debug(
            "Mapped %d texts to shapes, %d unmapped",
            sum(len(v) for v in shape_texts.values()),
            len(unmapped_texts),
        )
        return shape_texts, unmapped_texts

    def _find_smallest_containing_shape(
        self, center: tuple[float, float], shapes: list[DetectedShape],
    ) -> int | None:
        """Find the smallest shape containing the center point."""
        best_idx: int | None = None
        best_area = float("inf")
        for idx, shape in enumerate(shapes):
            if self._point_in_shape(center, shape):
                if shape.area < best_area:
                    best_area = shape.area
                    best_idx = idx
        return best_idx

    def _point_in_shape(
        self, point: tuple[float, float], shape: DetectedShape
    ) -> bool:
        """Determine if point is inside shape.

        Uses cv2.pointPolygonTest for precise test,
        falls back to bbox-based test with 10px margin.
        """
        result = cv2.pointPolygonTest(
            shape.contour,
            (float(point[0]), float(point[1])),
            measureDist=False,
        )
        if result >= 0:
            return True

        # Bbox-based fallback with 10px margin
        x, y, w, h = shape.bbox
        margin = 10
        return (
            x - margin <= point[0] <= x + w + margin
            and y - margin <= point[1] <= y + h + margin
        )
