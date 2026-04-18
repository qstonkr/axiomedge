"""OCR data models and provider abstraction.

Usage:
    from src.nlp.ocr.models import OCRResult, OCRProvider, OCRInput
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

type OCRInput = str | bytes


@dataclass(frozen=True, slots=True)
class OCRResult:
    """OCR result with confidence and trace metadata."""

    text: str
    confidence: float
    provider: str
    used_fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class OCRProvider(abc.ABC):
    """Provider abstraction for OCR engines."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier for logging."""

    @abc.abstractmethod
    async def ocr(self, image: OCRInput) -> OCRResult:
        """Run OCR on the input image."""
