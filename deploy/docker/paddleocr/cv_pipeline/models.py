"""CV Pipeline data models.

Intermediate result data models for OpenCV + PaddleOCR based image structure analysis.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class SignalQuality(enum.Enum):
    """CV pipeline data quality classification.

    Determines the processing path and prompt composition
    based on the output quality of each pipeline stage.

    Cases:
        FULL: OCR + shapes + edges all good -> full prompt
        OCR_PRIMARY: Rich OCR + noisy shapes -> OCR coordinate layout only
        SHAPE_PRIMARY: Sparse OCR + good shapes -> shape-centric prompt
        OCR_ONLY: OCR only, no shapes/edges -> text extraction only (no LLM needed)
        EMPTY: No meaningful data
    """

    FULL = "full"
    OCR_PRIMARY = "ocr_primary"
    SHAPE_PRIMARY = "shape_primary"
    OCR_ONLY = "ocr_only"
    EMPTY = "empty"


@dataclass
class OCRBox:
    """PaddleOCR text + coordinate info."""

    text: str
    polygon: list[list[float]]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    confidence: float
    center: tuple[float, float]  # computed center point


@dataclass
class DetectedShape:
    """OpenCV contour-based detected shape."""

    shape_type: str  # "rectangle", "circle", "diamond", "rounded_rect"
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    center: tuple[float, float]
    area: float
    contour: Any  # OpenCV contour (np.ndarray)


@dataclass
class RawEdge:
    """OpenCV HoughLinesP-based detected connection line."""

    start: tuple[float, float]
    end: tuple[float, float]
    has_arrowhead: bool
    source_shape_idx: int | None = None  # nearest shape to start point
    target_shape_idx: int | None = None  # nearest shape to end point


@dataclass
class CVResult:
    """CV pipeline intermediate result."""

    ocr_boxes: list[OCRBox] = field(default_factory=list)
    shapes: list[DetectedShape] = field(default_factory=list)
    edges: list[RawEdge] = field(default_factory=list)
    shape_texts: dict[int, list[str]] = field(default_factory=dict)  # shape_idx -> texts
    unmapped_texts: list[str] = field(default_factory=list)  # texts outside shapes
    image_width: int = 0
    image_height: int = 0
