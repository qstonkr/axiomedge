"""Step 2: Shape detection (OpenCV).

Canny Edge + findContours + approxPolyDP based shape classification.
Detects: rectangle, circle, diamond, rounded_rect, triangle, polygon.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from .models import DetectedShape, OCRBox

logger = logging.getLogger(__name__)

MIN_SHAPE_AREA = 5000  # minimum area (px^2) -- removes small text residue
APPROX_EPSILON_RATIO = 0.02  # approxPolyDP approximation ratio


class ShapeDetector:
    """OpenCV contour-based shape detection."""

    def detect(
        self, image_np: np.ndarray, ocr_boxes: list[OCRBox] | None = None
    ) -> list[DetectedShape]:
        """OpenCV contour-based shape detection.

        1. Grayscale -> Gaussian Blur -> Canny Edge
        2. findContours -> area filter
        3. approxPolyDP vertex count classification
        4. Remove contours overlapping OCR text regions

        Args:
            image_np: BGR numpy array
            ocr_boxes: OCR text boxes (for text residue filtering)

        Returns:
            List of DetectedShape
        """
        gray = cv2.cvtColor(image_np, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        # Morphology operations to connect broken edges
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.erode(edges, kernel, iterations=1)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Build OCR text mask (text residue filter)
        text_mask = self._build_text_mask(image_np.shape, ocr_boxes) if ocr_boxes else None

        shapes: list[DetectedShape] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < MIN_SHAPE_AREA:
                continue

            # Remove contours that excessively overlap text regions
            if text_mask is not None and self._is_text_contour(contour, text_mask):
                continue

            shape_type = self._classify_shape(contour)
            x, y, w, h = cv2.boundingRect(contour)
            M = cv2.moments(contour)
            if M["m00"] == 0:
                cx, cy = float(x + w / 2), float(y + h / 2)
            else:
                cx = M["m10"] / M["m00"]
                cy = M["m01"] / M["m00"]

            shapes.append(DetectedShape(
                shape_type=shape_type,
                bbox=(x, y, w, h),
                center=(cx, cy),
                area=area,
                contour=contour,
            ))

        logger.debug("Detected %d shapes", len(shapes))
        return shapes

    def _classify_shape(self, contour: np.ndarray) -> str:
        """Classify shape by contour vertex count."""
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, APPROX_EPSILON_RATIO * peri, True)
        vertices = len(approx)

        if vertices == 4:
            x, y, w, h = cv2.boundingRect(approx)

            # Diamond detection: vertices at midpoints of bbox edges, not corners
            if self._is_diamond(approx, x, y, w, h):
                return "diamond"

            return "rectangle"

        if vertices >= 8:
            # Circle vs rounded_rect: circularity test
            area = cv2.contourArea(contour)
            circularity = 4 * np.pi * area / (peri * peri) if peri > 0 else 0
            if circularity > 0.75:
                return "circle"
            return "rounded_rect"

        if vertices == 3:
            return "triangle"

        return "polygon"

    def _is_diamond(
        self, approx: np.ndarray, x: int, y: int, w: int, h: int
    ) -> bool:
        """Diamond (rhombus) detection.

        Vertices near midpoints of bbox edges indicate a diamond.
        """
        mid_x, mid_y = x + w / 2, y + h / 2
        tolerance = max(w, h) * 0.3

        midpoints = 0
        for pt in approx:
            px, py = pt[0]
            # Near top/bottom midpoint or left/right midpoint
            near_horizontal_mid = abs(px - mid_x) < tolerance
            near_vertical_mid = abs(py - mid_y) < tolerance
            near_edge = (
                abs(px - x) < tolerance
                or abs(px - (x + w)) < tolerance
                or abs(py - y) < tolerance
                or abs(py - (y + h)) < tolerance
            )
            if (near_horizontal_mid or near_vertical_mid) and near_edge:
                midpoints += 1

        return midpoints >= 3

    def _build_text_mask(
        self, image_shape: tuple, ocr_boxes: list[OCRBox]
    ) -> np.ndarray:
        """Build OCR text region mask."""
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        for box in ocr_boxes:
            pts = np.array(box.polygon, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
        return mask

    def _is_text_contour(self, contour: np.ndarray, text_mask: np.ndarray) -> bool:
        """Determine if contour excessively overlaps text regions."""
        contour_mask = np.zeros_like(text_mask)
        cv2.drawContours(contour_mask, [contour], -1, 255, -1)

        overlap = cv2.bitwise_and(contour_mask, text_mask)
        contour_area = cv2.countNonZero(contour_mask)
        overlap_area = cv2.countNonZero(overlap)

        if contour_area == 0:
            return False

        # 80% or more overlap => treat as text residue
        return (overlap_area / contour_area) > 0.8
