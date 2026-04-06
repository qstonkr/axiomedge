"""Attachment content extraction for Confluence crawled files.

Handles PDF, Excel, Word (.doc/.docx), PowerPoint (.ppt/.pptx), and image files.
Uses PaddleOCR for image-based content extraction with subprocess isolation to
defend against SIGSEGV crashes in PaddleOCR's C++ inference engine.

Originally extracted from ``scripts/confluence_full_crawler.py``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from pathlib import Path
from typing import Any

from .models import AttachmentOCRPolicy, AttachmentParseResult

logger = logging.getLogger(__name__)

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
                from src.ocr.paddle_ocr_provider import PaddleOCRProvider

                _ocr_worker_fn._ocr = PaddleOCRProvider()
            except ImportError:
                return (None, 0.0, [])
        result = _ocr_worker_fn._ocr.extract(image_bytes)
        tables = [
            {"headers": t.headers, "rows": t.rows, "source": "ocr"}
            for t in (result.tables or [])
        ]
        return (result.text, result.confidence, tables)
    except Exception:
        return (None, 0.0, [])


# =============================================================================
# Attachment Parsers
# =============================================================================
class AttachmentParser:
    """첨부파일 내용 추출기"""

    _active_source_key = ""
    _ocr_policy = AttachmentOCRPolicy(
        attachment_ocr_mode=_DEFAULT_ATTACHMENT_OCR_MODE,
        ocr_min_text_chars=_DEFAULT_OCR_MIN_TEXT_CHARS,
        ocr_max_pdf_pages=_DEFAULT_OCR_MAX_PDF_PAGES,
        ocr_max_ppt_slides=_DEFAULT_OCR_MAX_PPT_SLIDES,
        ocr_max_images_per_attachment=_DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT,
        slide_render_enabled=_env_bool("KNOWLEDGE_SLIDE_RENDER_ENABLED", True),
        layout_analysis_enabled=_env_bool("KNOWLEDGE_LAYOUT_ANALYSIS_ENABLED", True),
    )

    @classmethod
    def configure_run(cls, source_key: str, overrides: dict[str, Any] | None = None) -> AttachmentOCRPolicy:
        """Resolve run-local OCR policy once per crawl source."""
        overrides = overrides or {}
        source_defaults = _SOURCE_ATTACHMENT_OCR_DEFAULTS.get(source_key, {})
        legacy_slide_render = _env_bool("KNOWLEDGE_SLIDE_RENDER_ENABLED", True)
        legacy_layout_analysis = _env_bool("KNOWLEDGE_LAYOUT_ANALYSIS_ENABLED", True)

        def _resolve_mode() -> str:
            raw_mode = (
                overrides.get("attachment_ocr_mode")
                or os.getenv("KNOWLEDGE_CRAWL_ATTACHMENT_OCR_MODE")
                or source_defaults.get("attachment_ocr_mode")
                or _DEFAULT_ATTACHMENT_OCR_MODE
            )
            value = str(raw_mode).strip().lower()
            return value if value in {"auto", "off", "force"} else _DEFAULT_ATTACHMENT_OCR_MODE

        def _resolve_int_value(
            override_key: str,
            env_key: str,
            source_key_name: str,
            legacy_default: int,
        ) -> int:
            if override_key in overrides and overrides[override_key] is not None:
                return max(0, int(overrides[override_key]))
            env_value = _env_int(env_key)
            if env_value is not None:
                return max(0, env_value)
            if source_key_name in source_defaults:
                return max(0, int(source_defaults[source_key_name]))
            return legacy_default

        def _resolve_bool_value(
            override_key: str,
            env_key: str,
            source_key_name: str,
            legacy_default: bool,
        ) -> bool:
            if override_key in overrides and overrides[override_key] is not None:
                return bool(overrides[override_key])
            if os.getenv(env_key) is not None:
                return _env_bool(env_key, legacy_default)
            if source_key_name in source_defaults:
                return bool(source_defaults[source_key_name])
            return legacy_default

        cls._active_source_key = source_key
        cls._ocr_policy = AttachmentOCRPolicy(
            attachment_ocr_mode=_resolve_mode(),
            ocr_min_text_chars=_resolve_int_value(
                "ocr_min_text_chars",
                "KNOWLEDGE_CRAWL_OCR_MIN_TEXT_CHARS",
                "ocr_min_text_chars",
                _DEFAULT_OCR_MIN_TEXT_CHARS,
            ),
            ocr_max_pdf_pages=_resolve_int_value(
                "ocr_max_pdf_pages",
                "KNOWLEDGE_CRAWL_OCR_MAX_PDF_PAGES",
                "ocr_max_pdf_pages",
                _DEFAULT_OCR_MAX_PDF_PAGES,
            ),
            ocr_max_ppt_slides=_resolve_int_value(
                "ocr_max_ppt_slides",
                "KNOWLEDGE_CRAWL_OCR_MAX_PPT_SLIDES",
                "ocr_max_ppt_slides",
                _DEFAULT_OCR_MAX_PPT_SLIDES,
            ),
            ocr_max_images_per_attachment=_resolve_int_value(
                "ocr_max_images_per_attachment",
                "KNOWLEDGE_CRAWL_OCR_MAX_IMAGES_PER_ATTACHMENT",
                "ocr_max_images_per_attachment",
                _DEFAULT_OCR_MAX_IMAGES_PER_ATTACHMENT,
            ),
            slide_render_enabled=_resolve_bool_value(
                "slide_render_enabled",
                "KNOWLEDGE_CRAWL_SLIDE_RENDER_ENABLED",
                "slide_render_enabled",
                legacy_slide_render,
            ),
            layout_analysis_enabled=_resolve_bool_value(
                "layout_analysis_enabled",
                "KNOWLEDGE_CRAWL_LAYOUT_ANALYSIS_ENABLED",
                "layout_analysis_enabled",
                legacy_layout_analysis,
            ),
        )
        return cls._ocr_policy

    @classmethod
    def current_policy(cls) -> AttachmentOCRPolicy:
        return cls._ocr_policy

    @staticmethod
    def _emit_status(heartbeat_fn, message: str) -> None:
        if heartbeat_fn:
            heartbeat_fn(message)
        else:
            logger.debug("[status] %s", message)

    @staticmethod
    def _text_chars(value: str | None) -> int:
        return len(value.strip()) if value and value.strip() else 0

    @classmethod
    def parse_pdf(cls, file_path: Path, heartbeat_fn=None) -> AttachmentParseResult:
        """PDF에서 텍스트와 테이블 추출

        Strategy:
        1. PyMuPDF 텍스트 레이어 추출 시도
        2. 텍스트가 빈 페이지 → 이미지 렌더링 → PaddleOCR fallback
           (이미지 기반 PDF — PPT를 PDF로 내보낸 파일 등)

        Returns:
            (extracted_text, tables, confidence)
        """
        try:
            policy = cls.current_policy()
            import fitz  # PyMuPDF

            doc = fitz.open(file_path)
            text_parts = []
            tables = []
            native_text_chars = 0
            ocr_text_chars = 0
            ocr_units_attempted = 0
            ocr_units_extracted = 0
            ocr_units_deferred = 0
            total_pages = len(doc)
            textless_pages = 0

            cls._emit_status(heartbeat_fn, f"native_extract pdf pages={total_pages}")

            for page_num, page in enumerate(doc, 1):
                # 1차: 텍스트 레이어 추출
                page_text = page.get_text("text")
                if page_text.strip():
                    text_parts.append(f"[Page {page_num}]\n{page_text}")
                    native_text_chars += cls._text_chars(page_text)
                else:
                    textless_pages += 1
                    if policy.attachment_ocr_mode == "off":
                        continue
                    if ocr_units_attempted >= policy.ocr_max_pdf_pages:
                        ocr_units_deferred += 1
                        continue
                    # 2차: 이미지 렌더링 → PaddleOCR fallback
                    try:
                        ocr_units_attempted += 1
                        cls._emit_status(
                            heartbeat_fn,
                            f"ocr_processing pdf page={page_num}/{total_pages}",
                        )
                        zoom = 2.0  # 144 DPI (72 * 2)
                        mat = fitz.Matrix(zoom, zoom)
                        pix = page.get_pixmap(matrix=mat)
                        png_bytes = pix.tobytes("png")
                        del pix  # Free native memory immediately

                        try:
                            from src.core.feature_flags import FeatureFlags
                            _pdf_postprocess = FeatureFlags.is_knowledge_ocr_postprocess_enabled()
                        except ImportError:
                            _pdf_postprocess = os.getenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "true").lower() == "true"

                        ocr_text = cls._ocr_slide_image(
                            png_bytes, page_num,
                            preprocess=True,
                            layout_analysis=policy.layout_analysis_enabled,
                            postprocess=_pdf_postprocess,
                        )
                        if ocr_text and ocr_text.strip():
                            text_parts.append(f"[Page {page_num}]\n{ocr_text}")
                            ocr_units_extracted += 1
                            ocr_text_chars += cls._text_chars(ocr_text)
                    except Exception as ocr_err:
                        logger.warning("[PDF OCR] Page %d OCR failed: %s", page_num, ocr_err)

                if heartbeat_fn and page_num % 5 == 0:
                    heartbeat_fn(f"pdf_ocr: {page_num}/{total_pages}")

                # 테이블 추출 시도
                try:
                    page_tables = page.find_tables()
                    for table in page_tables:
                        table_data = table.extract()
                        if table_data and len(table_data) > 1:
                            headers = table_data[0] if table_data else []
                            rows = table_data[1:] if len(table_data) > 1 else []
                            tables.append({
                                "page": page_num,
                                "headers": headers,
                                "rows": [dict(zip(headers, row)) for row in rows if len(row) == len(headers)],
                            })
                except Exception:
                    pass  # 테이블 추출 실패 시 건너뛰기

            doc.close()

            full_text = "\n\n".join(text_parts)
            if ocr_units_extracted > 0:
                logger.info("[PDF OCR] %d/%d pages used OCR fallback", ocr_units_extracted, total_pages)
            if ocr_units_deferred > 0:
                cls._emit_status(
                    heartbeat_fn,
                    f"ocr_skipped_budget pdf deferred={ocr_units_deferred}",
                )
            confidence = 0.9 if full_text.strip() and ocr_units_extracted == 0 else (0.7 if full_text.strip() else 0.0)

            skip_reason = None
            if textless_pages > 0 and policy.attachment_ocr_mode == "off":
                skip_reason = "disabled"
            elif ocr_units_deferred > 0:
                skip_reason = "budget_exceeded"

            return AttachmentParseResult(
                extracted_text=full_text,
                extracted_tables=tables,
                confidence=confidence,
                ocr_mode=policy.attachment_ocr_mode,
                ocr_applied=ocr_units_extracted > 0,
                ocr_skip_reason=skip_reason,
                ocr_units_attempted=ocr_units_attempted,
                ocr_units_extracted=ocr_units_extracted,
                ocr_units_deferred=ocr_units_deferred,
                native_text_chars=native_text_chars,
                ocr_text_chars=ocr_text_chars,
            )

        except Exception as e:
            return AttachmentParseResult(
                extracted_text=f"[PDF 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_mode=cls.current_policy().attachment_ocr_mode,
                ocr_skip_reason="parse_error",
            )

    @staticmethod
    def parse_excel(file_path: Path) -> AttachmentParseResult:
        """Excel에서 시트 데이터 추출"""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file_path, read_only=True, data_only=True)
            text_parts = []
            tables = []

            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]

                # 시트 데이터를 테이블로 변환
                rows_data = []
                for row in sheet.iter_rows(values_only=True):
                    row_values = [str(cell) if cell is not None else "" for cell in row]
                    if any(v.strip() for v in row_values):  # 빈 행 제외
                        rows_data.append(row_values)

                if rows_data:
                    headers = rows_data[0] if rows_data else []
                    data_rows = rows_data[1:] if len(rows_data) > 1 else []

                    tables.append({
                        "sheet": sheet_name,
                        "headers": headers,
                        "rows": [dict(zip(headers, row)) for row in data_rows if len(row) == len(headers)],
                        "row_count": len(data_rows),
                    })

                    # 텍스트 표현
                    text_parts.append(f"[Sheet: {sheet_name}]")
                    text_parts.append(" | ".join(headers))
                    for row in data_rows[:10]:  # 최대 10행만 텍스트로
                        text_parts.append(" | ".join(row))
                    if len(data_rows) > 10:
                        text_parts.append(f"... 외 {len(data_rows) - 10}행")

            wb.close()

            full_text = "\n".join(text_parts)
            confidence = 0.95 if tables else 0.0

            return AttachmentParseResult(
                extracted_text=full_text,
                extracted_tables=tables,
                confidence=confidence,
                native_text_chars=AttachmentParser._text_chars(full_text),
            )

        except Exception as e:
            return AttachmentParseResult(
                extracted_text=f"[Excel 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_skip_reason="parse_error",
            )

    @staticmethod
    def _extract_doc_olefile(file_path: Path) -> str | None:
        """Pure Python .doc 텍스트 추출 (olefile OLE2 스트림 파싱)"""
        try:
            import olefile
        except ImportError:
            return None

        try:
            ole = olefile.OleFileIO(str(file_path))
        except Exception:
            return None

        try:
            if not ole.exists("WordDocument"):
                return None

            raw = ole.openstream("WordDocument").read()
            if len(raw) < 20:
                return None

            # Word .doc: 텍스트는 FIB 이후 영역에 저장됨
            # 인코딩 우선순위: cp949(한국어) → cp1252(서양어) → utf-16-le
            for encoding in ("cp949", "cp1252"):
                try:
                    decoded = raw.decode(encoding, errors="ignore")
                    # 제어 문자 제거 (탭/줄바꿈 제외)
                    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", decoded)
                    # 유효 텍스트 비율 검사 (한글/영문/숫자/공백)
                    readable = re.findall(r"[\uAC00-\uD7AF\u3000-\u9FFFa-zA-Z0-9\s,.!?()]+", cleaned)
                    text = " ".join(readable).strip()
                    text = re.sub(r"\s{3,}", "\n\n", text)
                    if len(text) > 50:
                        return text
                except Exception:
                    continue

            return None
        finally:
            ole.close()

    @staticmethod
    def _parse_legacy_doc(file_path: Path) -> AttachmentParseResult:
        """레거시 .doc (OLE2) 파일에서 텍스트 추출 (antiword → catdoc → olefile fallback)"""
        import shutil
        import subprocess

        # 1차: antiword (테이블 구조 보존, Docker 환경)
        antiword_path = shutil.which("antiword")
        if antiword_path:
            try:
                result = subprocess.run(
                    [antiword_path, str(file_path)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    text = result.stdout.strip()
                    return AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.7,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )
            except Exception:
                pass

        # 2차: catdoc (antiword 없을 때, Docker 환경)
        catdoc_path = shutil.which("catdoc")
        if catdoc_path:
            try:
                result = subprocess.run(
                    [catdoc_path, "-w", str(file_path)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    text = result.stdout.strip()
                    return AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.6,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )
            except Exception:
                pass

        # 3차: olefile pure Python 추출 (로컬 환경, 시스템 도구 없을 때)
        text = AttachmentParser._extract_doc_olefile(file_path)
        if text:
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=0.5,
                native_text_chars=AttachmentParser._text_chars(text),
            )

        return AttachmentParseResult(
            extracted_text="[.doc 파싱 실패: antiword/catdoc 미설치 및 olefile 추출 실패]",
            extracted_tables=[],
            confidence=0.0,
            ocr_skip_reason="parse_error",
        )

    @staticmethod
    def parse_word(file_path: Path) -> AttachmentParseResult:
        """Word에서 텍스트와 테이블 추출 (.docx: python-docx, .doc: antiword/catdoc)"""
        try:
            if str(file_path).lower().endswith(".doc"):
                return AttachmentParser._parse_legacy_doc(file_path)
            from docx import Document

            doc = Document(file_path)
            text_parts = []
            tables = []

            # 문단 추출
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)

            # 테이블 추출
            for idx, table in enumerate(doc.tables, 1):
                rows_data = []
                for row in table.rows:
                    row_values = [cell.text.strip() for cell in row.cells]
                    rows_data.append(row_values)

                if rows_data:
                    headers = rows_data[0] if rows_data else []
                    data_rows = rows_data[1:] if len(rows_data) > 1 else []

                    tables.append({
                        "table_index": idx,
                        "headers": headers,
                        "rows": [dict(zip(headers, row)) for row in data_rows if len(row) == len(headers)],
                    })

            full_text = "\n\n".join(text_parts)
            confidence = 0.9 if full_text.strip() else 0.0

            return AttachmentParseResult(
                extracted_text=full_text,
                extracted_tables=tables,
                confidence=confidence,
                native_text_chars=AttachmentParser._text_chars(full_text),
            )

        except Exception as e:
            return AttachmentParseResult(
                extracted_text=f"[Word 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_skip_reason="parse_error",
            )

    @staticmethod
    def _extract_ppt_olefile(file_path: Path) -> str | None:
        """Pure Python .ppt 텍스트 추출 (OLE2 PowerPoint 레코드 파싱)"""
        try:
            import olefile
        except ImportError:
            return None

        import struct

        try:
            ole = olefile.OleFileIO(str(file_path))
        except Exception:
            return None

        try:
            if not ole.exists("PowerPoint Document"):
                return None

            raw = ole.openstream("PowerPoint Document").read()
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
                    # TextCharsAtom (0x0FA0): UTF-16LE 텍스트
                    if rec_type == 0x0FA0:
                        text = raw[data_start:data_end].decode("utf-16-le", errors="ignore").strip()
                        if text:
                            text_parts.append(text)
                    # TextBytesAtom (0x0FA8): ANSI 텍스트
                    elif rec_type == 0x0FA8:
                        data = raw[data_start:data_end]
                        try:
                            text = data.decode("cp949").strip()
                        except UnicodeDecodeError:
                            text = data.decode("cp1252", errors="ignore").strip()
                        if text:
                            text_parts.append(text)

                offset = data_end if rec_len > 0 else offset + 1

            return "\n\n".join(text_parts) if text_parts else None
        finally:
            ole.close()

    @staticmethod
    def _parse_legacy_ppt(file_path: Path, heartbeat_fn=None) -> AttachmentParseResult:
        """레거시 .ppt (OLE2) 파일에서 텍스트 추출

        Strategy:
        1. LibreOffice headless로 .ppt → .pptx 변환 → parse_ppt 재사용 (OCR 포함)
        2. Fallback: catppt → olefile 텍스트 추출
        """
        import shutil
        import subprocess
        import tempfile

        # 1차: LibreOffice .ppt → .pptx 변환 → parse_ppt (OCR 포함)
        from scripts.slide_renderer import _find_soffice

        soffice = _find_soffice()
        if soffice:
            try:
                with tempfile.TemporaryDirectory(prefix="ppt_convert_") as tmpdir:
                    lo_profile = os.path.join(tmpdir, "lo_profile")
                    os.makedirs(lo_profile, exist_ok=True)

                    result = subprocess.run(
                        [
                            soffice,
                            "--headless",
                            "--norestore",
                            f"-env:UserInstallation=file://{lo_profile}",
                            "--convert-to", "pptx",
                            "--outdir", tmpdir,
                            str(file_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if result.returncode == 0:
                        pptx_files = list(Path(tmpdir).glob("*.pptx"))
                        if pptx_files:
                            logger.info("[PPT] Converted %s -> %s", file_path.name, pptx_files[0].name)
                            return AttachmentParser.parse_ppt(
                                pptx_files[0], heartbeat_fn=heartbeat_fn,
                            )
                    logger.info("[PPT] LibreOffice conversion failed: %s", result.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.warning("[PPT] LibreOffice conversion timeout for %s", file_path.name)
            except Exception as e:
                logger.warning("[PPT] LibreOffice conversion error: %s", e)

        # 2차: catppt (Docker 환경)
        catppt_path = shutil.which("catppt")
        if catppt_path:
            try:
                result = subprocess.run(
                    [catppt_path, str(file_path)],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    text = result.stdout.strip()
                    return AttachmentParseResult(
                        extracted_text=text,
                        extracted_tables=[],
                        confidence=0.6,
                        native_text_chars=AttachmentParser._text_chars(text),
                    )
            except Exception:
                pass

        # 3차: olefile pure Python 추출 (로컬 환경)
        text = AttachmentParser._extract_ppt_olefile(file_path)
        if text:
            return AttachmentParseResult(
                extracted_text=text,
                extracted_tables=[],
                confidence=0.5,
                native_text_chars=AttachmentParser._text_chars(text),
            )

        return AttachmentParseResult(
            extracted_text="[.ppt 파싱 실패: LibreOffice/catppt/olefile 모두 실패]",
            extracted_tables=[],
            confidence=0.0,
            ocr_mode=AttachmentParser.current_policy().attachment_ocr_mode,
            ocr_skip_reason="parse_error",
        )

    @staticmethod
    def _iter_shapes(shapes):
        """GroupShape 포함 재귀 shape 탐색."""
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from AttachmentParser._iter_shapes(shape.shapes)

    @classmethod
    def parse_ppt(cls, file_path: Path, heartbeat_fn=None) -> AttachmentParseResult:
        """PPT에서 슬라이드 텍스트 추출 (.pptx: python-pptx, .ppt: catppt)

        Args:
            file_path: PPT/PPTX 파일 경로
            heartbeat_fn: Temporal heartbeat 콜백 (thread-safe, OCR 중 호출)
        """
        try:
            if str(file_path).lower().endswith(".ppt"):
                return cls._parse_legacy_ppt(file_path, heartbeat_fn=heartbeat_fn)
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE

            policy = cls.current_policy()
            prs = Presentation(file_path)
            text_parts = []
            tables = []
            image_shapes = []  # (slide_num, shape) tuples for deferred processing

            for slide_num, slide in enumerate(prs.slides, 1):
                slide_texts = []

                for shape in AttachmentParser._iter_shapes(slide.shapes):
                    # 텍스트 프레임
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_texts.append(shape.text)

                    # 테이블
                    if shape.has_table:
                        table = shape.table
                        rows_data = []
                        for row in table.rows:
                            row_values = [cell.text.strip() for cell in row.cells]
                            rows_data.append(row_values)

                        if rows_data:
                            headers = rows_data[0] if rows_data else []
                            data_rows = rows_data[1:] if len(rows_data) > 1 else []
                            tables.append({
                                "slide": slide_num,
                                "headers": headers,
                                "rows": [dict(zip(headers, row)) for row in data_rows if len(row) == len(headers)],
                            })

                    # 이미지 shape 수집 (OCR 처리 대상)
                    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        try:
                            image_bytes = shape.image.blob
                            if len(image_bytes) > 10_000:  # 10KB 이상만
                                image_shapes.append((slide_num, image_bytes))
                        except Exception:
                            pass

                # 슬라이드 노트 추출
                if slide.has_notes_slide:
                    notes_text = slide.notes_slide.notes_text_frame.text.strip()
                    if notes_text:
                        slide_texts.append(f"[Notes] {notes_text}")

                if slide_texts:
                    text_parts.append(f"[Slide {slide_num}]\n" + "\n".join(slide_texts))

            native_text = "\n\n".join(text_parts)
            native_text_chars = cls._text_chars(native_text)
            cls._emit_status(
                heartbeat_fn,
                f"native_extract ppt slides={len(prs.slides)} chars={native_text_chars}",
            )

            should_ocr = policy.attachment_ocr_mode == "force" or (
                policy.attachment_ocr_mode == "auto"
                and native_text_chars < policy.ocr_min_text_chars
            )
            if policy.attachment_ocr_mode == "off":
                should_ocr = False

            # ============================================================
            # Phase 1: Slide rendering (LibreOffice PPTX → PNG)
            # ============================================================
            slide_rendered = False
            try:
                from src.core.feature_flags import FeatureFlags
                ocr_preprocess_enabled = FeatureFlags.is_knowledge_ocr_preprocess_enabled()
                ocr_postprocess_enabled = FeatureFlags.is_knowledge_ocr_postprocess_enabled()
            except ImportError:
                ocr_preprocess_enabled = os.getenv("KNOWLEDGE_OCR_PREPROCESS_ENABLED", "true").lower() == "true"
                ocr_postprocess_enabled = os.getenv("KNOWLEDGE_OCR_POSTPROCESS_ENABLED", "true").lower() == "true"

            ocr_units_attempted = 0
            ocr_units_extracted = 0
            ocr_units_deferred = 0
            ocr_text_chars = 0
            extracted_slides: set[int] = set()

            if should_ocr and policy.slide_render_enabled and file_path:
                try:
                    from scripts.slide_renderer import render_slides_as_images

                    rendered_slides = render_slides_as_images(Path(str(file_path)))
                    if rendered_slides:
                        slide_rendered = True
                        logger.info("[OCR] Slide rendering: %d slides from %s", len(rendered_slides), file_path)
                        for slide_num, png_bytes in rendered_slides:
                            if ocr_units_attempted >= policy.ocr_max_ppt_slides:
                                ocr_units_deferred += 1
                                continue
                            ocr_units_attempted += 1
                            cls._emit_status(
                                heartbeat_fn,
                                f"ocr_processing ppt slide={slide_num}/{len(rendered_slides)}",
                            )
                            ocr_text = cls._ocr_slide_image(
                                png_bytes, slide_num,
                                preprocess=ocr_preprocess_enabled,
                                layout_analysis=policy.layout_analysis_enabled,
                                postprocess=ocr_postprocess_enabled,
                            )
                            if ocr_text:
                                text_parts.append(f"[Slide {slide_num} OCR]\n{ocr_text}")
                                ocr_units_extracted += 1
                                ocr_text_chars += cls._text_chars(ocr_text)
                                extracted_slides.add(slide_num)
                            if heartbeat_fn and slide_num % 5 == 0:
                                heartbeat_fn(f"slide_render_ocr: {slide_num}/{len(rendered_slides)}")
                except Exception as render_err:
                    logger.warning("[OCR] Slide rendering failed, falling back to shape OCR: %s", render_err)
                    slide_rendered = False

            # ============================================================
            # Fallback: Shape-by-shape OCR (original path)
            # ============================================================
            if should_ocr and not slide_rendered:
                timed_out_images = []  # (slide_num, png_bytes) — timeout된 이미지 재시도용
                ocr_processed = 0
                ocr_total = len(image_shapes)
                attempted_slides: set[int] = set()
                for slide_num, image_bytes in image_shapes:
                    if slide_num not in attempted_slides and len(attempted_slides) >= policy.ocr_max_ppt_slides:
                        ocr_units_deferred += 1
                        continue
                    try:
                        ocr = cls._get_ocr_instance()
                        if ocr is None:
                            break
                        from PIL import Image
                        img = Image.open(io.BytesIO(image_bytes))
                        img = cls._resize_image_if_needed(img)
                        if img is None:
                            continue  # 극단적 종횡비/너무 작은 이미지 스킵
                        if slide_num not in attempted_slides:
                            attempted_slides.add(slide_num)
                            ocr_units_attempted += 1
                            cls._emit_status(
                                heartbeat_fn,
                                f"ocr_processing ppt slide={slide_num}/{len(prs.slides)}",
                            )

                        # Keep original color image for layout analysis (PP-Structure
                        # uses color cues for table/figure boundary detection)
                        img_original = img.copy()
                        if img_original.mode != "RGB":
                            img_original = img_original.convert("RGB")

                        # Phase 2: Image preprocessing (for standard OCR path)
                        if ocr_preprocess_enabled:
                            try:
                                from scripts.ocr_preprocessor import preprocess_for_ocr
                                img = preprocess_for_ocr(img, mode="auto")
                            except Exception as preproc_err:
                                logger.warning("[OCR] Preprocess failed, using original: %s", preproc_err)
                                if img.mode != "RGB":
                                    img = img.convert("RGB")
                        else:
                            if img.mode != "RGB":
                                img = img.convert("RGB")

                        img_buffer = io.BytesIO()
                        img.save(img_buffer, format="PNG")
                        png_bytes = img_buffer.getvalue()

                        # Phase 2: Layout analysis (PP-Structure) for shape images
                        # Uses ORIGINAL color image (not preprocessed) for better region detection
                        ocr_text = None
                        ocr_conf = 0.0
                        if policy.layout_analysis_enabled:
                            try:
                                from scripts.ocr_preprocessor import analyze_layout_and_ocr
                                regions = analyze_layout_and_ocr(img_original)
                                if regions:
                                    ocr_text = "\n".join(r["content"] for r in regions if r.get("content"))
                                    ocr_conf = 0.7  # Layout-based OCR default confidence
                            except Exception:
                                pass  # Fall through to standard OCR

                        if not ocr_text:
                            # subprocess 격리로 OCR 실행 (SIGSEGV가 메인 프로세스를 죽이지 않도록)
                            with AttachmentParser._ocr_lock:
                                ocr_text, ocr_conf, _ = AttachmentParser._ocr_extract_safe(
                                    png_bytes, f"slide_{slide_num}"
                                )

                        # Phase 3: Post-processing
                        if ocr_text and ocr_postprocess_enabled:
                            try:
                                from scripts.ocr_postprocessor import postprocess_ocr_text
                                ocr_text, ocr_conf = postprocess_ocr_text(ocr_text, ocr_conf)
                            except Exception as postproc_err:
                                logger.warning("[OCR] Postprocess failed: %s", postproc_err)

                        if ocr_text and ocr_conf > 0.3:
                            text_parts.append(f"[Slide {slide_num} Image OCR]\n{ocr_text}")
                            if slide_num not in extracted_slides:
                                extracted_slides.add(slide_num)
                                ocr_units_extracted += 1
                            ocr_text_chars += cls._text_chars(ocr_text)
                        elif ocr_text is None:
                            # timeout 또는 SIGSEGV — 재시도 대상
                            timed_out_images.append((slide_num, png_bytes))
                        # Temporal heartbeat (thread-safe) — 10장마다 전송
                        ocr_processed += 1
                        if heartbeat_fn and ocr_processed % 10 == 0:
                            heartbeat_fn(f"ocr: {ocr_processed}/{ocr_total} images, slide_{slide_num}")
                    except Exception as ocr_err:
                        logger.warning("[OCR Warning] Slide %d image: %s", slide_num, ocr_err)

                # timeout된 이미지 순차 재시도 (동시 경합 없이 단독 실행)
                if timed_out_images:
                    logger.info("[OCR Retry] %d timed-out images, retrying sequentially...", len(timed_out_images))
                    if heartbeat_fn:
                        heartbeat_fn(f"ocr_retry: {len(timed_out_images)} images to retry")
                    for slide_num, png_bytes in timed_out_images:
                        if slide_num not in attempted_slides and len(attempted_slides) >= policy.ocr_max_ppt_slides:
                            ocr_units_deferred += 1
                            continue
                        try:
                            if slide_num not in attempted_slides:
                                attempted_slides.add(slide_num)
                                ocr_units_attempted += 1
                            with AttachmentParser._ocr_lock:
                                ocr_text, ocr_conf, _ = AttachmentParser._ocr_extract_safe(
                                    png_bytes, f"retry_slide_{slide_num}"
                                )
                            # Post-processing on retry
                            if ocr_text and ocr_postprocess_enabled:
                                try:
                                    from scripts.ocr_postprocessor import postprocess_ocr_text
                                    ocr_text, ocr_conf = postprocess_ocr_text(ocr_text, ocr_conf)
                                except Exception:
                                    pass
                            if ocr_text and ocr_conf > 0.3:
                                text_parts.append(f"[Slide {slide_num} Image OCR]\n{ocr_text}")
                                if slide_num not in extracted_slides:
                                    extracted_slides.add(slide_num)
                                    ocr_units_extracted += 1
                                ocr_text_chars += cls._text_chars(ocr_text)
                                logger.info("[OCR Retry] slide_%d: OK (%d chars)", slide_num, len(ocr_text))
                            else:
                                logger.info("[OCR Retry] slide_%d: still failed", slide_num)
                        except Exception as retry_err:
                            logger.warning("[OCR Retry] slide_%d: error - %s", slide_num, retry_err)

            full_text = "\n\n".join(text_parts)

            # ============================================================
            # Final fallback: PPTX → PDF → image OCR
            # If both text extraction and slide rendering produced nothing
            # (e.g., PPTX with embedded images that python-pptx can't read,
            # or LibreOffice rendering failed), convert PPTX to PDF and use
            # the PDF image OCR pipeline as last resort.
            # ============================================================
            if should_ocr and (not full_text.strip() or len(full_text.strip()) < 50):
                logger.info("[PPT] Empty result after all extraction (%d chars), trying PDF fallback", len(full_text))
                try:
                    import subprocess
                    import tempfile

                    from scripts.slide_renderer import _find_soffice

                    soffice = _find_soffice()
                    if soffice:
                        with tempfile.TemporaryDirectory(prefix="pptx_pdf_fallback_") as tmpdir:
                            lo_profile = os.path.join(tmpdir, "lo_profile")
                            os.makedirs(lo_profile, exist_ok=True)

                            result = subprocess.run(
                                [
                                    soffice, "--headless", "--norestore",
                                    f"-env:UserInstallation=file://{lo_profile}",
                                    "--convert-to", "pdf",
                                    "--outdir", tmpdir,
                                    str(file_path),
                                ],
                                capture_output=True, text=True, timeout=120,
                            )
                            if result.returncode == 0:
                                pdf_files = list(Path(tmpdir).glob("*.pdf"))
                                if pdf_files:
                                    logger.info("[PPT] PDF fallback: converted %s -> %s", file_path.name, pdf_files[0].name)
                                    pdf_result = cls.parse_pdf(
                                        pdf_files[0], heartbeat_fn=heartbeat_fn,
                                    )
                                    if pdf_result.extracted_text.strip() and len(pdf_result.extracted_text.strip()) > len(full_text.strip()):
                                        full_text = pdf_result.extracted_text
                                        tables = pdf_result.extracted_tables or tables
                                        ocr_text_chars = max(ocr_text_chars, pdf_result.ocr_text_chars)
                                        logger.info("[PPT] PDF fallback produced %d chars", len(full_text))
                except Exception as fb_err:
                    logger.warning("[PPT] PDF fallback failed: %s", fb_err)

            if ocr_units_deferred > 0:
                cls._emit_status(
                    heartbeat_fn,
                    f"ocr_skipped_budget ppt deferred={ocr_units_deferred}",
                )
            confidence = 0.85 if full_text.strip() else 0.0

            skip_reason = None
            if policy.attachment_ocr_mode == "off":
                skip_reason = "disabled"
            elif policy.attachment_ocr_mode == "auto" and not should_ocr:
                skip_reason = "native_text_sufficient"
            elif ocr_units_deferred > 0:
                skip_reason = "budget_exceeded"

            return AttachmentParseResult(
                extracted_text=full_text,
                extracted_tables=tables,
                confidence=confidence,
                ocr_mode=policy.attachment_ocr_mode,
                ocr_applied=ocr_units_extracted > 0,
                ocr_skip_reason=skip_reason,
                ocr_units_attempted=ocr_units_attempted,
                ocr_units_extracted=ocr_units_extracted,
                ocr_units_deferred=ocr_units_deferred,
                native_text_chars=native_text_chars,
                ocr_text_chars=ocr_text_chars,
            )

        except Exception as e:
            return AttachmentParseResult(
                extracted_text=f"[PPT 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_mode=cls.current_policy().attachment_ocr_mode,
                ocr_skip_reason="parse_error",
            )

    # 싱글톤 OCR 인스턴스 (메모리 효율화)
    _ocr_instance = None
    _ocr_type = None  # "paddle" only (amd64 Crawler Pod)
    _ocr_lock = __import__("threading").Lock()  # PaddleOCR는 thread-safe 보장 없음

    @classmethod
    def _get_ocr_instance(cls):
        """싱글톤 PaddleOCR 인스턴스 반환 (amd64 only, no fallback)."""
        if cls._ocr_instance is None:
            try:
                from src.ocr.paddle_ocr_provider import PaddleOCRProvider
                cls._ocr_instance = PaddleOCRProvider()
                cls._ocr_type = "paddle"
                logger.info("[OCR] PaddleOCR singleton created")
            except ImportError:
                logger.warning("[OCR] PaddleOCR not available (requires amd64)")
                return None
        return cls._ocr_instance

    @classmethod
    def cleanup_ocr(cls):
        """OCR 인스턴스 정리 및 메모리 해제."""
        import gc
        cls._ocr_instance = None
        cls._ocr_type = None
        # Shutdown subprocess pool if active
        if cls._ocr_process_pool is not None:
            try:
                cls._ocr_process_pool.shutdown(wait=False)
            except Exception:
                pass
            cls._ocr_process_pool = None
        gc.collect()
        logger.info("[OCR] 메모리 정리 완료")

    # --- Subprocess-isolated OCR (SIGSEGV defense) ---
    _ocr_process_pool = None
    _ocr_pool_lock = __import__("threading").Lock()

    @classmethod
    def _ocr_extract_safe(
        cls, image_bytes: bytes, file_name: str = "", timeout: int = 1800
    ) -> tuple[str | None, float, list]:
        """Execute OCR in a forked subprocess to survive PaddleOCR SIGSEGV.

        PaddleOCR's C++ inference engine can crash with SIGSEGV on certain
        images. Python try/except cannot catch OS signals (signal 11).
        By running OCR in a forked subprocess via ProcessPoolExecutor:
        - SIGSEGV kills only the child process
        - Parent detects BrokenProcessPool, recreates pool, skips image
        - Fork inherits loaded model via COW (no ~30s model reload)
        """
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        import multiprocessing as mp

        with cls._ocr_pool_lock:
            if cls._ocr_process_pool is None:
                ctx = mp.get_context("fork")
                cls._ocr_process_pool = ProcessPoolExecutor(
                    max_workers=1, mp_context=ctx
                )
            pool = cls._ocr_process_pool

        try:
            future = pool.submit(_ocr_worker_fn, image_bytes)
            return future.result(timeout=timeout)
        except BrokenProcessPool:
            logger.error(
                "[OCR SIGSEGV] Worker crashed on %s — restarting pool, skipping image",
                file_name,
            )
            with cls._ocr_pool_lock:
                cls._ocr_process_pool = None
            return None, 0.0, []
        except TimeoutError:
            logger.warning("[OCR Timeout] %s exceeded %ds — skipped", file_name, timeout)
            return None, 0.0, []
        except Exception as e:
            logger.warning("[OCR Error] %s: %s — skipped", file_name, e)
            return None, 0.0, []

    # PaddleOCR PP-OCRv5 det 모델이 극단적 종횡비에서 SIGSEGV 발생
    _OCR_MIN_DIMENSION = 32  # 최소 폭/높이 (px)
    _OCR_MAX_ASPECT_RATIO = 8.0  # 최대 종횡비

    @staticmethod
    def _resize_image_if_needed(img, max_size: int = 2048):
        """큰 이미지 리사이즈 (메모리 최적화).

        Args:
            img: PIL Image 객체
            max_size: 최대 폭/높이 (기본: 2048px)

        Returns:
            리사이즈된 이미지 (또는 원본).
            극단적 종횡비(>8:1)나 너무 작은(<32px) 이미지는 None 반환.
        """
        width, height = img.size

        # 원본이 이미 너무 작으면 OCR 무의미
        if width < AttachmentParser._OCR_MIN_DIMENSION or height < AttachmentParser._OCR_MIN_DIMENSION:
            logger.debug("[OCR] 이미지 스킵 (너무 작음): %dx%d", width, height)
            return None

        if width > max_size or height > max_size:
            ratio = min(max_size / width, max_size / height)
            new_size = (int(width * ratio), int(height * ratio))

            # 리사이즈 후 최소 크기 검증
            if new_size[0] < AttachmentParser._OCR_MIN_DIMENSION or new_size[1] < AttachmentParser._OCR_MIN_DIMENSION:
                logger.debug("[OCR] 이미지 스킵 (리사이즈 후 너무 작음): %dx%d -> %dx%d", width, height, new_size[0], new_size[1])
                return None

            img = img.resize(new_size, resample=3)  # Pillow LANCZOS = 3
            logger.debug("[OCR] 이미지 리사이즈: %dx%d -> %dx%d", width, height, new_size[0], new_size[1])

        # 종횡비 검증 (리사이즈 후 기준) — 극단적이면 패딩으로 보정
        w, h = img.size
        aspect = max(w, h) / max(min(w, h), 1)
        if aspect > AttachmentParser._OCR_MAX_ASPECT_RATIO:
            # 짧은 쪽에 흰색 패딩 추가하여 8:1 이내로 보정
            target_short = max(w, h) // int(AttachmentParser._OCR_MAX_ASPECT_RATIO)
            from PIL import Image as _PilImage
            if w > h:
                padded = _PilImage.new("RGB", (w, target_short), (255, 255, 255))
                padded.paste(img, (0, (target_short - h) // 2))
            else:
                padded = _PilImage.new("RGB", (target_short, h), (255, 255, 255))
                padded.paste(img, ((target_short - w) // 2, 0))
            logger.debug("[OCR] 이미지 패딩 (종횡비 %.1f:1 -> 8:1): %dx%d -> %dx%d", aspect, w, h, padded.size[0], padded.size[1])
            return padded

        return img

    @classmethod
    def _ocr_slide_image(
        cls,
        png_bytes: bytes,
        slide_num: int,
        preprocess: bool = True,
        layout_analysis: bool = True,
        postprocess: bool = True,
    ) -> str | None:
        """OCR a rendered slide image with preprocessing and layout analysis.

        Args:
            png_bytes: PNG image bytes of the rendered slide.
            slide_num: Slide number (for logging).
            preprocess: Apply OpenCV preprocessing.
            layout_analysis: Use PP-Structure layout analysis.
            postprocess: Apply OCR text post-processing.

        Returns:
            Extracted text or None if OCR fails.
        """
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(png_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Keep original color image for layout analysis (PP-Structure
            # uses color cues for region boundary detection)
            img_original = img.copy()

            # Phase 2a: Image preprocessing (for standard OCR fallback path)
            if preprocess:
                try:
                    from scripts.ocr_preprocessor import preprocess_for_ocr
                    img = preprocess_for_ocr(img, mode="slide")
                except Exception as e:
                    logger.warning("[OCR] Slide %d preprocess failed: %s", slide_num, e)

            # Phase 2b: Layout analysis (PP-Structure) — uses ORIGINAL color image
            ocr_text = None
            if layout_analysis:
                try:
                    from scripts.ocr_preprocessor import analyze_layout_and_ocr
                    regions = analyze_layout_and_ocr(img_original)
                    if regions:
                        ocr_text = "\n".join(r["content"] for r in regions if r.get("content"))
                        logger.info("[OCR] Slide %d: layout analysis found %d regions", slide_num, len(regions))
                except Exception as e:
                    logger.warning("[OCR] Slide %d layout analysis failed: %s", slide_num, e)

            # Fallback: standard OCR on preprocessed image
            if not ocr_text:
                img_buffer = io.BytesIO()
                img.save(img_buffer, format="PNG")
                with cls._ocr_lock:
                    ocr_text, ocr_conf, _ = cls._ocr_extract_safe(
                        img_buffer.getvalue(), f"rendered_slide_{slide_num}"
                    )
                if not ocr_text or ocr_conf <= 0.3:
                    return None

            # Phase 3: Post-processing
            if ocr_text and postprocess:
                try:
                    from scripts.ocr_postprocessor import postprocess_ocr_text
                    ocr_text, _ = postprocess_ocr_text(ocr_text)
                except Exception as e:
                    logger.warning("[OCR] Slide %d postprocess failed: %s", slide_num, e)

            # Phase 4: OCR noise filter — remove lines with repeated characters
            # (e.g., "폐폐폐폐폐" from low-quality PDF OCR)
            if ocr_text:
                clean_lines = []
                for line in ocr_text.split("\n"):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Detect repeated single-char patterns (e.g., "폐폐폐폐폐")
                    if len(stripped) >= 3:
                        unique_chars = set(stripped.replace(" ", ""))
                        if len(unique_chars) <= 2 and len(stripped) >= 5:
                            continue  # Skip noise line (1-2 unique chars repeated)
                    clean_lines.append(line)
                ocr_text = "\n".join(clean_lines)

            return ocr_text if ocr_text and ocr_text.strip() else None

        except Exception as e:
            logger.error("[OCR] Slide %d OCR error: %s", slide_num, e)
            return None

    @classmethod
    def _parse_image_sync(
        cls,
        file_path: Path,
        content: bytes,
        use_ocr: bool = True,
    ) -> AttachmentParseResult:
        """이미지 OCR 및 메타데이터 추출 (동기 내부 구현).

        PaddleOCR(한국어 특화)을 사용하여 텍스트와 표를 추출합니다.
        싱글톤 패턴으로 OCR 인스턴스를 재사용하여 메모리를 최적화합니다.
        """
        try:
            policy = cls.current_policy()
            from PIL import Image
            import gc

            img = Image.open(io.BytesIO(content))
            width, height = img.size
            format_type = img.format or "unknown"

            # 이미지 메타데이터
            metadata_text = f"[Image: {width}x{height}, {format_type}, {len(content):,} bytes]"
            tables = []
            confidence = 0.5  # 기본값

            if policy.attachment_ocr_mode == "off" or not use_ocr:
                return AttachmentParseResult(
                    extracted_text=metadata_text,
                    extracted_tables=tables,
                    confidence=confidence,
                    ocr_mode=policy.attachment_ocr_mode,
                    ocr_skip_reason="disabled",
                )

            if policy.ocr_max_images_per_attachment <= 0:
                cls._emit_status(None, "ocr_skipped_budget image deferred=1")
                return AttachmentParseResult(
                    extracted_text=metadata_text,
                    extracted_tables=tables,
                    confidence=confidence,
                    ocr_mode=policy.attachment_ocr_mode,
                    ocr_skip_reason="budget_exceeded",
                    ocr_units_deferred=1,
                )

            # OCR 수행 조건: 10MB 미만 && use_ocr 플래그
            if len(content) < 10_000_000:
                try:
                    cls._emit_status(None, f"ocr_processing image file={file_path.name}")
                    # 싱글톤 OCR 인스턴스 사용 (메모리 최적화)
                    ocr = cls._get_ocr_instance()
                    if ocr is None:
                        return AttachmentParseResult(
                            extracted_text=metadata_text,
                            extracted_tables=tables,
                            confidence=confidence,
                            ocr_mode=policy.attachment_ocr_mode,
                            ocr_skip_reason="ocr_unavailable",
                        )

                    # 큰 이미지 리사이즈 (메모리 최적화 + 극단적 종횡비 방어)
                    img = cls._resize_image_if_needed(img)
                    if img is None:
                        return AttachmentParseResult(
                            extracted_text=metadata_text,
                            extracted_tables=tables,
                            confidence=confidence,
                            ocr_mode=policy.attachment_ocr_mode,
                            ocr_skip_reason="guard_rejected",
                        )

                    # RGB 변환 및 바이트 변환
                    if img.mode != "RGB":
                        img = img.convert("RGB")

                    # 리사이즈된 이미지를 바이트로 변환
                    img_buffer = io.BytesIO()
                    img.save(img_buffer, format="PNG")
                    resized_content = img_buffer.getvalue()

                    # OCR 실행 (subprocess isolation: PaddleOCR SIGSEGV 방어)
                    # SIGSEGV는 try/except로 잡을 수 없음 → 별도 프로세스에서 실행
                    with cls._ocr_lock:
                        ocr_text, ocr_conf, ocr_tables = cls._ocr_extract_safe(
                            resized_content, file_path.name
                        )

                    if ocr_text and ocr_conf > 0.3:
                        # OCR 성공
                        extracted_text = ocr_text
                        confidence = ocr_conf
                        tables = ocr_tables

                        # 메타데이터 + OCR 텍스트
                        full_text = f"{metadata_text}\n\n{extracted_text}"

                        # 메모리 정리 (이미지별)
                        del img, img_buffer, resized_content
                        gc.collect()

                        return AttachmentParseResult(
                            extracted_text=full_text,
                            extracted_tables=tables,
                            confidence=confidence,
                            ocr_mode=policy.attachment_ocr_mode,
                            ocr_applied=True,
                            ocr_units_attempted=1,
                            ocr_units_extracted=1,
                            ocr_text_chars=cls._text_chars(extracted_text),
                        )

                except Exception as ocr_error:
                    # OCR 실패 시 로그만 남기고 메타데이터 반환
                    logger.warning("[OCR Warning] %s: %s", file_path.name, ocr_error)

            # OCR 실패 또는 미수행 시 메타데이터만 반환
            return AttachmentParseResult(
                extracted_text=metadata_text,
                extracted_tables=tables,
                confidence=confidence,
                ocr_mode=policy.attachment_ocr_mode,
                ocr_skip_reason="ocr_failed" if len(content) < 10_000_000 else "image_too_large",
                ocr_units_attempted=1 if len(content) < 10_000_000 else 0,
            )

        except Exception as e:
            return AttachmentParseResult(
                extracted_text=f"[이미지 파싱 오류: {e}]",
                extracted_tables=[],
                confidence=0.0,
                ocr_mode=cls.current_policy().attachment_ocr_mode,
                ocr_skip_reason="parse_error",
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
        """이미지 OCR 비동기 처리.

        CPU-bound OCR을 asyncio.to_thread로 오프로드하여
        다른 첨부파일 다운로드와 병렬 처리합니다.
        OMP_NUM_THREADS=2 설정으로 스레드당 2코어만 사용하여
        동시 여러 OCR 처리가 가능합니다.
        """
        return await asyncio.to_thread(cls._parse_image_sync, file_path, content, use_ocr)
