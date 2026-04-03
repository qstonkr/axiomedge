"""Step 3: Arrow/line detection (OpenCV).

HoughLinesP-based connection line detection + arrowhead direction detection.
"""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np

from .models import DetectedShape, RawEdge

logger = logging.getLogger(__name__)

# HoughLinesP parameters
HOUGH_THRESHOLD = 50
MIN_LINE_LENGTH = 30
MAX_LINE_GAP = 15

# Segment merge distance threshold
MERGE_DISTANCE = 20

# Arrowhead triangle detection radius
ARROWHEAD_SEARCH_RADIUS = 25


class ArrowDetector:
    """OpenCV HoughLinesP-based arrow/connection line detection."""

    def detect(
        self, image_np: np.ndarray, shapes: list[DetectedShape]
    ) -> list[RawEdge]:
        """Detect arrows and connection lines.

        1. Mask shape interiors (ignore lines inside shapes)
        2. Canny -> HoughLinesP
        3. Segment clustering (merge nearby segments)
        4. Arrowhead direction detection
        5. Map line endpoints to nearest shapes

        Args:
            image_np: BGR numpy array
            shapes: Detected shapes list

        Returns:
            List of RawEdge
        """
        gray = cv2.cvtColor(image_np, cv2.COLOR_BGR2GRAY)

        # Mask shape interiors
        mask = np.ones(gray.shape, dtype=np.uint8) * 255
        for shape in shapes:
            cv2.drawContours(mask, [shape.contour], -1, 0, -1)
            # Also mask shape boundaries (prevent boundary lines from being detected)
            cv2.drawContours(mask, [shape.contour], -1, 0, 3)

        masked = cv2.bitwise_and(gray, mask)

        # Canny -> HoughLinesP
        edges = cv2.Canny(masked, 50, 150)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=HOUGH_THRESHOLD,
            minLineLength=MIN_LINE_LENGTH,
            maxLineGap=MAX_LINE_GAP,
        )

        if lines is None:
            return []

        # Extract segments
        raw_segments = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            raw_segments.append(((float(x1), float(y1)), (float(x2), float(y2))))

        # Merge segments
        merged = self._merge_segments(raw_segments)

        # Arrowhead detection + shape mapping + self-reference/unmapped filter
        result: list[RawEdge] = []
        for start, end in merged:
            has_arrowhead = self._detect_arrowhead(image_np, start, end)
            source_idx = self._find_nearest_shape(start, shapes)
            target_idx = self._find_nearest_shape(end, shapes)

            # Remove self-references (lines inside a shape) + None==None noise
            if source_idx is None and target_idx is None:
                continue  # both unmapped -- noise
            if source_idx is not None and source_idx == target_idx:
                continue  # same shape internal line

            result.append(RawEdge(
                start=start,
                end=end,
                has_arrowhead=has_arrowhead,
                source_shape_idx=source_idx,
                target_shape_idx=target_idx,
            ))

        logger.debug("Detected %d edges (%d with arrowheads)", len(result), sum(1 for e in result if e.has_arrowhead))
        return result

    def _merge_segments(
        self, segments: list[tuple[tuple[float, float], tuple[float, float]]]
    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Merge nearby segments."""
        if not segments:
            return []

        used = [False] * len(segments)
        merged: list[tuple[tuple[float, float], tuple[float, float]]] = []

        for i, (s1, e1) in enumerate(segments):
            if used[i]:
                continue

            group_start = s1
            group_end = e1
            used[i] = True

            for j in range(i + 1, len(segments)):
                if used[j]:
                    continue
                s2, e2 = segments[j]

                # Merge if any endpoint is close
                if (
                    self._point_distance(group_end, s2) < MERGE_DISTANCE
                    or self._point_distance(group_end, e2) < MERGE_DISTANCE
                    or self._point_distance(group_start, s2) < MERGE_DISTANCE
                    or self._point_distance(group_start, e2) < MERGE_DISTANCE
                ):
                    # Select the two farthest points
                    points = [group_start, group_end, s2, e2]
                    max_dist = 0.0
                    best_pair = (group_start, group_end)
                    for a_idx in range(len(points)):
                        for b_idx in range(a_idx + 1, len(points)):
                            d = self._point_distance(points[a_idx], points[b_idx])
                            if d > max_dist:
                                max_dist = d
                                best_pair = (points[a_idx], points[b_idx])
                    group_start, group_end = best_pair
                    used[j] = True

            merged.append((group_start, group_end))

        return merged

    def _detect_arrowhead(
        self,
        image_np: np.ndarray,
        _start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        """Detect arrowhead by finding triangle contour near endpoint."""
        h, w = image_np.shape[:2]
        ex, ey = int(end[0]), int(end[1])

        # Extract ROI around endpoint
        x1 = max(0, ex - ARROWHEAD_SEARCH_RADIUS)
        y1 = max(0, ey - ARROWHEAD_SEARCH_RADIUS)
        x2 = min(w, ex + ARROWHEAD_SEARCH_RADIUS)
        y2 = min(h, ey + ARROWHEAD_SEARCH_RADIUS)

        if x2 - x1 < 5 or y2 - y1 < 5:
            return False

        roi = image_np[y1:y2, x1:x2]
        gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges_roi = cv2.Canny(gray_roi, 50, 150)

        contours, _ = cv2.findContours(edges_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 20 or area > 2000:
                continue
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
            if len(approx) == 3:
                return True

        return False

    def _find_nearest_shape(
        self,
        point: tuple[float, float],
        shapes: list[DetectedShape],
        max_distance: float = 80.0,
    ) -> int | None:
        """Return index of nearest shape to point.

        Returns the shape whose boundary is within max_distance of the point.
        """
        best_idx: int | None = None
        best_dist = max_distance

        for idx, shape in enumerate(shapes):
            # Distance to contour (positive=outside, negative=inside, 0=boundary)
            dist = abs(cv2.pointPolygonTest(
                shape.contour,
                (float(point[0]), float(point[1])),
                measureDist=True,
            ))
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        return best_idx

    @staticmethod
    def _point_distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])
