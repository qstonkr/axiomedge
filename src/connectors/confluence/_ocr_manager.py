# pyright: reportAttributeAccessIssue=false
"""OCR management and image parsing mixin for AttachmentParser.

Handles OCR singleton lifecycle, subprocess-isolated OCR execution,
slide image OCR, and image file parsing.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

from .models import AttachmentParseResult
from ._attachment_helpers import (
    OCR_MAX_ASPECT_RATIO,
    OCR_MIN_DIMENSION,
    _downscale_image,
    _filter_ocr_noise,
    _image_result_no_ocr,
    _ocr_worker_fn,
    _pad_extreme_aspect_ratio,
    _postprocess_slide_text,
    _preprocess_slide_image,
    _try_slide_layout_ocr,
)

logger = logging.getLogger(__name__)


class _OcrManagerMixin:
    """OCR singleton management and image parsing methods."""

    # -----------------------------------------------------------------
    # OCR singleton management
    # -----------------------------------------------------------------

    _ocr_instance = None
    _ocr_type = None  # "paddle" only (amd64 Crawler Pod)
    _ocr_lock = __import__("threading").Lock()

    @classmethod
    def _get_ocr_instance(cls) -> Any:
        """싱글톤 PaddleOCR 인스턴스 반환 (amd64 only)."""
        if cls._ocr_instance is None:
            try:
                from src.nlp.ocr.paddle_ocr_provider import (
                    PaddleOCRProvider,
                )
                cls._ocr_instance = PaddleOCRProvider()
                cls._ocr_type = "paddle"
                logger.info("[OCR] PaddleOCR singleton created")
            except ImportError:
                logger.warning(
                    "[OCR] PaddleOCR not available (requires amd64)",
                )
                return None
        return cls._ocr_instance

    @classmethod
    def cleanup_ocr(cls) -> None:
        """OCR 인스턴스 정리 및 메모리 해제."""
        import gc
        cls._ocr_instance = None
        cls._ocr_type = None
        # Shutdown subprocess pool if active
        if cls._ocr_process_pool is not None:
            try:
                cls._ocr_process_pool.shutdown(wait=False)
            except (
                RuntimeError, OSError, ValueError,
                TypeError, KeyError, AttributeError,
            ):
                pass
            cls._ocr_process_pool = None
        gc.collect()
        logger.info("[OCR] 메모리 정리 완료")

    # --- Subprocess-isolated OCR (SIGSEGV defense) ---
    _ocr_process_pool = None
    _ocr_pool_lock = __import__("threading").Lock()

    @classmethod
    def _resolve_pool_workers(cls) -> int:
        """Settings 의 ``pipeline.ocr_pool_workers`` 를 읽어 결정 (PR-3 F).

        - 0 (default) → ``min(4, cpu_count)``. GPU 사용 시 1 강제 (OOM 방지).
        - >=1 → 그대로 사용.
        - 환경 변수 ``PADDLE_USE_GPU=1`` 이면 GPU 1장 공유로 1 worker 강제.
        """
        import os as _os
        if _os.getenv("PADDLE_USE_GPU", "").lower() in ("1", "true", "yes"):
            return 1

        try:
            from src.config import get_settings
            cfg = int(getattr(
                get_settings().pipeline, "ocr_pool_workers", 0,
            ))
        except (ImportError, AttributeError, ValueError, RuntimeError):
            cfg = 0
        if cfg > 0:
            return cfg
        return min(4, _os.cpu_count() or 1)

    @classmethod
    def _ocr_extract_safe(
        cls, image_bytes: bytes, file_name: str = "",
        timeout: int = 1800,
    ) -> tuple[str | None, float, list]:
        """Execute OCR in a forked subprocess to survive SIGSEGV."""
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        import multiprocessing as mp

        with cls._ocr_pool_lock:
            if cls._ocr_process_pool is None:
                ctx = mp.get_context("fork")
                workers = cls._resolve_pool_workers()
                logger.info("[OCR] ProcessPool starting with %d worker(s)", workers)
                cls._ocr_process_pool = ProcessPoolExecutor(
                    max_workers=workers, mp_context=ctx,
                )
            pool = cls._ocr_process_pool

        try:
            future = pool.submit(_ocr_worker_fn, image_bytes)
            return future.result(timeout=timeout)
        except BrokenProcessPool:
            logger.error(
                "[OCR SIGSEGV] Worker crashed on %s "
                "— restarting pool, skipping image",
                file_name,
            )
            with cls._ocr_pool_lock:
                cls._ocr_process_pool = None
            return None, 0.0, []
        except TimeoutError:
            logger.warning(
                "[OCR Timeout] %s exceeded %ds — skipped",
                file_name, timeout,
            )
            return None, 0.0, []
        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as e:
            logger.warning(
                "[OCR Error] %s: %s — skipped", file_name, e,
            )
            return None, 0.0, []

    # PaddleOCR PP-OCRv5 det 모델이 극단적 종횡비에서 SIGSEGV 발생
    _OCR_MIN_DIMENSION = OCR_MIN_DIMENSION
    _OCR_MAX_ASPECT_RATIO = OCR_MAX_ASPECT_RATIO

    @staticmethod
    def _resize_image_if_needed(img, max_size: int = 2048) -> Any:
        """큰 이미지 리사이즈 (메모리 최적화).

        Returns:
            리사이즈된 이미지 (또는 원본).
            극단적 종횡비(>8:1)나 너무 작은(<32px) 이미지는 None.
        """
        width, height = img.size

        if (
            width < _OcrManagerMixin._OCR_MIN_DIMENSION
            or height < _OcrManagerMixin._OCR_MIN_DIMENSION
        ):
            logger.debug(
                "[OCR] 이미지 스킵 (너무 작음): %dx%d",
                width, height,
            )
            return None

        if width > max_size or height > max_size:
            img = _downscale_image(img, width, height, max_size)
            if img is None:
                return None

        return _pad_extreme_aspect_ratio(img)

    # -----------------------------------------------------------------
    # OCR slide image -- split into stages
    # -----------------------------------------------------------------

    @classmethod
    def _ocr_slide_image(
        cls,
        png_bytes: bytes,
        slide_num: int,
        preprocess: bool = True,
        layout_analysis: bool = True,
        postprocess: bool = True,
    ) -> str | None:
        """OCR a rendered slide image with preprocessing."""
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(png_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")

            img_original = img.copy()

            if preprocess:
                img = _preprocess_slide_image(img, slide_num)

            ocr_text = _try_slide_layout_ocr(
                img_original, layout_analysis, slide_num,
            )

            if not ocr_text:
                ocr_text = cls._fallback_standard_ocr(
                    img, slide_num,
                )
                if not ocr_text:
                    return None

            if ocr_text and postprocess:
                ocr_text = _postprocess_slide_text(
                    ocr_text, slide_num,
                )

            if ocr_text:
                ocr_text = _filter_ocr_noise(ocr_text)

            return ocr_text if ocr_text and ocr_text.strip() else None

        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as e:
            logger.error(
                "[OCR] Slide %d OCR error: %s", slide_num, e,
            )
            return None

    @classmethod
    def _fallback_standard_ocr(
        cls, img, slide_num: int,
    ) -> str | None:
        """Run standard OCR on a preprocessed slide image."""
        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        with cls._ocr_lock:
            ocr_text, ocr_conf, _ = cls._ocr_extract_safe(
                img_buffer.getvalue(),
                f"rendered_slide_{slide_num}",
            )
        if not ocr_text or ocr_conf <= 0.3:
            return None
        return ocr_text

    # -----------------------------------------------------------------
    # Image parsing
    # -----------------------------------------------------------------

    @classmethod
    def _parse_image_sync(
        cls,
        file_path: Path,
        content: bytes,
        use_ocr: bool = True,
    ) -> AttachmentParseResult:
        """이미지 OCR 및 메타데이터 추출 (동기 내부 구현)."""
        try:
            policy = cls.current_policy()
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            width, height = img.size
            format_type = img.format or "unknown"
            metadata_text = (
                f"[Image: {width}x{height}, {format_type}, "
                f"{len(content):,} bytes]"
            )

            if policy.attachment_ocr_mode == "off" or not use_ocr:
                return _image_result_no_ocr(
                    metadata_text, policy, "disabled",
                )

            if policy.ocr_max_images_per_attachment <= 0:
                cls._emit_status(
                    None,
                    "ocr_skipped_budget image deferred=1",
                )
                return _image_result_no_ocr(
                    metadata_text, policy, "budget_exceeded",
                    ocr_units_deferred=1,
                )

            if len(content) >= 10_000_000:
                return _image_result_no_ocr(
                    metadata_text, policy, "image_too_large",
                )

            return cls._perform_image_ocr(
                img, content, file_path, metadata_text, policy,
            )

        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as e:
            return AttachmentParseResult(
                extracted_text=f"[이미지 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_mode=cls.current_policy().attachment_ocr_mode,
                ocr_skip_reason="parse_error",
            )

    @classmethod
    def _perform_image_ocr(
        cls, img, content, file_path, metadata_text, policy,
    ) -> Any:
        """Execute OCR on an image and return the result."""
        import gc

        try:
            cls._emit_status(
                None,
                f"ocr_processing image file={file_path.name}",
            )
            ocr = cls._get_ocr_instance()
            if ocr is None:
                return _image_result_no_ocr(
                    metadata_text, policy, "ocr_unavailable",
                )

            img = cls._resize_image_if_needed(img)
            if img is None:
                return _image_result_no_ocr(
                    metadata_text, policy, "guard_rejected",
                )

            if img.mode != "RGB":
                img = img.convert("RGB")

            img_buffer = io.BytesIO()
            img.save(img_buffer, format="PNG")
            resized_content = img_buffer.getvalue()

            with cls._ocr_lock:
                ocr_text, ocr_conf, ocr_tables = (
                    cls._ocr_extract_safe(
                        resized_content, file_path.name,
                    )
                )

            if ocr_text and ocr_conf > 0.3:
                full_text = f"{metadata_text}\n\n{ocr_text}"
                del img, img_buffer, resized_content
                gc.collect()

                return AttachmentParseResult(
                    extracted_text=full_text,
                    extracted_tables=ocr_tables,
                    confidence=ocr_conf,
                    ocr_mode=policy.attachment_ocr_mode,
                    ocr_applied=True,
                    ocr_units_attempted=1,
                    ocr_units_extracted=1,
                    ocr_text_chars=cls._text_chars(ocr_text),
                )

        except (
            RuntimeError, OSError, ValueError,
            TypeError, KeyError, AttributeError,
        ) as ocr_error:
            logger.warning(
                "[OCR Warning] %s: %s",
                file_path.name, ocr_error,
            )

        return AttachmentParseResult(
            extracted_text=metadata_text,
            extracted_tables=[],
            confidence=0.5,
            ocr_mode=policy.attachment_ocr_mode,
            ocr_skip_reason="ocr_failed",
            ocr_units_attempted=1,
        )

    @classmethod
    def parse_image(
        cls,
        file_path: Path,
        content: bytes,
        use_ocr: bool = True,
    ) -> AttachmentParseResult:
        """이미지 OCR 및 메타데이터 추출 (동기 호출용 래퍼)."""
        return cls._parse_image_sync(file_path, content, use_ocr)

    @classmethod
    async def parse_image_async(
        cls,
        file_path: Path,
        content: bytes,
        use_ocr: bool = True,
    ) -> AttachmentParseResult:
        """이미지 OCR 비동기 처리."""
        return await asyncio.to_thread(
            cls._parse_image_sync, file_path, content, use_ocr,
        )
