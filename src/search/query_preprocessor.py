"""Query Preprocessor.

Purpose:
    Normalize and typo-correct user queries before retrieval/classification.

Features:
    - Whitespace/punctuation normalization.
    - Domain typo map correction (Korean + English operational terms).
    - Optional lightweight fuzzy correction for ASCII tokens.

Usage:
    preprocessor = QueryPreprocessor()
    normalized = preprocessor.preprocess("쿠버네티즈   pod  재시작?")

Examples:
    "쿠버네티즈 pod 재시작" -> "쿠버네티스 pod 재시작"

py.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

_KO_KUBERNETES = "쿠버네티스"

# ---------------------------------------------------------------------------
# Domain synonyms
# ---------------------------------------------------------------------------
DOMAIN_SYNONYMS: dict[str, list[str]] = {
    # Kubernetes
    "k8s": ["kubernetes", _KO_KUBERNETES, "쿠베"],
    "kubernetes": ["k8s", _KO_KUBERNETES, "쿠베"],
    "pod": ["파드", "포드"],
    "deployment": ["디플로이먼트", "배포"],
    "service": ["서비스", "svc"],
    "ingress": ["인그레스"],
    "configmap": ["컨피그맵", "cm"],
    "secret": ["시크릿"],
    "pvc": ["persistent volume claim", "영구볼륨클레임"],
    "hpa": ["horizontal pod autoscaler", "수평 파드 오토스케일러"],
    # Roles
    "담당자": ["담당", "책임자", "관리자", "owner", "오너"],
    "pm": ["프로젝트 매니저", "project manager"],
    "pl": ["프로젝트 리더", "project leader"],
    "devops": ["데브옵스", "dev ops"],
    # Infrastructure
    "aws": ["아마존 웹 서비스", "amazon web services"],
    "ec2": ["이씨투", "ec 2"],
    "s3": ["에스쓰리", "simple storage service"],
    "rds": ["알디에스", "relational database service"],
    "eks": ["elastic kubernetes service"],
    "ecr": ["elastic container registry"],
    # Data
    "dm": ["데이터마트", "data mart", "datamart"],
    "dw": ["데이터웨어하우스", "data warehouse"],
    "etl": ["이티엘", "extract transform load"],
    "bi": ["비아이", "business intelligence"],
    # Systems
    "api": ["에이피아이", "application programming interface"],
    "db": ["데이터베이스", "database"],
    "ci/cd": ["cicd", "지속적 통합 배포"],
    "jenkins": ["젠킨스"],
    "argocd": ["아르고시디", "argo cd"],
    # Domain-specific synonyms — extend this dict for your project's vocabulary.
}


@dataclass(frozen=True, slots=True)
class QueryCorrection:
    """Single token-level query correction."""

    original: str
    corrected: str
    reason: str


@dataclass(frozen=True, slots=True)
class PreprocessedQuery:
    """Normalized query output for retrieval/classification stages."""

    original_query: str
    normalized_query: str
    corrected_query: str
    detected_language: str = "unknown"
    corrections: tuple[QueryCorrection, ...] = ()

    @property
    def was_corrected(self) -> bool:
        return self.corrected_query != self.normalized_query


_HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


# Relative time expressions that can be resolved by rule
_RELATIVE_TIME_KEYWORDS = frozenset({
    "차주", "다음 주", "이번 주", "금주", "지난 주", "전주",
    "이번 달", "이번달", "지난 달", "지난달", "다음 달",
})

# Patterns that suggest time context but need LLM to resolve
_COMPLEX_TIME_RE = re.compile(
    r"지난\s*주\s*[월화수목금토일]요일|"
    r"다음\s*주\s*[월화수목금토일]요일|"
    r"이번\s*주\s*[월화수목금토일]요일|"
    r"어제|그제|그저께|내일|모레|"
    r"최근\s*\d+\s*[일주월년]|"
    r"올해|작년|내년|"
    r"\d+\s*일\s*전|\d+\s*주\s*전|\d+\s*달\s*전"
)

_LLM_TIME_PROMPT = """현재 날짜: {today}

사용자 질문에서 상대적 시점 표현을 찾아 절대 날짜로 변환하세요.
변환할 표현이 없으면 원문 그대로 출력하세요.

질문: {query}

규칙:
- "차주" → 다음 주의 날짜 범위
- "지난 주 월요일" → 구체적 날짜 (YYYY년 M월 D일)
- "최근 3일" → 구체적 날짜 범위
- 변환한 표현만 교체하고 나머지는 그대로 유지
- 설명 없이 변환된 질문만 출력

변환된 질문:"""


def _resolve_relative_time(query: str, llm_client=None) -> tuple[str, list[QueryCorrection]]:
    """Replace relative time expressions with absolute dates.

    Two-tier strategy:
    1. Rule-based: instant replacement for common expressions (0ms)
    2. LLM fallback: complex expressions that rules can't handle (2-3s)

    Examples:
        "차주 업무" → "2026년 4월 2주차 업무" (rule)
        "지난 주 월요일 회의" → "2026년 3월 31일 회의" (LLM)
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone(timedelta(hours=9)))  # KST
    corrections: list[QueryCorrection] = []

    _TIME_MAP = {
        "차주": lambda n: f"{(n + timedelta(weeks=1)).year}년 {(n + timedelta(weeks=1)).month}월 {((n + timedelta(weeks=1)).day - 1) // 7 + 1}주차",  # noqa: E501
        "다음 주": lambda n: f"{(n + timedelta(weeks=1)).year}년 {(n + timedelta(weeks=1)).month}월 {((n + timedelta(weeks=1)).day - 1) // 7 + 1}주차",  # noqa: E501
        "이번 주": lambda n: f"{n.year}년 {n.month}월 {(n.day - 1) // 7 + 1}주차",
        "금주": lambda n: f"{n.year}년 {n.month}월 {(n.day - 1) // 7 + 1}주차",
        "지난 주": lambda n: f"{(n - timedelta(weeks=1)).year}년 {(n - timedelta(weeks=1)).month}월 {((n - timedelta(weeks=1)).day - 1) // 7 + 1}주차",  # noqa: E501
        "전주": lambda n: f"{(n - timedelta(weeks=1)).year}년 {(n - timedelta(weeks=1)).month}월 {((n - timedelta(weeks=1)).day - 1) // 7 + 1}주차",  # noqa: E501
        "이번 달": lambda n: f"{n.year}년 {n.month}월",
        "이번달": lambda n: f"{n.year}년 {n.month}월",
        "지난 달": lambda n: f"{(n.replace(day=1) - timedelta(days=1)).year}년 {(n.replace(day=1) - timedelta(days=1)).month}월",  # noqa: E501
        "지난달": lambda n: f"{(n.replace(day=1) - timedelta(days=1)).year}년 {(n.replace(day=1) - timedelta(days=1)).month}월",  # noqa: E501
        "다음 달": lambda n: f"{(n.replace(day=28) + timedelta(days=4)).year}년 {(n.replace(day=28) + timedelta(days=4)).month}월",  # noqa: E501
    }

    # Tier 1: Rule-based (instant)
    result = query
    rule_matched = False
    for expr, resolver in _TIME_MAP.items():
        if expr in result:
            resolved = resolver(now)
            result = result.replace(expr, resolved)
            corrections.append(QueryCorrection(
                original=expr, corrected=resolved, reason="시점 해석 (규칙)",
            ))
            rule_matched = True

    if rule_matched:
        return result, corrections

    # Tier 2: LLM fallback for complex expressions
    if llm_client and _COMPLEX_TIME_RE.search(query):
        try:
            today_str = now.strftime("%Y년 %m월 %d일 (%A)")
            prompt = _LLM_TIME_PROMPT.format(today=today_str, query=query)
            resolved = llm_client.generate_sync(prompt, max_tokens=200, temperature=0.0)
            if resolved and resolved.strip() != query.strip():
                resolved = resolved.strip()
                corrections.append(QueryCorrection(
                    original=query, corrected=resolved, reason="시점 해석 (LLM)",
                ))
                return resolved, corrections
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("LLM time resolution failed: %s", e)

    return result, corrections


class QueryPreprocessor:
    """Normalize and typo-correct knowledge search queries."""

    _TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\uac00-\ud7a3]+")
    _MULTISPACE_PATTERN = re.compile(r"\s+")
    _SPACE_BEFORE_PUNCT_PATTERN = re.compile(r"\s+([,.:;!?])")

    _DEFAULT_TYPO_MAP: dict[str, str] = {
        # Kubernetes / infra domain typos
        "kuberenetes": "kubernetes",
        "kubernates": "kubernetes",
        "kuberentes": "kubernetes",
        "kubernets": "kubernetes",
        "argocdc": "argocd",
        "argoc": "argocd",
        "confluance": "confluence",
        "conflunce": "confluence",
        "jenkin": "jenkins",
        # Korean domain typos
        "쿠버네티즈": _KO_KUBERNETES,
        "큐버네티스": _KO_KUBERNETES,
        "쿠버네테스": _KO_KUBERNETES,
        "쿠베네티스": _KO_KUBERNETES,
        "파드드": "파드",
        "컨플루언스": "콘플루언스",
        "콘플루언쓰": "콘플루언스",
        "쉐어포인트": "셰어포인트",
        "셰어포인": "셰어포인트",
    }

    def __init__(
        self,
        *,
        typo_map: dict[str, str] | None = None,
        fuzzy_enabled: bool | None = None,
        fuzzy_cutoff: float | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self._llm_client = llm_client
        source_typo_map = self._DEFAULT_TYPO_MAP if typo_map is None else typo_map
        self._typo_map = {
            key.lower(): value
            for key, value in source_typo_map.items()
            if key and value
        }

        if fuzzy_enabled is None:
            env_val = os.getenv("KNOWLEDGE_QUERY_PREPROCESS_FUZZY_ENABLED")
            fuzzy_enabled = (
                env_val.lower() == "true" if env_val is not None
                else _w.preprocessor.fuzzy_enabled
            )
        self._fuzzy_enabled = fuzzy_enabled

        if fuzzy_cutoff is None:
            fuzzy_cutoff = self._read_fuzzy_cutoff_env()
        self._fuzzy_cutoff = max(0.0, min(float(fuzzy_cutoff), 1.0))
        self._fuzzy_vocabulary = self._build_fuzzy_vocabulary()

    def preprocess(self, query: str) -> PreprocessedQuery:
        """Normalize and typo-correct a user query."""
        original_query = str(query or "")
        normalized_query = self._normalize_query(original_query)
        if not normalized_query:
            return PreprocessedQuery(
                original_query=original_query,
                normalized_query=normalized_query,
                corrected_query=normalized_query,
                detected_language="unknown",
            )

        # Resolve relative time expressions first (rule → LLM fallback)
        time_resolved, time_corrections = _resolve_relative_time(
            normalized_query, llm_client=self._llm_client
        )
        if time_resolved != normalized_query:
            normalized_query = time_resolved

        corrected_query, corrections = self._apply_token_corrections(normalized_query)
        corrections = list(time_corrections) + corrections
        if corrections:
            logger.info(
                "Knowledge query preprocessed with corrections",
                extra={
                    "original_query_preview": original_query[:120],
                    "normalized_query_preview": normalized_query[:120],
                    "corrected_query_preview": corrected_query[:120],
                    "correction_count": len(corrections),
                },
            )

        return PreprocessedQuery(
            original_query=original_query,
            normalized_query=normalized_query,
            corrected_query=corrected_query,
            detected_language=self._detect_language(corrected_query),
            corrections=tuple(corrections),
        )

    def _normalize_query(self, query: str) -> str:
        text = self._MULTISPACE_PATTERN.sub(" ", str(query or "").strip())
        text = self._SPACE_BEFORE_PUNCT_PATTERN.sub(r"\1", text)
        return text

    def _apply_token_corrections(
        self, normalized_query: str
    ) -> tuple[str, list[QueryCorrection]]:
        parts: list[str] = []
        corrections: list[QueryCorrection] = []
        last_index = 0

        for match in self._TOKEN_PATTERN.finditer(normalized_query):
            parts.append(normalized_query[last_index : match.start()])
            token = match.group(0)
            corrected_token, reason = self._correct_token(token)
            parts.append(corrected_token)
            if corrected_token != token and reason:
                corrections.append(
                    QueryCorrection(
                        original=token,
                        corrected=corrected_token,
                        reason=reason,
                    )
                )
            last_index = match.end()

        parts.append(normalized_query[last_index:])
        return "".join(parts), corrections

    def _correct_token(self, token: str) -> tuple[str, str | None]:
        lowered = token.lower()
        if lowered in self._typo_map:
            return self._typo_map[lowered], "typo_map"

        if not self._fuzzy_enabled:
            return token, None

        # Fuzzy correction is intentionally limited to ASCII words to avoid
        # aggressive Korean token rewrites.
        if not token.isascii() or not token.isalnum() or len(token) < _w.preprocessor.fuzzy_min_token_length:
            return token, None

        matches = difflib.get_close_matches(
            lowered,
            self._fuzzy_vocabulary,
            n=1,
            cutoff=self._fuzzy_cutoff,
        )
        if not matches:
            return token, None

        candidate = matches[0]
        if candidate == lowered:
            return token, None
        return candidate, "fuzzy"

    def _build_fuzzy_vocabulary(self) -> tuple[str, ...]:
        candidates: set[str] = set()
        candidates.update(self._typo_map.values())
        for term, synonyms in DOMAIN_SYNONYMS.items():
            candidates.add(term)
            for synonym in synonyms:
                candidates.add(str(synonym))
        # Fuzzy stage only handles ASCII tokens.
        return tuple(
            sorted(
                item.lower()
                for item in candidates
                if item and item.isascii() and item.replace("/", "").isalnum()
            )
        )

    @staticmethod
    def _read_fuzzy_cutoff_env() -> float:
        raw = os.getenv("KNOWLEDGE_QUERY_PREPROCESS_FUZZY_CUTOFF")
        if raw is None:
            return _w.preprocessor.fuzzy_cutoff
        try:
            parsed = float(raw)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return _w.preprocessor.fuzzy_cutoff
        return max(0.0, min(parsed, 1.0))

    @staticmethod
    def _detect_language(text: str) -> str:
        """Best-effort language detection for Korean/English/mixed queries."""
        content = str(text or "").strip()
        if not content:
            return "unknown"

        hangul_count = len(_HANGUL_PATTERN.findall(content))
        latin_count = len(_LATIN_PATTERN.findall(content))
        alpha_total = hangul_count + latin_count
        if alpha_total <= 0:
            return "other"

        if hangul_count > 0 and latin_count > 0:
            hangul_ratio = hangul_count / alpha_total
            if hangul_ratio >= 0.7:
                return "ko"
            if hangul_ratio <= 0.3:
                return "en"
            return "mixed"
        if hangul_count > 0:
            return "ko"
        return "en"


class NoOpQueryPreprocessor:
    """No-op query preprocessor for tests and controlled fallback usage."""

    def preprocess(self, query: str) -> PreprocessedQuery:
        normalized_query = str(query or "").strip()
        return PreprocessedQuery(
            original_query=str(query or ""),
            normalized_query=normalized_query,
            corrected_query=normalized_query,
            detected_language=QueryPreprocessor._detect_language(normalized_query),
            corrections=(),
        )


__all__ = [
    "DOMAIN_SYNONYMS",
    "NoOpQueryPreprocessor",
    "PreprocessedQuery",
    "QueryCorrection",
    "QueryPreprocessor",
]
