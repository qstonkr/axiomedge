"""Term extraction patterns, noise filters, and particle stripping.

Consolidated NLP pattern definitions and noise detection rules
for glossary term extraction from document chunks.

Extracted from term_extractor.py for single-responsibility.
"""

from __future__ import annotations

import re

# ==========================================================================
# NLP Patterns
# ==========================================================================

CAMEL_CASE_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")
ACRONYM_PATTERN = re.compile(r"\b([A-Z]{2,6})\b")
HYPHENATED_PATTERN = re.compile(r"\b([a-z]+(?:-[a-z]+)+)\b", re.IGNORECASE)
KOREAN_TECH_PATTERN = re.compile(
    r"[\uac00-\ud7a3]{2,8}(?:베이스|시스템|서비스|검색|처리|관리)"
)
MIXED_PATTERN = re.compile(r"[A-Za-z0-9]+[\uac00-\ud7a3]{2,}")

# Pre-compiled whitespace normalization
WHITESPACE_RE = re.compile(r"\s+")


# ==========================================================================
# Noise Filters
# ==========================================================================

_CSS_PREFIXES = frozenset({
    "border", "margin", "padding", "font", "text", "background", "display",
    "flex", "grid", "align", "justify", "overflow", "position", "transform",
    "transition", "animation", "opacity", "visibility", "cursor", "outline",
    "box", "line", "letter", "word", "white", "list", "table", "vertical",
    "max", "min", "content", "counter", "resize", "user", "object",
    "pointer", "scroll", "clip", "filter", "mix", "backdrop", "isolation",
    "break", "page", "column", "gap", "place", "prefers",
})

_CODE_NOISE_RE = re.compile(
    r"(?:"
    r"^[d\-][rwx\-]{8,}$"
    r"|Exception$"
    r"|Error$"
    r"|^x-www-"
    r"|^[yYMmdDHhSs]{2,}[-/.][yYMmdDHhSs]"
    r"|^application[-/]"
    r"|^content[-/]"
    r"|^text[-/]"
    r")",
    re.IGNORECASE,
)

_KOREAN_PARTICLES_LONG = [
    "에서", "으로", "까지", "부터", "처럼", "같이", "에게", "한테", "보다"
]
_KOREAN_PARTICLES_SHORT = [
    "가", "를", "에", "의", "는", "은", "도", "와", "과", "이", "로", "만", "서"
]

_BOUNDARY_PARTICLE_RE = re.compile(
    r"^([A-Za-z0-9]+)"
    r"([가를에의는은도와과이])"
    r"([\uac00-\ud7a3]{2,})"
)

_COMPOUND_STOP = frozenset({
    "완료", "요청", "제거", "실행", "중지", "생성", "수정", "삭제",
    "추가", "변경", "개선", "적용", "배포", "진행", "검토", "승인",
    "반려", "취소", "종료", "시작", "등록", "해제", "연동", "전환",
    "이관", "이전", "복구", "백업", "설치", "제공", "구현", "개발",
    "테스트", "점검", "확인", "보고", "안내", "공유", "전달",
    "관련", "대상", "목록", "현황", "이력", "결과", "예정",
    "필요", "가능", "불가", "여부", "문의", "답변",
})

KNOWN_ACRONYMS = frozenset({
    "API", "KB", "RAG", "LLM", "OFC", "DW", "FAQ", "GS", "CU",
    "POS", "ERP", "CRM", "HR", "IT", "AI", "ML", "DB", "SQL",
    "VM", "VPN", "SSL", "DNS", "CDN", "AWS", "GCP", "K8S",
    "CI", "CD", "QA", "UAT", "SLA", "KPI", "ROI", "MOU",
    "B2B", "B2C", "DM", "PM", "SM", "BI", "ETL", "RPA",
})

ENGLISH_STOP = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "have", "has",
    "are", "was", "were", "been", "will", "would", "could", "should",
    "not", "but", "can", "all", "any", "some", "more", "most", "other",
    "new", "old", "first", "last", "long", "great", "own", "same",
    "home", "page", "status", "type", "name", "data", "info", "list",
    "item", "value", "null", "true", "false", "none", "file", "test",
    "image", "text", "http", "https", "html", "json", "xml", "css",
    "div", "span", "class", "style", "width", "height", "color",
    "size", "title", "date", "time", "user", "admin", "server",
    "error", "success", "result", "total", "count", "index", "table",
})

STOP_TERMS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "have", "has",
    "are", "was", "were", "been", "being", "will", "would", "could", "should",
    "not", "but", "can", "all", "any", "some", "more", "most", "other",
    "new", "old", "first", "last", "long", "great", "little", "own", "same",
    "있다", "하다", "되다", "이다", "없다", "않다", "같다", "보다", "위해",
    "통해", "대해", "따라", "경우", "때문", "이후", "이전", "현재", "내용",
})


# ==========================================================================
# Filter Functions
# ==========================================================================


def is_noise_term(
    term: str, tag_type: str, kiwi_score: float = -999.0,
    stop_nouns: frozenset | None = None,
    kiwi_threshold: float = -12.0,
) -> bool:
    """Comprehensive noise filter for extracted terms."""
    if len(term) < 2:
        return True
    if len(set(term)) <= 1:
        return True
    if term[0] in "#*@\\/$`~^:=+" or term[-1] in "#*@\\/$`~^":
        return True
    if "." in term:
        return True
    if any(c in term for c in "/,_"):
        return True
    if term[0].isdigit():
        return True
    stripped = term.strip(".-+%,")
    if stripped.isdigit():
        return True
    if any(c in term for c in "{}()[]\"'=<>;|"):
        return True
    alnum_count = sum(1 for c in term if c.isalnum() or "\uac00" <= c <= "\ud7a3")
    if len(term) > 3 and alnum_count / len(term) < 0.7:
        return True
    if term.isascii():
        if term.isalpha() and len(term) <= 10:
            return True
        if term[0].islower() and any(c.isupper() for c in term[1:]):
            return True
        if term.isalnum() and not any("\uac00" <= c <= "\ud7a3" for c in term):
            return True
    if tag_type == "foreign":
        if len(term) < 3:
            return True
        if len(term) <= 4 and term.islower():
            return True
        if term.lower() in ENGLISH_STOP:
            return True
        if len(term) <= 3 and term.isupper() and term not in KNOWN_ACRONYMS:
            return True
    if tag_type == "compound_noun" and term.isascii() and term.isalpha():
        return True
    if stop_nouns and term in stop_nouns:
        return True
    if is_code_artifact(term):
        return True
    if (
        kiwi_score > kiwi_threshold
        and kiwi_score != -999.0
        and tag_type in ("noun", "compound_noun")
        and all("\uac00" <= c <= "\ud7a3" for c in term)
    ):
        return True
    return False


def is_synonym_noise(term: str) -> bool:
    """Check if a term is OCR/code noise for synonym discovery."""
    if not term:
        return True
    if term[0] in "#*@\\/$`~^|{}<>":
        return True
    if term.strip(".-+%,").isdigit():
        return True
    if len(term) > 3:
        alnum = sum(1 for c in term if c.isalnum() or "\uac00" <= c <= "\ud7a3")
        if alnum / len(term) < 0.6:
            return True
    return False


def is_code_artifact(term: str) -> bool:
    """Check if term is a code/CSS/technical noise artifact."""
    if "-" in term:
        first_part = term.split("-")[0].lower()
        if first_part in _CSS_PREFIXES:
            return True
    return bool(_CODE_NOISE_RE.search(term))


def strip_korean_particles(term: str) -> str:
    """Strip Korean particles (조사) from mixed Korean-English terms."""
    changed = True
    while changed:
        changed = False
        for p in _KOREAN_PARTICLES_LONG:
            if term.endswith(p) and len(term) > len(p) + 2:
                term = term[: -len(p)]
                changed = True
                break
        if not changed:
            for p in _KOREAN_PARTICLES_SHORT:
                if term.endswith(p) and len(term) > len(p) + 2:
                    term = term[: -len(p)]
                    changed = True
                    break

    m = _BOUNDARY_PARTICLE_RE.match(term)
    if m:
        term = m.group(1) + m.group(3)

    return term
