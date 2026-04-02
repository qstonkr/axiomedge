"""PaddleOCR Provider (Optional Dependency).

Purpose:
    Wrap PaddleOCR behind the `OCRProvider` interface used by the hybrid OCR
    service. This module uses lazy imports so the base runtime does not require
    PaddleOCR unless OCR features are enabled.

Features:
    - Lazy initialization (import + model load only when first used)
    - Simple confidence aggregation from per-line scores
    - Supports both file-path and bytes input (bytes are written to a temp file)

Usage:
    from src.ocr import PaddleOCRProvider

    Examples:
        provider = PaddleOCRProvider(model_name="korean_PP-OCRv5_server_rec", use_gpu=False)
        result = await provider.ocr("/tmp/spec.png")
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any

from ..config_weights import weights as _w
from .models import OCRInput, OCRProvider, OCRResult


class PaddleOCRProvider(OCRProvider):
    """PaddleOCR-based OCR provider."""

    def __init__(
        self,
        *,
        model_name: str = _w.ocr.paddle_model,
        use_gpu: bool = _w.ocr.use_gpu,
        enable_orientation: bool = _w.ocr.enable_orientation,
    ) -> None:
        self._model_name = model_name
        self._use_gpu = bool(use_gpu)
        self._enable_orientation = bool(enable_orientation)
        self._ocr: Any | None = None

    @property
    def name(self) -> str:
        return "paddleocr"

    async def ocr(self, image: OCRInput) -> OCRResult:
        ocr_engine = await self._get_engine()

        if isinstance(image, bytes):
            # PaddleOCR APIs are most stable with file paths; keep this predictable.
            import asyncio
            f = await asyncio.to_thread(tempfile.NamedTemporaryFile, suffix=".png", delete=False)
            try:
                await asyncio.to_thread(f.write, image)
                await asyncio.to_thread(f.flush)
                return await self._ocr_path(ocr_engine, f.name)
            finally:
                await asyncio.to_thread(f.close)
                import os
                os.unlink(f.name)

        return await self._ocr_path(ocr_engine, image)

    async def _get_engine(self) -> Any:
        if self._ocr is not None:
            return self._ocr

        def _init() -> Any:
            try:
                from paddleocr import PaddleOCR  # type: ignore[import-not-found]
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "PaddleOCR is not installed. Install optional OCR deps to enable this provider."
                ) from e

            # PaddleOCR constructor parameters vary by version.
            # We prefer safe defaults and tolerate older versions.
            try:
                return PaddleOCR(
                    lang="korean",
                    use_gpu=self._use_gpu,
                    use_angle_cls=self._enable_orientation,
                )
            except TypeError:
                return PaddleOCR(
                    lang="korean",
                    use_gpu=self._use_gpu,
                )

        self._ocr = await asyncio.to_thread(_init)
        return self._ocr

    async def _ocr_path(self, ocr_engine: Any, path: str) -> OCRResult:
        def _run() -> Any:
            return ocr_engine.ocr(path, cls=self._enable_orientation)

        raw = await asyncio.to_thread(_run)
        text, confidence = self._parse_result(raw)
        return OCRResult(
            text=text,
            confidence=confidence,
            provider=self.name,
            used_fallback=False,
            metadata={
                "model_name": self._model_name,
                "use_gpu": self._use_gpu,
                "enable_orientation": self._enable_orientation,
            },
        )

    def _parse_result(self, raw: Any) -> tuple[str, float]:
        # Typical output:
        # [
        #   [ [box], (text, score) ],
        #   ...
        # ]
        if not raw:
            return "", 0.0

        # Some versions wrap in an outer list per image.
        lines = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw

        texts: list[str] = []
        scores: list[float] = []
        for item in lines:
            try:
                _, (txt, score) = item
                if txt:
                    texts.append(str(txt))
                if score is not None:
                    scores.append(float(score))
            except Exception:
                continue

        merged_text = "\n".join(texts).strip()
        if not scores:
            return merged_text, 0.0

        # Simple average; if future weighting is needed, do it in the domain layer.
        avg = sum(scores) / len(scores)
        return merged_text, max(min(avg, 1.0), 0.0)
