"""Ingestion helper functions — document classification, owner extraction, quality scoring.

Independent utility functions used by IngestionPipeline.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from .quality_processor import QualityTier, QualityMetrics, _normalize_owners

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document type classification (META-03)
# ---------------------------------------------------------------------------

_DOC_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("guide", ("가이드", "guide", "매뉴얼", "manual")),
    ("policy", ("정책", "policy", "규정")),
    ("procedure", ("절차", "procedure", "프로세스")),
    ("faq", ("faq", "자주", "질문")),
    ("meeting_notes", ("회의", "meeting", "미팅")),
    ("changelog", ("변경", "changelog", "릴리스")),
)

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg",
})


# ---------------------------------------------------------------------------
# Owner extraction (META-06)
# ---------------------------------------------------------------------------


def extract_owner(raw: Any) -> str:
    """Extract document owner from metadata (author, creator, last_modifier)."""
    candidates = [
        raw.author or "",
        raw.metadata.get("creator", ""),
        raw.metadata.get("last_modifier", ""),
    ]

    if raw.title:
        name_match = re.search(r"\d[_-]\d[^_]*_([가-힣]{2,4})M?_", raw.title)
        if name_match:
            candidates.append(name_match.group(1))

    normalized = _normalize_owners(candidates)
    return normalized[0] if normalized else ""


# ---------------------------------------------------------------------------
# L1 category assignment (META-07)
# ---------------------------------------------------------------------------

_L1_CATEGORIES_CACHE: list[dict[str, Any]] | None = None

_L1_CATEGORIES_DEFAULT: list[dict[str, Any]] = [
    {"name": "IT인프라·운영", "keywords": ["서버", "배포", "장애", "모니터링", "인프라", "K8s", "쿠버네티스", "도커", "네트워크", "방화벽", "SSL", "DNS", "CDN"]},
    {"name": "시스템·애플리케이션", "keywords": ["시스템", "메뉴", "화면", "API", "POS", "WMS", "ERP", "앱", "UI", "UX", "프론트", "백엔드", "DB", "데이터베이스"]},
    {"name": "업무프로세스·규정", "keywords": ["프로세스", "절차", "규정", "감사", "인사", "회계", "재무", "결재", "양수도", "폐점", "계약", "정산", "담배권"]},
    {"name": "사업·전략", "keywords": ["매출", "전략", "KPI", "마케팅", "영업", "홈쇼핑", "실적", "성과", "사업분석", "상권분석", "경영전략"]},
    {"name": "유통·물류", "keywords": ["OFC", "발주", "배송", "재고", "물류", "점포", "가맹", "상품", "유통", "식품", "경영주", "편의점", "GS25", "진열", "경영", "상권", "ESPA"]},
    {"name": "용어·지식정의", "keywords": ["용어", "정의", "약어", "표준", "사전", "데이터 표준"]},
    {"name": "기타", "keywords": []},
]


def load_l1_categories_from_db(categories: list[dict[str, Any]]) -> None:
    """Load L1 categories from DB into cache. Called during app startup."""
    global _L1_CATEGORIES_CACHE
    if categories:
        _L1_CATEGORIES_CACHE = categories
        logger.info("L1 categories loaded from DB: %d categories", len(categories))


def _get_l1_categories_sync() -> list[dict[str, Any]]:
    """Get L1 categories (cached). Falls back to hardcoded defaults."""
    if _L1_CATEGORIES_CACHE is not None:
        return _L1_CATEGORIES_CACHE
    return _L1_CATEGORIES_DEFAULT


def classify_l1_category(title: str, content: str) -> str:
    """Assign L1 category based on keyword matching."""
    categories = _get_l1_categories_sync()
    title_lower = title.lower() if title else ""
    content_lower = content[:2000].lower() if content else ""

    best_name = "기타"
    best_score = 0

    for cat in categories:
        if not cat["keywords"]:
            continue
        score = 0
        for kw in cat["keywords"]:
            kw_lower = kw.lower()
            if kw_lower in title_lower:
                score += 3
            if kw_lower in content_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_name = cat["name"]

    return best_name


# ---------------------------------------------------------------------------
# Quality score (numeric, 0-100) (META-08)
# ---------------------------------------------------------------------------


def calculate_quality_score(metrics: QualityMetrics | None, tier: QualityTier) -> float:
    """Calculate numeric quality score (0-100) from quality metrics."""
    if metrics is None:
        return 50.0

    length = metrics.content_length if hasattr(metrics, "content_length") else 0
    base = min(60.0, math.log1p(length) / math.log1p(5000) * 60) if length > 0 else 0

    structure = 0.0
    for attr in ("has_tables", "has_code_blocks", "has_headers", "has_images", "has_links"):
        if getattr(metrics, attr, False):
            structure += 8.0

    tier_bonus = {"GOLD": 15.0, "SILVER": 10.0, "BRONZE": 5.0}.get(tier.value, 0.0)

    return min(100.0, round(base + structure + tier_bonus, 1))


def classify_document_type(title: str, content: str) -> str:
    """Rule-based document type classifier."""
    title_lower = title.lower()
    for doc_type, keywords in _DOC_TYPE_KEYWORDS:
        if any(k in title_lower for k in keywords):
            return doc_type
    return "reference"


# ---------------------------------------------------------------------------
# Cross-reference extraction (GRAPH-01)
# ---------------------------------------------------------------------------

_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def extract_cross_references(content: str) -> list[tuple[str, str]]:
    """Extract wiki/internal links from document content."""
    refs: list[tuple[str, str]] = []
    for link_text, link_url in _LINK_PATTERN.findall(content):
        if link_url.startswith("/") or "confluence" in link_url or "wiki" in link_url:
            refs.append((link_text, link_url))
    return refs
