"""OCR Module - PaddleOCR for Korean text extraction.

No fallback providers. Errors propagate for debugging.
"""

from src.nlp.ocr.models import OCRInput, OCRProvider, OCRResult
from src.nlp.ocr.paddle_ocr_provider import PaddleOCRProvider

__all__ = [
    "OCRInput",
    "OCRProvider",
    "OCRResult",
    "PaddleOCRProvider",
]
