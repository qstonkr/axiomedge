"""OCR Module for knowledge-local.

PaddleOCR-only text extraction from scanned PDFs and images.
No fallback providers -- errors propagate for easier debugging.

Environment variables:
- OCR_PROVIDER: "paddle" (default)
"""

from src.ocr.hybrid_ocr_service import (
    OCRInput,
    OCRProvider,
    OCRResult,
)
from src.ocr.paddle_ocr_provider import PaddleOCRProvider

__all__ = [
    "OCRInput",
    "OCRProvider",
    "OCRResult",
    "PaddleOCRProvider",
]
