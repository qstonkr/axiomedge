"""Quality Processor.

문서 품질 필터링 및 정제를 수행하는 모듈.

Extracted from oreo-ecosystem quality_processor.py.
All oreo-specific imports (StatsD, feature flags) removed.
Core quality tier logic preserved exactly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..config.weights import weights as _w

logger = logging.getLogger(__name__)


class QualityTier(str, Enum):
    """품질 등급."""

    GOLD = "GOLD"      # 최고 품질: 2000자+, 구조화
    SILVER = "SILVER"  # 좋은 품질: 500자+, 테이블/코드
    BRONZE = "BRONZE"  # 기본 품질: 50-500자
    NOISE = "NOISE"    # 노이즈: 50자 미만


@dataclass
class QualityMetrics:
    """품질 메트릭."""

    content_length: int
    has_tables: bool
    has_code_blocks: bool
    has_headers: bool
    has_images: bool
    has_links: bool
    word_count: int
    paragraph_count: int


@dataclass
class ProcessedDocument:
    """처리된 문서."""

    page_id: str
    title: str
    content_text: str
    quality_tier: QualityTier
    metrics: QualityMetrics
    is_stale: bool
    freshness_warning: str | None
    days_since_update: int | None
    creators: list[str]
    modifiers: list[str]
    metadata: dict[str, Any]

    # 최신성 상태 (current/stale/outdated)
    freshness: str = "unknown"

    # 첨부파일 포함 여부
    has_attachments: bool = False

    # 추적성 정보
    provenance_id: str | None = None
    lineage_id: str | None = None


# 제외할 소유자 패턴
SKIP_OWNER_PATTERNS = [
    r"^Unknown$",
    r"^User$",
    r"^admin$",
    r"AI센터",
    r"MC\s*Front",
    r"^hwlee$",
    r"APP$",
    r"팀$",
    r"본부$",
    r"부문$",
    r"센터$",
    r"^T$",
]

_ATTACHMENT_MARKERS = ["[이미지:", "[PDF:", "[프레젠테이션:", "[스프레드시트:", "[문서:", "[첨부:"]


def _update_tier_stats(stats: dict[str, int], processed: Any) -> None:
    """Update tier/stale/attachment counts for a processed document."""
    stats["useful"] += 1

    if processed.quality_tier == QualityTier.GOLD:
        stats["gold"] += 1
    elif processed.quality_tier == QualityTier.SILVER:
        stats["silver"] += 1
    else:
        stats["bronze"] += 1

    if processed.is_stale:
        stats["stale"] += 1

    if "### [" in processed.content_text and any(
        marker in processed.content_text for marker in _ATTACHMENT_MARKERS
    ):
        stats["with_attachments"] += 1


def process_quality(
    crawl_data_list: list[dict[str, Any]],
    min_content_length: int = _w.quality.bronze_min_chars,
    stale_threshold_days: int = _w.quality.stale_threshold_days,
) -> dict[str, Any]:
    """크롤링 데이터 품질 처리.

    Args:
        crawl_data_list: 크롤링 결과 목록
        min_content_length: 최소 콘텐츠 길이
        stale_threshold_days: 오래된 문서 기준 일수

    Returns:
        처리 결과 {"documents": [...], "stats": {...}}
    """
    documents: list[dict[str, Any]] = []
    stats = {
        "total": 0,
        "useful": 0,
        "excluded_empty": 0,
        "excluded_noise": 0,
        "gold": 0,
        "silver": 0,
        "bronze": 0,
        "stale": 0,
        "with_attachments": 0,
    }

    for crawl_data in crawl_data_list:
        pages = crawl_data.get("pages", [])
        source_name = crawl_data.get("source_name", "unknown")

        for page in pages:
            stats["total"] += 1
            processed = _process_single_document(
                page, source_name, min_content_length, stale_threshold_days
            )

            if processed is None:
                stats["excluded_empty"] += 1
                continue

            if processed.quality_tier == QualityTier.NOISE:
                stats["excluded_noise"] += 1
                continue

            _update_tier_stats(stats, processed)
            documents.append(_to_dict(processed))

    logger.info(
        f"품질 처리 완료: 총 {stats['total']}개 -> "
        f"유용 {stats['useful']}개 (GOLD {stats['gold']}, "
        f"SILVER {stats['silver']}, BRONZE {stats['bronze']}), "
        f"오래됨 {stats['stale']}개, "
        f"첨부파일 포함 {stats['with_attachments']}개"
    )

    return {
        "documents": documents,
        "stats": stats,
    }


def _attachment_header(filename: str, file_type: str) -> str:
    """Return a markdown section header based on file type."""
    fl = filename.lower()
    if "image" in file_type.lower() or fl.endswith((".png", ".jpg", ".jpeg", ".gif")):
        return f"\n\n### [이미지: {filename}]\n"
    if fl.endswith((".pdf",)):
        return f"\n\n### [PDF: {filename}]\n"
    if fl.endswith((".pptx", ".ppt")):
        return f"\n\n### [프레젠테이션: {filename}]\n"
    if fl.endswith((".xlsx", ".xls")):
        return f"\n\n### [스프레드시트: {filename}]\n"
    if fl.endswith((".docx", ".doc")):
        return f"\n\n### [문서: {filename}]\n"
    return f"\n\n### [첨부: {filename}]\n"


def _format_attachment_section(att: dict[str, Any]) -> str | None:
    """Format a single attachment into a markdown section, or None if invalid."""
    extracted_text = att.get("extracted_text", "")

    if not extracted_text or len(extracted_text) < 20:
        return None
    if "오류" in extracted_text or "Error" in extracted_text:
        return None
    if "지원하지 않는" in extracted_text or "unsupported" in extracted_text.lower():
        return None

    filename = att.get("filename", att.get("title", "첨부파일"))
    file_type = att.get("mediaType", att.get("file_type", ""))
    header = _attachment_header(filename, file_type)
    return header + extracted_text


def _merge_attachment_content(page: dict[str, Any], content: str) -> str:
    """첨부파일 extracted_text를 본문에 병합.

    Args:
        page: 페이지 데이터 (attachments 배열 포함)
        content: 기존 본문 텍스트

    Returns:
        첨부파일 텍스트가 병합된 콘텐츠
    """
    attachments = page.get("attachments", [])
    if not attachments:
        return content

    merged_parts = [content] if content else []
    attachment_count = 0

    for att in attachments:
        section = _format_attachment_section(att)
        if section:
            merged_parts.append(section)
            attachment_count += 1

    if attachment_count > 0:
        logger.debug(f"첨부파일 {attachment_count}개 콘텐츠 병합: page_id={page.get('page_id')}")

    return "".join(merged_parts)


def _process_single_document(
    page: dict[str, Any],
    source_name: str,
    min_content_length: int,
    stale_threshold_days: int,
) -> ProcessedDocument | None:
    """단일 문서 처리."""
    page_id = str(page.get("page_id", ""))
    title = page.get("title", "")
    content = page.get("content_text", "") or page.get("content", "")

    # 첨부파일 콘텐츠 병합
    content = _merge_attachment_content(page, content)

    if not content or len(content.strip()) < min_content_length:
        return None

    # 메트릭 계산
    metrics = _calculate_metrics(content)

    # 품질 등급 결정
    quality_tier = _determine_quality_tier(metrics)

    # 최신성 평가
    updated_at = page.get("updated_at", "")
    is_stale, freshness_warning, days_old = _assess_freshness(
        updated_at, stale_threshold_days
    )

    # 최신성 상태 계산 (current/stale/outdated)
    freshness = _calculate_freshness_status(days_old, stale_threshold_days)

    # 첨부파일 포함 여부 확인
    attachments = page.get("attachments", [])
    has_attachments = len(attachments) > 0

    # 소유자 정규화
    creator = page.get("creator", "")
    modifier = page.get("last_modifier", "")
    creators = _normalize_owners([creator])
    modifiers = _normalize_owners([modifier])

    return ProcessedDocument(
        page_id=page_id,
        title=title,
        content_text=content,
        quality_tier=quality_tier,
        metrics=metrics,
        is_stale=is_stale,
        freshness_warning=freshness_warning,
        days_since_update=days_old,
        creators=creators,
        modifiers=modifiers,
        metadata={
            "source": source_name,
            "url": page.get("url", ""),
            "parent_id": page.get("parent_id"),
            "updated_at": updated_at,
            "attachment_count": len(attachments),
        },
        freshness=freshness,
        has_attachments=has_attachments,
    )


def _calculate_metrics(content: str) -> QualityMetrics:
    """콘텐츠 메트릭 계산."""
    return QualityMetrics(
        content_length=len(content),
        has_tables=bool(re.search(r"\|.*\|.*\|", content)),
        has_code_blocks=bool(re.search(r"```|<code>", content)),
        has_headers=bool(
            re.search(r"^#{1,6}\s", content, re.MULTILINE)
            or re.search(r"<h[1-6]>", content)
        ),
        has_images=bool(re.search(r"!\[.*\]\(.*\)|<img", content)),
        has_links=bool(re.search(r"\[.*\]\(.*\)|<a\s+href", content)),
        word_count=len(content.split()),
        paragraph_count=len(re.split(r"\n\n+", content)),
    )


def _determine_quality_tier(metrics: QualityMetrics) -> QualityTier:
    """품질 등급 결정."""
    length = metrics.content_length

    # GOLD: 2000자 이상이거나, 1000자 이상 + 구조화 요소
    if length >= _w.quality.gold_min_chars:
        return QualityTier.GOLD
    if length >= _w.quality.gold_structured_min_chars and (metrics.has_tables or metrics.has_code_blocks):
        return QualityTier.GOLD

    # SILVER: 500자 이상이거나, 구조화 요소 있음
    if length >= _w.quality.silver_min_chars:
        return QualityTier.SILVER
    if length >= _w.quality.silver_structured_min_chars and (metrics.has_tables or metrics.has_code_blocks or metrics.has_headers):
        return QualityTier.SILVER

    # BRONZE: 50자 이상
    if length >= _w.quality.bronze_min_chars:
        return QualityTier.BRONZE

    # NOISE: below bronze threshold
    return QualityTier.NOISE


def _assess_freshness(
    updated_at: str,
    stale_threshold_days: int,
) -> tuple[bool, str | None, int | None]:
    """최신성 평가.

    Returns:
        (is_stale, warning_message, days_since_update)
    """
    if not updated_at:
        return False, None, None

    try:
        if len(updated_at) >= 10:
            update_date = datetime.fromisoformat(updated_at[:10])
            days_old = (datetime.now() - update_date).days

            if days_old > stale_threshold_days:
                years = days_old // 365
                if years >= 1:
                    return True, f"{years}년 이상 미수정 문서", days_old
                else:
                    months = days_old // 30
                    return True, f"{months}개월 이상 미수정", days_old

            elif days_old > 365:
                months = days_old // 30
                return False, f"{months}개월 전 수정", days_old

            return False, None, days_old

    except (ValueError, TypeError) as e:
        logger.debug("Date parsing failed for freshness check: %s", e)

    return False, None, None


def _calculate_freshness_status(
    days_old: int | None,
    stale_threshold_days: int,
) -> str:
    """최신성 상태 계산.

    Args:
        days_old: 마지막 수정 후 경과 일수
        stale_threshold_days: STALE 기준 일수

    Returns:
        freshness status: "current", "stale", "outdated", "unknown"
    """
    if days_old is None:
        return "unknown"

    if days_old <= _w.quality.fresh_max_days:
        return "current"
    elif days_old <= _w.quality.stale_max_days:
        return "stale"
    elif days_old <= stale_threshold_days:
        return "outdated"
    else:
        return "archived"


def _clean_owner_name(raw: str) -> str | None:
    """Clean a single raw owner name, returning None if invalid."""
    name = raw.strip()
    if re.match(r"Unknown User \([^)]+\)", name):
        return None
    if "/" in name:
        name = name.split("/")[0].strip()
    if name.endswith("M") and len(name) > 1:
        name = name[:-1]
    for pattern in SKIP_OWNER_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return None
    if re.match(r"^[가-힣]{2,4}$", name) or re.match(r"^[A-Za-z]{2,20}$", name):
        return name
    return None


def _normalize_owners(raw_names: list[str]) -> list[str]:
    """소유자 이름 정규화."""
    normalized = []
    for raw in raw_names:
        if not raw:
            continue
        name = _clean_owner_name(raw)
        if name:
            normalized.append(name)
    return normalized


def _to_dict(doc: ProcessedDocument) -> dict[str, Any]:
    """ProcessedDocument를 딕셔너리로 변환."""
    # 소스 정보가 없거나 unknown이면 "confluence"로 기본 설정
    source = doc.metadata.get("source", "")
    if not source or source == "unknown":
        source = "confluence"

    return {
        "page_id": doc.page_id,
        "title": doc.title,
        "content_text": doc.content_text,
        "quality_tier": doc.quality_tier.value,
        "content_length": doc.metrics.content_length,
        "has_tables": doc.metrics.has_tables,
        "has_code_blocks": doc.metrics.has_code_blocks,
        "has_headers": doc.metrics.has_headers,
        "has_images": doc.metrics.has_images,
        "word_count": doc.metrics.word_count,
        "paragraph_count": doc.metrics.paragraph_count,
        "is_stale": doc.is_stale,
        "freshness_warning": doc.freshness_warning,
        "days_since_update": doc.days_since_update,
        "creators": doc.creators,
        "modifiers": doc.modifiers,
        "source": source,
        "url": doc.metadata.get("url", ""),
        "updated_at": doc.metadata.get("updated_at", ""),
        "parent_id": doc.metadata.get("parent_id"),
        "freshness": doc.freshness,
        "has_attachments": doc.has_attachments,
        "attachment_count": doc.metadata.get("attachment_count", 0),
        "provenance_id": doc.provenance_id,
        "lineage_id": doc.lineage_id,
    }


def get_quality_summary(stats: dict[str, int]) -> str:
    """품질 통계 요약 문자열 생성."""
    total = stats.get("total", 0)
    useful = stats.get("useful", 0)
    gold = stats.get("gold", 0)
    silver = stats.get("silver", 0)
    bronze = stats.get("bronze", 0)
    stale = stats.get("stale", 0)

    if total == 0:
        return "처리된 문서 없음"

    useful_pct = useful / total * 100
    stale_pct = stale / useful * 100 if useful > 0 else 0

    return (
        f"총 {total}개 중 {useful}개 유용 ({useful_pct:.1f}%)\n"
        f"  GOLD: {gold}개, SILVER: {silver}개, BRONZE: {bronze}개\n"
        f"  오래된 문서: {stale}개 ({stale_pct:.1f}%)"
    )
