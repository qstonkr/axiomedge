"""Confluence crawler configuration.

Extracts environment variables, knowledge source definitions, and OCR defaults
from the original ``confluence_full_crawler.py`` module-level code (lines 60-232).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AttachmentOCRPolicy  # noqa: F401 – re-export for convenience

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# .env.local auto-loader
# ---------------------------------------------------------------------------

_env_local = Path(__file__).resolve().parents[3] / ".env.local"
if _env_local.exists():
    with open(_env_local) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                if _key and _key not in os.environ:
                    os.environ[_key] = _val


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Knowledge-source loader
# ---------------------------------------------------------------------------

def _load_knowledge_sources() -> dict[str, dict[str, str]]:
    """환경변수에서 지식 소스 로드 (KNOWLEDGE_SOURCES_JSON 또는 기본값 사용)"""
    sources_json = os.getenv("KNOWLEDGE_SOURCES_JSON")
    if sources_json:
        try:
            return json.loads(sources_json)
        except json.JSONDecodeError as e:
            logger.warning("KNOWLEDGE_SOURCES_JSON 파싱 실패: %s — 기본 지식 소스를 사용합니다.", e)

    # 기본 지식 소스 (환경변수 미설정 시)
    # space_key는 API 응답에서 자동으로 가져옴
    sources: dict[str, dict[str, str]] = {}
    infra_pid = os.getenv("KNOWLEDGE_SOURCE_INFRA_PAGE_ID", "312765276")
    if infra_pid:
        sources["infra"] = {
            "page_id": infra_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_INFRA_NAME", "infra"),
        }
    hs_pid = os.getenv("KNOWLEDGE_SOURCE_HS_PAGE_ID")
    if hs_pid:
        sources["homeshopping"] = {
            "page_id": hs_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_HS_NAME", "homeshopping"),
        }
    # AX본부 KB sources (small Confluence spaces)
    faq_pid = os.getenv("KNOWLEDGE_SOURCE_FAQ_PAGE_ID", "388722996")
    if faq_pid:
        sources["faq"] = {
            "page_id": faq_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_FAQ_NAME", "AX FAQ"),
        }
    system_pid = os.getenv("KNOWLEDGE_SOURCE_SYSTEM_PAGE_ID", "388722906")
    if system_pid:
        sources["system"] = {
            "page_id": system_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_SYSTEM_NAME", "AX 시스템별상세정보"),
        }
    dictionary_pid = os.getenv("KNOWLEDGE_SOURCE_DICTIONARY_PAGE_ID", "388722934")
    if dictionary_pid:
        sources["dictionary"] = {
            "page_id": dictionary_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_DICTIONARY_NAME", "AX 용어사전"),
        }
    # AX챗봇 지식관리
    axchat_pid = os.getenv("KNOWLEDGE_SOURCE_AXCHAT_PAGE_ID", "373865276")
    if axchat_pid:
        sources["axchat_know"] = {
            "page_id": axchat_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_AXCHAT_NAME", "AX챗봇 지식관리"),
        }
    # Large Confluence sources
    hax_pid = os.getenv("KNOWLEDGE_SOURCE_HAX_PAGE_ID", "6685934")
    if hax_pid:
        sources["hax"] = {
            "page_id": hax_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_HAX_NAME", "홈쇼핑AX부문 업무 위키"),
        }
    itops_pid = os.getenv("KNOWLEDGE_SOURCE_ITOPS_PAGE_ID", "14225186")
    if itops_pid:
        sources["itops"] = {
            "page_id": itops_pid,
            "name": os.getenv("KNOWLEDGE_SOURCE_ITOPS_NAME", "홈쇼핑 IT운영 업무 가이드"),
        }
    if not sources:
        logger.warning(
            "지식 소스가 설정되지 않았습니다. "
            ".env.local에 KNOWLEDGE_SOURCE_INFRA_PAGE_ID 등을 설정하세요."
        )
        raise ValueError("지식 소스가 설정되지 않았습니다.")
    return sources


# ---------------------------------------------------------------------------
# Output directory resolver
# ---------------------------------------------------------------------------

def _resolve_output_dir() -> Path:
    """Resolve crawl output directory with safe fallback for local execution."""
    candidates: list[tuple[Path, str]] = []
    configured_dir = os.getenv("CONFLUENCE_OUTPUT_DIR", "").strip()
    if configured_dir:
        candidates.append((Path(configured_dir), "CONFLUENCE_OUTPUT_DIR"))
    fallback_dir = Path.home() / ".oreo" / "crawl"
    candidates.append((fallback_dir, "home fallback"))

    last_error: Exception | None = None
    for output_dir, label in candidates:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir
        except OSError as exc:
            last_error = exc
            if label == "CONFLUENCE_OUTPUT_DIR":
                logger.warning(
                    "CONFLUENCE_OUTPUT_DIR '%s' is not writable; fallback to %s",
                    output_dir,
                    fallback_dir,
                )
            else:
                logger.warning(
                    "Failed to create fallback output dir '%s': %s",
                    output_dir,
                    exc,
                )

    if last_error is not None:
        raise RuntimeError(
            "Unable to resolve writable CONFLUENCE_OUTPUT_DIR"
        ) from last_error
    return fallback_dir


# ---------------------------------------------------------------------------
# OCR defaults
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CrawlerConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class CrawlerConfig:
    """Immutable bag of resolved configuration for a Confluence crawl run."""

    base_url: str
    pat: str
    output_dir: Path
    attachments_dir: Path
    knowledge_sources: dict[str, dict[str, str]]

    @classmethod
    def from_env(cls) -> "CrawlerConfig":
        """Build a ``CrawlerConfig`` from the current environment variables.

        Raises
        ------
        ValueError
            If ``CONFLUENCE_PAT`` is missing or no knowledge sources are defined.
        """
        base_url = os.getenv("CONFLUENCE_BASE_URL", "https://wiki.gsretail.com")
        pat = os.getenv("CONFLUENCE_PAT")
        if not pat:
            logger.warning(
                "CONFLUENCE_PAT 환경변수가 설정되지 않았습니다. "
                "export CONFLUENCE_PAT='your-personal-access-token'"
            )
            raise ValueError("CONFLUENCE_PAT 환경변수가 설정되지 않았습니다.")

        knowledge_sources = _load_knowledge_sources()

        output_dir = _resolve_output_dir()
        attachments_dir = output_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            base_url=base_url,
            pat=pat,
            output_dir=output_dir,
            attachments_dir=attachments_dir,
            knowledge_sources=knowledge_sources,
        )
