"""Module-level helper functions for attachment parsing.

Extracted from attachment_parser.py to reduce file complexity.
These are standalone functions used by AttachmentParser class methods.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from src.config.weights import weights as _w
from .models import AttachmentOCRPolicy, AttachmentParseResult

logger = logging.getLogger(__name__)

# Constants extracted from AttachmentParser class for use by module-level helpers
OCR_MIN_DIMENSION = 32
OCR_MAX_ASPECT_RATIO = 8.0


def _text_chars(text: str) -> int:
    """Count non-whitespace text characters."""
    return len(text.strip()) if text else 0

# =============================================================================
# Environment helpers
# =============================================================================


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# =============================================================================
# Default OCR policy constants
# =============================================================================

_DEFAULT_ATTACHMENT_OCR_MODE = "force"
_DEFAULT_OCR_MIN_TEXT_CHARS = 100
_DEFAULT_OCR_MAX_PDF_PAGES = 1_000_000
_DEFAULT_OCR_MAX_PPT_SLIDES = 1_000_000
_DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT = 1

_SOURCE_ATTACHMENT_OCR_DEFAULTS: dict[str, dict[str, Any]] = {
    "itops": {
        "attachment_ocr_mode": "auto",
        "ocr_min_text_chars": 100,
        "ocr_max_pdf_pages": 10,
        "ocr_max_ppt_slides": 10,
        "ocr_max_images_per_attachment": 1,
        "slide_render_enabled": False,
        "layout_analysis_enabled": False,
    }
}


# =============================================================================
# OCR Subprocess Isolation (SIGSEGV Defense)
# =============================================================================
# PaddleOCR C++ inference can SIGSEGV on certain images (signal 11).
# Python try/except CANNOT catch OS signals — the entire process dies.
# Solution: run OCR in a forked subprocess via ProcessPoolExecutor.
# If SIGSEGV occurs, only the child process dies; the parent skips the image.
# Fork inherits the loaded PaddleOCR model via copy-on-write (no reload cost).


def _ocr_worker_fn(image_bytes: bytes) -> tuple:
    """OCR worker function executed in forked subprocess.

    Inherits PaddleOCR model from parent via fork COW — no model reload.
    If SIGSEGV occurs here, only this subprocess dies.
    """
    try:
        if not hasattr(_ocr_worker_fn, "_ocr"):
            try:
                from src.nlp.ocr.paddle_ocr_provider import PaddleOCRProvider

                _ocr_worker_fn._ocr = PaddleOCRProvider()
            except ImportError:
                return (None, 0.0, [])
        result = _ocr_worker_fn._ocr.extract(image_bytes)
        tables = [
            {"headers": t.headers, "rows": t.rows, "source": "ocr"}
            for t in (result.tables or [])
        ]
        return (result.text, result.confidence, tables)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return (None, 0.0, [])


# =============================================================================
# Policy resolution helpers (module-level to reduce class method complexity)
# =============================================================================


def _resolve_ocr_mode(
    overrides: dict[str, Any],
    source_defaults: dict[str, Any],
) -> str:
    """Resolve the OCR mode from overrides, env, source defaults, or global default."""
    raw_mode = (
        overrides.get("attachment_ocr_mode")
        or os.getenv("KNOWLEDGE_CRAWL_ATTACHMENT_OCR_MODE")
        or source_defaults.get("attachment_ocr_mode")
        or _DEFAULT_ATTACHMENT_OCR_MODE
    )
    value = str(raw_mode).strip().lower()
    return value if value in {"auto", "off", "force"} else _DEFAULT_ATTACHMENT_OCR_MODE


def _resolve_int_field(
    overrides: dict[str, Any],
    source_defaults: dict[str, Any],
    field_key: str,
    env_key: str,
    legacy_default: int,
) -> int:
    """Resolve an integer policy field from overrides, env, source defaults."""
    if field_key in overrides and overrides[field_key] is not None:
        return max(0, int(overrides[field_key]))
    env_value = _env_int(env_key)
    if env_value is not None:
        return max(0, env_value)
    if field_key in source_defaults:
        return max(0, int(source_defaults[field_key]))
    return legacy_default


def _resolve_bool_field(
    overrides: dict[str, Any],
    source_defaults: dict[str, Any],
    field_key: str,
    env_key: str,
    legacy_default: bool,
) -> bool:
    """Resolve a boolean policy field from overrides, env, source defaults."""
    if field_key in overrides and overrides[field_key] is not None:
        return bool(overrides[field_key])
    if os.getenv(env_key) is not None:
        return _env_bool(env_key, legacy_default)
    if field_key in source_defaults:
        return bool(source_defaults[field_key])
    return legacy_default


def _get_ocr_feature_flags() -> tuple[bool, bool]:
    """Return (preprocess_enabled, postprocess_enabled) from feature flags or env."""
    try:
        from src.core.feature_flags import FeatureFlags
        pre = FeatureFlags.is_knowledge_ocr_preprocess_enabled()
        post = FeatureFlags.is_knowledge_ocr_postprocess_enabled()
        return pre, post
    except ImportError:
        pre = os.getenv("KNOWLEDGE_OCR_PREPROCESS_ENABLED", "true").lower() == "true"
        post = os.getenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "true").lower() == "true"
        return pre, post


def _get_ocr_postprocess_flag() -> bool:
    """Return postprocess_enabled flag from feature flags or env."""
    try:
        from src.core.feature_flags import FeatureFlags
        return FeatureFlags.is_knowledge_ocr_postprocess_enabled()
    except ImportError:
        return os.getenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "true").lower() == "true"


def _filter_ocr_noise(ocr_text: str) -> str:
    """Remove OCR noise lines with repeated characters (e.g. '폐폐폐폐폐')."""
    clean_lines = []
    for line in ocr_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) >= 5:
            unique_chars = set(stripped.replace(" ", ""))
            if len(unique_chars) <= 2:
                continue
        clean_lines.append(line)
    return "\n".join(clean_lines)


# =============================================================================
# Module-level helper functions (extracted to reduce class method complexity)
# =============================================================================


def _decode_ole_text(raw: bytes) -> str | None:
    """Decode OLE2 Word document raw bytes to text."""
    for encoding in ("cp949", "cp1252"):
        try:
            decoded = raw.decode(encoding, errors="ignore")
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", decoded)
            readable = re.findall(
                r"[\uAC00-\uD7AF\u3001-\u9FFFa-zA-Z0-9\s,.!?()]+", cleaned,
            )
            text = " ".join(readable).strip()
            text = re.sub(r"\s{3,}", "\n\n", text)
            if len(text) > 50:
                return text
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def _try_cli_doc_extract(
    tool_path: str | None,
    file_path: Path,
    confidence: float,
    extra_args: list[str] | None = None,
) -> AttachmentParseResult | None:
    """Try extracting .doc text using a CLI tool (antiword/catdoc)."""
    import subprocess

    if not tool_path:
        return None
    try:
        cmd = [tool_path]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(str(file_path))
        _timeout = _w.timeouts.httpx_default
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_timeout)
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=confidence,
                native_text_chars=_text_chars(text),
            )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def _parse_ppt_ole_records(raw: bytes, struct) -> list[str]:
    """Parse OLE2 PowerPoint text records from raw stream bytes."""
    text_parts: list[str] = []
    offset = 0

    while offset < len(raw) - 8:
        try:
            rec_type = struct.unpack_from("<H", raw, offset + 2)[0]
            rec_len = struct.unpack_from("<I", raw, offset + 4)[0]
        except struct.error:
            break

        data_start = offset + 8
        data_end = data_start + rec_len

        if rec_len > 0 and data_end <= len(raw):
            text = _decode_ppt_record(raw, data_start, data_end, rec_type)
            if text:
                text_parts.append(text)

        offset = data_end if rec_len > 0 else offset + 1

    return text_parts


def _decode_ppt_record(
    raw: bytes, data_start: int, data_end: int, rec_type: int,
) -> str | None:
    """Decode a single PowerPoint OLE2 text record."""
    # TextCharsAtom (0x0FA0): UTF-16LE 텍스트
    if rec_type == 0x0FA0:
        text = raw[data_start:data_end].decode("utf-16-le", errors="ignore").strip()
        return text if text else None
    # TextBytesAtom (0x0FA8): ANSI 텍스트
    if rec_type == 0x0FA8:
        data = raw[data_start:data_end]
        try:
            text = data.decode("cp949").strip()
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="ignore").strip()
        return text if text else None
    return None


def _try_libreoffice_ppt_convert(
    file_path: Path, heartbeat_fn,
) -> AttachmentParseResult | None:
    """Try converting .ppt to .pptx via LibreOffice and parse the result."""
    import subprocess
    import tempfile

    from scripts.slide_renderer import _find_soffice

    soffice = _find_soffice()
    if not soffice:
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="ppt_convert_") as tmpdir:
            lo_profile = os.path.join(tmpdir, "lo_profile")
            os.makedirs(lo_profile, exist_ok=True)

            result = subprocess.run(
                [
                    soffice, "--headless", "--norestore",
                    f"-env:UserInstallation=file://{lo_profile}",
                    "--convert-to", "pptx",
                    "--outdir", tmpdir,
                    str(file_path),
                ],
                capture_output=True, text=True, timeout=_w.timeouts.subprocess_ocr_cli,
            )
            if result.returncode == 0:
                pptx_files = list(Path(tmpdir).glob("*.pptx"))
                if pptx_files:
                    logger.info(
                        "[PPT] Converted %s -> %s",
                        file_path.name, pptx_files[0].name,
                    )
                    return AttachmentParser.parse_ppt(
                        pptx_files[0], heartbeat_fn=heartbeat_fn,
                    )
            logger.info("[PPT] LibreOffice conversion failed: %s", result.stderr[:200])
    except subprocess.TimeoutExpired:
        logger.warning("[PPT] LibreOffice conversion timeout for %s", file_path.name)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("[PPT] LibreOffice conversion error: %s", e)

    return None


def _try_catppt_extract(file_path: Path) -> AttachmentParseResult | None:
    """Try extracting .ppt text using catppt CLI tool."""
    import shutil
    import subprocess

    catppt_path = shutil.which("catppt")
    if not catppt_path:
        return None
    try:
        result = subprocess.run(
            [catppt_path, str(file_path)],
            capture_output=True, text=True, timeout=_w.timeouts.httpx_default,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=0.6,
                native_text_chars=_text_chars(text),
            )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def _should_ocr_ppt(policy: AttachmentOCRPolicy, native_text_chars: int) -> bool:
    """Determine whether OCR should be applied for a PPT file."""
    if policy.attachment_ocr_mode == "off":
        return False
    if policy.attachment_ocr_mode == "force":
        return True
    # auto mode
    return native_text_chars < policy.ocr_min_text_chars


def _preprocess_shape_image(img, ocr_preprocess: bool) -> Any:
    """Apply OCR preprocessing to a shape image."""
    if ocr_preprocess:
        try:
            from scripts.ocr_preprocessor import preprocess_for_ocr
            return preprocess_for_ocr(img, mode="auto")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as preproc_err:
            logger.warning("[OCR] Preprocess failed, using original: %s", preproc_err)

    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _try_layout_ocr(img_original, policy) -> tuple[str | None, float]:
    """Try layout analysis OCR on the original color image."""
    if not policy.layout_analysis_enabled:
        return None, 0.0
    try:
        from scripts.ocr_preprocessor import analyze_layout_and_ocr
        regions = analyze_layout_and_ocr(img_original)
        if regions:
            text = "\n".join(r["content"] for r in regions if r.get("content"))
            return text, 0.7
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None, 0.0


def _apply_ocr_postprocess(ocr_text: str, ocr_conf: float) -> tuple[str, float]:
    """Apply OCR post-processing to extracted text."""
    try:
        from scripts.ocr_postprocessor import postprocess_ocr_text
        return postprocess_ocr_text(ocr_text, ocr_conf)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as postproc_err:
        logger.warning("[OCR] Postprocess failed: %s", postproc_err)
    return ocr_text, ocr_conf


def _preprocess_slide_image(img, slide_num: int) -> Any:
    """Apply preprocessing to a slide image for OCR."""
    try:
        from scripts.ocr_preprocessor import preprocess_for_ocr
        return preprocess_for_ocr(img, mode="slide")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("[OCR] Slide %d preprocess failed: %s", slide_num, e)
    return img


def _try_slide_layout_ocr(
    img_original, layout_analysis: bool, slide_num: int,
) -> str | None:
    """Try layout analysis OCR on a slide's original color image."""
    if not layout_analysis:
        return None
    try:
        from scripts.ocr_preprocessor import analyze_layout_and_ocr
        regions = analyze_layout_and_ocr(img_original)
        if regions:
            text = "\n".join(r["content"] for r in regions if r.get("content"))
            logger.info(
                "[OCR] Slide %d: layout analysis found %d regions",
                slide_num, len(regions),
            )
            return text
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("[OCR] Slide %d layout analysis failed: %s", slide_num, e)
    return None


def _postprocess_slide_text(ocr_text: str, slide_num: int) -> str:
    """Apply post-processing to OCR text from a slide."""
    try:
        from scripts.ocr_postprocessor import postprocess_ocr_text
        ocr_text, _ = postprocess_ocr_text(ocr_text)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("[OCR] Slide %d postprocess failed: %s", slide_num, e)
    return ocr_text


def _downscale_image(img, width: int, height: int, max_size: int) -> Any:
    """Downscale an image to fit within max_size, returning None if result too small."""
    ratio = min(max_size / width, max_size / height)
    new_size = (int(width * ratio), int(height * ratio))

    if (
        new_size[0] < OCR_MIN_DIMENSION
        or new_size[1] < OCR_MIN_DIMENSION
    ):
        logger.debug(
            "[OCR] 이미지 스킵 (리사이즈 후 너무 작음): %dx%d -> %dx%d",
            width, height, new_size[0], new_size[1],
        )
        return None

    img = img.resize(new_size, resample=3)  # Pillow LANCZOS = 3
    logger.debug(
        "[OCR] 이미지 리사이즈: %dx%d -> %dx%d",
        width, height, new_size[0], new_size[1],
    )
    return img


def _pad_extreme_aspect_ratio(img) -> Any:
    """Add white padding to correct extreme aspect ratios (>8:1)."""
    w, h = img.size
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect <= OCR_MAX_ASPECT_RATIO:
        return img

    target_short = max(w, h) // int(OCR_MAX_ASPECT_RATIO)
    from PIL import Image as _PilImage
    if w > h:
        padded = _PilImage.new("RGB", (w, target_short), (255, 255, 255))
        padded.paste(img, (0, (target_short - h) // 2))
    else:
        padded = _PilImage.new("RGB", (target_short, h), (255, 255, 255))
        padded.paste(img, ((target_short - w) // 2, 0))
    logger.debug(
        "[OCR] 이미지 패딩 (종횡비 %.1f:1 -> 8:1): %dx%d -> %dx%d",
        aspect, w, h, padded.size[0], padded.size[1],
    )
    return padded


def _image_result_no_ocr(
    metadata_text: str,
    policy: AttachmentOCRPolicy,
    skip_reason: str,
    ocr_units_deferred: int = 0,
) -> AttachmentParseResult:
    """Build an AttachmentParseResult for images where OCR was not performed."""
    return AttachmentParseResult(
        extracted_text=metadata_text,
        extracted_tables=[],
        confidence=0.5,
        ocr_mode=policy.attachment_ocr_mode,
        ocr_skip_reason=skip_reason,
        ocr_units_deferred=ocr_units_deferred,
    )
